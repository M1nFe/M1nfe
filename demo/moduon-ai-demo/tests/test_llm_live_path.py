"""live 모드 코드 경로 테스트: 실제 anthropic SDK를 쓰되 HTTP만 가짜로 바꿔, SDK가 보내는 요청과 녹화를 확인한다."""
import io
import json

import anthropic
import httpx2
import pytest

from moduon_demo.ai import extract_doc, nlq_parse
from moduon_demo.console import Reporter
from moduon_demo.llm import DEFAULT_MODEL, FALLBACK_BETA, LLM, AIError

GOOD = {"intent": "price_lookup", "params": {"partner": "A통신", "category": None, "field": "subsidy_amount",
                                             "condition": "join=mnp", "op": "gte", "amount": 300000, "direction": None},
        "unsupported_reason": None}


def _client(handler):
    return anthropic.Anthropic(api_key="test-key", max_retries=0,
                               http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


def _reply(output: dict, model: str, stop_reason="end_turn"):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}],
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 1200, "output_tokens": 300,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


def _capture(model, output=GOOD):
    seen = {}

    def handler(request):
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=_reply(output, model))
    return seen, handler


def test_default_model_is_haiku():
    assert DEFAULT_MODEL == "claude-haiku-4-5"


def test_haiku_request_shape_and_recording(tmp_path):
    seen, handler = _capture("claude-haiku-4-5")
    llm = LLM("live", tmp_path, Reporter(file=io.StringIO()), client=_client(handler))
    out = nlq_parse.run(llm, "q2_nlq", "A통신 번호이동 공시지원금 30만원 이상", "2026-10", "2026-09")
    assert out == GOOD

    body = seen["body"]
    assert body["model"] == "claude-haiku-4-5"
    assert "effort" not in body["output_config"]              # Haiku 4.5는 effort를 받지 않음(400)
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in body                              # effort=low → thinking 끔
    assert "fallbacks" not in body and not seen["headers"].get("anthropic-beta")
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "기준 월: 2026-10" in body["messages"][0]["content"][0]["text"]   # 날짜는 system이 아니라 user 메시지에

    rec = json.loads((tmp_path / "recordings" / "claude-haiku-4-5" / "q2_nlq.json").read_text(encoding="utf-8"))
    assert rec["output"] == GOOD and rec["input_hash"] and rec["duration_s"] >= 0
    assert rec["cost_usd"] == pytest.approx((1200 * 1 + 300 * 5) / 1_000_000)   # $1 / $5 per 1M

    replay = LLM("replay", tmp_path, Reporter(file=io.StringIO()))
    assert nlq_parse.run(replay, "q2_nlq", "A통신 번호이동 공시지원금 30만원 이상", "2026-10", "2026-09") == GOOD


def test_haiku_medium_effort_becomes_thinking_budget(tmp_path):
    extract = {"doc_type": "other", "effective_period_text": None, "rows": [], "document_notes": [],
               "unreadable_regions": []}
    seen, handler = _capture("claude-haiku-4-5", extract)
    llm = LLM("live", tmp_path, Reporter(file=io.StringIO()), client=_client(handler))
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    extract_doc.run(llm, pdf, "x.pdf")                          # 추출은 effort=medium
    assert seen["body"]["thinking"] == {"type": "enabled", "budget_tokens": 2048}
    assert seen["body"]["max_tokens"] > 2048
    assert seen["body"]["messages"][0]["content"][0]["type"] == "document"


def test_opus_request_shape(tmp_path):
    seen, handler = _capture("claude-opus-5")
    llm = LLM("live", tmp_path, Reporter(file=io.StringIO()), model="claude-opus-5", client=_client(handler))
    nlq_parse.run(llm, "q2_nlq", "질문", "2026-10", "2026-09")
    body = seen["body"]
    assert body["model"] == "claude-opus-5"
    assert body["thinking"] == {"type": "adaptive"} and body["output_config"]["effort"] == "low"
    assert body["fallbacks"] == "default" and FALLBACK_BETA in seen["headers"]["anthropic-beta"]
    rec = json.loads((tmp_path / "recordings" / "claude-opus-5" / "q2_nlq.json").read_text(encoding="utf-8"))
    assert rec["cost_usd"] == pytest.approx((1200 * 5 + 300 * 25) / 1_000_000)


def test_opus_no_fallback_uses_plain_messages(tmp_path):
    seen, handler = _capture("claude-opus-5")
    llm = LLM("live", tmp_path, Reporter(file=io.StringIO()), model="claude-opus-5",
              use_fallback=False, client=_client(handler))
    nlq_parse.run(llm, "q2_nlq", "질문", "2026-10", "2026-09")
    assert "fallbacks" not in seen["body"] and not seen["headers"].get("anthropic-beta")


def test_unknown_model_rejected(tmp_path):
    with pytest.raises(AIError):
        LLM("mock", tmp_path, Reporter(file=io.StringIO()), model="gpt-x")


def test_refusal_and_truncation_are_not_silently_accepted(tmp_path):
    for stop in ("refusal", "max_tokens"):
        llm = LLM("live", tmp_path, Reporter(file=io.StringIO()),
                  client=_client(lambda r, s=stop: httpx2.Response(200, json=_reply(GOOD, DEFAULT_MODEL, s))))
        with pytest.raises(AIError):
            nlq_parse.run(llm, "q2_nlq", "질문", "2026-10", "2026-09")


def test_auth_error_message(tmp_path):
    llm = LLM("live", tmp_path, Reporter(file=io.StringIO()),
              client=_client(lambda r: httpx2.Response(401, json={"type": "error", "error": {
                  "type": "authentication_error", "message": "invalid x-api-key"}})))
    with pytest.raises(AIError, match="API 키"):
        nlq_parse.run(llm, "q2_nlq", "질문", "2026-10", "2026-09")
