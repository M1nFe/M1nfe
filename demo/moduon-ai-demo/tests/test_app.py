"""실행 프로그램(모두온 AI 콘솔) 테스트 — 서비스 계층과 웹 서버. API 키·Ollama 없이 돈다."""
import base64
import http.client
import json
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from moduon_demo.app.server import App, make_handler
from moduon_demo.app.service import Session, UserError

ROOT = Path(__file__).resolve().parent.parent
NOOP = lambda *_: None   # noqa: E731


def _full_flow(s: Session) -> Session:
    """모의 모드: 자동 수집 → 매핑 승인 → 매칭 확정 → 검수 → 확정 반영."""
    s.process_all(NOOP)
    m = s.source_detail("a_excel")["mapping"]
    s.approve_mapping("a_excel", m["columns"], m["header_rows"], NOOP)
    for x in s.matches():
        s.confirm_match(x["id"], x["suggestion"]["product_id"] or "NEW", NOOP)
    s.approve_safe()
    g = s.review()["groups"]
    s.decide(g["blocked"][0]["id"], "edit", "35개")
    s.approve_group("needs")
    s.promote()
    return s


def test_session_full_flow_mock():
    s = Session(ROOT, mode="mock")
    assert s.state()["todo"] == {"mapping": 0, "matching": 0, "review": 0, "to_promote": 0}
    s.process_all(NOOP)
    st = s.state()
    assert st["todo"]["mapping"] == 1 and st["todo"]["matching"] == 5          # 엑셀은 매핑 승인 전까지 값이 없다
    assert {x["id"]: x["status"] for x in st["sources"]}["a_excel"] == "매핑 승인 대기"
    _ = s                                                                     # 이어서 끝까지
    m = s.source_detail("a_excel")["mapping"]
    assert all(c["passed"] for c in m["columns"]) and m["header_rows"] == [4, 5]
    s.approve_mapping("a_excel", m["columns"], m["header_rows"], NOOP)
    assert len(s.matches()) == 8
    for x in s.matches():
        s.confirm_match(x["id"], x["suggestion"]["product_id"] or "NEW", NOOP)
    g = s.review()["groups"]
    assert len(g["blocked"]) == 1 and len(g["needs"]) == 14 and len(g["safe"]) == 84
    with pytest.raises(UserError, match="차단"):
        s.decide(g["blocked"][0]["id"], "approve")
    with pytest.raises(UserError):
        s.approve_group("blocked")
    s.approve_safe()
    s.decide(g["blocked"][0]["id"], "edit", "35개")
    s.approve_group("needs")
    out = s.promote()
    assert out["promoted"] == 99
    html = s.screen_html()
    assert "60개" in html and "▲5" in html and "아직" not in html
    rows = s.nlq("지난달보다 리베이트가 오른 통신 단말 알려줘", NOOP)["table"]["rows"]
    assert len(rows) == 4
    assert s.store.one("select count(*) n from canonical_price where month='2026-10' and approved_by='사용자'")["n"] == 99


def test_screen_before_confirmation_shows_last_month():
    s = Session(ROOT, mode="mock")
    html = s.screen_html()
    assert "아직 2026-10 값을 확정하지 않아" in html and "9월 값" in html


def test_mock_mode_refuses_uploads_and_free_questions():
    s = Session(ROOT, mode="mock")
    with pytest.raises(UserError, match="모의 모드"):
        s.add_upload("kakao", "B통신", "x.txt", "갤S24 256 : 플래티넘 60개".encode())
    with pytest.raises(UserError, match="예시 질문"):
        s.nlq("아무 질문", NOOP)
    with pytest.raises(UserError):
        s.promote()                                   # 승인된 값이 없으면 반영할 것도 없다


def test_undo_and_manual_entry():
    s = _full_flow(Session(ROOT, mode="mock"))
    r = s.add_manual("B통신", "P003", "B110", "rebate", "join=mnp", "48개")
    assert r["value"] == 480000
    s.promote()
    v = s.store.one("select value_int, source from canonical_price where partner='B통신' and product_id='P003' "
                    "and plan_id='B110' and field_code='rebate' and month='2026-10'")
    assert v["value_int"] == 480000 and "사람 직접 입력" in v["source"]
    with pytest.raises(UserError):
        s.add_manual("B통신", "P003", "A115", "rebate", "join=mnp", "10개")   # 다른 통신사 요금제


# ───────────────────────── 웹 서버
@pytest.fixture
def server():
    app = App(ROOT, prefer="mock")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, 0))
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port
    httpd.shutdown()


def _req(port, method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = {"Host": host or f"127.0.0.1:{port}", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.update({"Content-Type": "application/json", "X-Moduon": "1"} if "X-Moduon" not in (headers or {}) else {})
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    return r.status, (json.loads(raw) if "json" in (r.getheader("Content-Type") or "") else raw.decode())


def _wait(port, jid):
    for _ in range(200):
        code, j = _req(port, "GET", f"/api/job/{jid}")
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("작업이 끝나지 않음")


def test_server_pages_and_protection(server):
    code, html = _req(server, "GET", "/")
    assert code == 200 and "모두온 AI 콘솔" in html
    assert _req(server, "GET", "/static/app.js")[0] == 200
    assert _req(server, "GET", "/static/../service.py")[0] == 404
    assert _req(server, "GET", "/api/state", host="evil.example")[0] == 403           # DNS 리바인딩 방지
    code, _ = _req(server, "POST", "/api/process_all", {}, headers={"X-Moduon": "0"})
    assert code == 403                                                                  # CSRF 방지
    code, st = _req(server, "GET", "/api/state")
    assert code == 200 and st["engine"]["mode"] == "mock" and len(st["sources"]) == 6


def test_server_job_flow(server):
    code, r = _req(server, "POST", "/api/process_all", {})
    assert code == 200
    j = _wait(server, r["job_id"])
    assert j["status"] == "done" and len(j["result"]["processed"]) == 6
    code, again = _req(server, "POST", "/api/process/b_kakao", {})
    assert code == 200 and _wait(server, again["job_id"])["status"] == "error"         # 같은 자료 두 번 처리 금지
    code, d = _req(server, "GET", "/api/source/a_excel")
    m = d["mapping"]
    code, r = _req(server, "POST", "/api/mapping/a_excel", {"columns": m["columns"], "header_rows": m["header_rows"]})
    assert _wait(server, r["job_id"])["status"] == "done"
    code, r = _req(server, "POST", "/api/matches/confirm_all", {})
    assert _wait(server, r["job_id"])["result"]["confirmed"] == 8
    code, rv = _req(server, "GET", "/api/review")
    blocked = rv["groups"]["blocked"][0]["id"]
    assert _req(server, "POST", f"/api/review/{blocked}", {"action": "approve"})[0] == 400
    assert _req(server, "POST", f"/api/review/{blocked}", {"action": "reject"})[0] == 200
    assert _req(server, "POST", "/api/review/approve_safe", {})[1]["approved"] == 84
    assert _req(server, "POST", "/api/review/approve_group", {"group": "needs"})[1]["approved"] == 14
    code, p = _req(server, "POST", "/api/promote", {})
    assert code == 200 and p["promoted"] == 98
    code, html = _req(server, "GET", "/screen")
    assert code == 200 and "휴대폰 요금 설계" in html and "9월 값" in html         # 제외한 칸은 지난달 값
    code, h = _req(server, "GET", "/api/history")
    assert len(h["calls"]) >= 10 and h["audit"][0]["action"] == "promote"


def test_server_upload_in_mock_is_refused(server):
    b64 = base64.b64encode("갤S24 256 : 플래티넘 60개".encode()).decode()
    code, r = _req(server, "POST", "/api/upload", {"kind": "kakao", "partner": "B통신", "filename": "a.txt", "data_b64": b64})
    assert code == 400 and "모의 모드" in r["error"]


def test_live_ollama_paste_kakao_detects_mismatch():
    """Ollama 경로(가짜 서버): 카톡 공지를 붙여넣되 한 값을 고쳐 두면(55개 → 60개),
    AI 응답(가짜 서버는 원래 샘플 응답을 돌려줌)과 원문이 달라 코드 검증이 잡아낸다."""
    from test_ollama_path import FakeOllama
    fake = FakeOllama()
    try:
        s = Session(ROOT, provider="ollama", mode="live", base_url=fake.url)
        text = (ROOT / "data/b_telecom_kakao_2610.txt").read_text(encoding="utf-8").replace("플래티넘 55개", "플래티넘 60개", 1)
        sid = s.add_upload("kakao", "B통신", "붙여넣은 카톡 공지.txt", text.encode())
        s.process(sid, NOOP)
        low = s.store.query("select value_text, checks from staging_record where extractor='ai' and grade='low'")
        # 고친 줄에서 나온 값 3개는 AI가 적은 근거 줄이 원문에 없어 모두 '검증 실패'가 된다
        assert sorted(r["value_text"] for r in low) == ["28개", "42개", "55개"]
        assert all(json.loads(r["checks"])["원문 줄 대조"] is False for r in low)
        assert s.history and s.history[0]["provider"] == "ollama" and s.history[0]["step_id"] == "k2_kakao_parse"
        assert len(s.matches()) == 2 and all(m["suggestion"] for m in s.matches())   # 상품명 AI 판정도 로컬 모델로
        assert s.llm.record is False           # 실행 프로그램은 발표용 녹화본을 덮어쓰지 않는다
    finally:
        fake.close()
