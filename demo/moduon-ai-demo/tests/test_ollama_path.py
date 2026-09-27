"""Ollama(로컬 오픈소스 모델) 경로 테스트.

실제 Ollama 대신 같은 API(/api/tags, /api/chat)를 흉내 내는 가짜 서버를 localhost에 띄워 실제 HTTP로 검증한다.
가짜 서버가 돌려주는 답은 mock_responses/ 의 모의 응답이다(실제 모델 결과 아님).
"""
import io
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from moduon_demo.ai import extract_doc, nlq_parse
from moduon_demo.console import Reporter
from moduon_demo.llm import LLM, AIError

ROOT = Path(__file__).resolve().parent.parent
MOCK = {p.stem: json.loads(p.read_text(encoding="utf-8"))["output"] for p in (ROOT / "mock_responses").glob("*.json")}
NLQ = {"렌탈료가 오른": "q1_nlq", "30만원 이상": "q2_nlq", "정산금": "q3_nlq"}


def _pick_mock(body) -> dict:
    sys_text, user = body["messages"][0]["content"], body["messages"][1]["content"]
    if "상품명 매칭" in sys_text:              # 로컬 모델은 상품명 하나씩 묻는다 → 그 상품의 답만 돌려준다
        keys = set(re.findall(r"\[(m\d\d)\]", user))
        return {"results": [r for r in MOCK["m4_match_judge"]["results"] if r["mention_key"] in keys]}
    for key, step in [("헤더 매핑", "e2_header_map"), ("문서 추출", "e3_pdf_extract"), ("상품명 매칭", "m4_match_judge"),
                      ("이상 데이터 설명", "a3_anomaly_explain"), ("공지·메일", "n1_notice_parse")]:
        if key in sys_text:
            return MOCK[step]
    return MOCK[next(v for q, v in NLQ.items() if q in user)]


class FakeOllama:
    """replies: None이면 모의 응답 자동 선택, 리스트면 순서대로 content 문자열(또는 dict 전체 응답)을 돌려준다."""

    def __init__(self, models=("qwen2.5:7b",), replies=None):
        self.models, self.replies, self.requests = list(models), replies, []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send(200, {"models": [{"name": m} for m in outer.models]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                if body["model"] not in outer.models:
                    return self._send(404, {"error": f"model '{body['model']}' not found"})
                if outer.replies is None:
                    content, extra = json.dumps(_pick_mock(body), ensure_ascii=False), {}
                else:
                    r = outer.replies.pop(0)
                    content, extra = (r, {}) if isinstance(r, str) else (r.get("content", ""), r)
                self._send(200, {"model": body["model"], "message": {"role": "assistant", "content": content},
                                 "done": True, "done_reason": extra.get("done_reason", "stop"),
                                 "prompt_eval_count": 500, "eval_count": 120})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def fake():
    servers = []

    def make(**kw):
        s = FakeOllama(**kw)
        servers.append(s)
        return s
    yield make
    for s in servers:
        s.close()


def _llm(tmp_path, url, **kw):
    return LLM("live", tmp_path, Reporter(file=io.StringIO()), provider="ollama", base_url=url, **kw)


def test_request_shape_and_recording(tmp_path, fake):
    srv = fake()
    out = nlq_parse.run(_llm(tmp_path, srv.url), "q2_nlq", "A통신 번호이동 공시지원금이 30만원 이상", "2026-10", "2026-09")
    assert out == MOCK["q2_nlq"]
    body = srv.requests[0]
    assert body["model"] == "qwen2.5:7b" and body["stream"] is False
    assert body["format"]["required"] == ["intent", "params", "unsupported_reason"]   # JSON Schema로 형식 강제
    assert body["options"]["temperature"] == 0 and body["options"]["num_ctx"] >= 8192
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    rec = json.loads((tmp_path / "recordings" / "ollama__qwen2.5_7b" / "q2_nlq.json").read_text(encoding="utf-8"))
    assert rec["provider"] == "ollama" and rec["cost_usd"] == 0.0
    assert rec["usage"] == {"input_tokens": 500, "output_tokens": 120}


def test_pdf_is_sent_as_extracted_text(tmp_path, fake):
    srv = fake()
    extract_doc.run(_llm(tmp_path, srv.url), ROOT / "data/c_rental_price_2610.pdf", "c_rental_price_2610.pdf")
    user = srv.requests[0]["messages"][1]["content"]
    assert "[페이지 1]" in user and "퓨어워터 WP500 냉온 36,900원 60개월 100,000원" in user
    assert "JVBER" not in user        # PDF 원본(base64)은 보내지 않는다


def test_missing_model_message(tmp_path, fake):
    srv = fake(models=["llama3.2:3b"])
    with pytest.raises(AIError, match="ollama pull qwen2.5:7b"):
        _llm(tmp_path, srv.url)


def test_server_down_message(tmp_path):
    with pytest.raises(AIError, match="ollama serve"):
        _llm(tmp_path, "http://127.0.0.1:9")


def test_retry_once_on_bad_format(tmp_path, fake):
    good = json.dumps(MOCK["q2_nlq"], ensure_ascii=False)
    srv = fake(replies=['{"intent": "price_lookup"}', good])      # 첫 답은 필수 항목 누락
    out = nlq_parse.run(_llm(tmp_path, srv.url), "q2_nlq", "질문", "2026-10", "2026-09")
    assert out == MOCK["q2_nlq"] and len(srv.requests) == 2
    assert "형식 오류" in srv.requests[1]["messages"][-1]["content"]


def test_gives_up_after_second_bad_format(tmp_path, fake):
    srv = fake(replies=["not json", "still not json"])
    with pytest.raises(AIError, match="형식"):
        nlq_parse.run(_llm(tmp_path, srv.url), "q2_nlq", "질문", "2026-10", "2026-09")


def test_think_tags_are_stripped(tmp_path, fake):
    srv = fake(replies=["<think>생각 중…</think>\n" + json.dumps(MOCK["q2_nlq"], ensure_ascii=False)])
    assert nlq_parse.run(_llm(tmp_path, srv.url), "q2_nlq", "질문", "2026-10", "2026-09") == MOCK["q2_nlq"]


def test_truncation_is_an_error(tmp_path, fake):
    srv = fake(replies=[{"content": "{", "done_reason": "length"}])
    with pytest.raises(AIError, match="잘렸"):
        nlq_parse.run(_llm(tmp_path, srv.url), "q2_nlq", "질문", "2026-10", "2026-09")


def test_full_demo_through_fake_ollama(tmp_path, fake, monkeypatch):
    """시연 전체를 Ollama 경로로 끝까지 실행(가짜 서버). 녹화본과 요약 JSON이 만들어지는지 확인."""
    import run_demo
    srv = fake()
    monkeypatch.setattr(run_demo, "ROOT", ROOT)
    monkeypatch.setattr("moduon_demo.llm.LLM._record", lambda *a, **k: None)   # 저장소에 가짜 녹화본을 남기지 않음
    ctx, report, summary = run_demo.run("live", provider="ollama", base_url=srv.url, quiet=True)
    s = json.loads(summary.read_text(encoding="utf-8"))
    assert s["completed"] and s["provider"] == "ollama" and s["model"] == "qwen2.5:7b"
    # 8단계 중 매칭은 상품명 6개를 하나씩 물어서 호출은 모두 13번
    assert s["totals"]["calls"] == 13 and s["totals"]["cost_usd"] == 0
    assert "Ollama 로컬 qwen2.5:7b" in s["mode_label"] and s["warnings"] == []
    assert len(srv.requests) == 13
    assert s["accuracy"]["② 상품명 매칭"] == {"ok": 6, "total": 6}
    report.unlink()
    summary.unlink()
