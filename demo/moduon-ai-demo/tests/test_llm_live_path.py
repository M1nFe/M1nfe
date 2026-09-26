"""live 모드 코드 경로 테스트: 실제 anthropic SDK를 쓰되 HTTP만 가짜로 바꿔, SDK가 보내는 요청과 녹화를 확인한다."""
import io
import json

import anthropic
import httpx2
import pytest

from moduon_demo.ai import nlq_parse
from moduon_demo.console import Reporter
from moduon_demo.llm import FALLBACK_BETA, LLM, MODEL, AIError


def _client(handler):
    return anthropic.Anthropic(api_key="test-key", max_retries=0,
                               http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


def _reply(output: dict, stop_reason="end_turn"):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": MODEL,
            "content": [{"type": "text", "text": json.dumps(output, ensure_ascii=False)}],
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 1200, "output_tokens": 300,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


GOOD = {"intent": "price_lookup", "params": {"partner": "A통신", "category": None, "field": "subsidy_amount",
                                             "condition": "join=mnp", "op": "gte", "amount": 300000, "direction": None},
        "unsupported_reason": None}


def test_live_request_shape_and_recording(tmp_path):
    seen = {}

    def handler(request):
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=_reply(GOOD))

    rep = Reporter(file=io.StringIO())
    llm = LLM("live", tmp_path, rep, client=_client(handler))
    out = nlq_parse.run(llm, "q2_nlq", "A통신 번호이동 공시지원금 30만원 이상", "2026-10", "2026-09")

    assert out == GOOD
    body = seen["body"]
    assert body["model"] == "claude-opus-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in seen["headers"]["anthropic-beta"]
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "기준 월: 2026-10" in body["messages"][0]["content"][0]["text"]   # 날짜는 system이 아니라 user 메시지에

    rec = json.loads((tmp_path / "recordings" / "q2_nlq.json").read_text(encoding="utf-8"))
    assert rec["output"] == GOOD and rec["model"] == MODEL and rec["input_hash"]
    assert rec["cost_usd"] == pytest.approx((1200 * 5 + 300 * 25) / 1_000_000)
    assert llm.calls[0].input_tokens == 1200

    # 녹화본 재생(replay)은 같은 결과를 돌려준다
    replay = LLM("replay", tmp_path, Reporter(file=io.StringIO()))
    assert nlq_parse.run(replay, "q2_nlq", "A통신 번호이동 공시지원금 30만원 이상", "2026-10", "2026-09") == GOOD


def test_no_fallback_uses_plain_messages(tmp_path):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        return httpx2.Response(200, json=_reply(GOOD))

    llm = LLM("live", tmp_path, Reporter(file=io.StringIO()), use_fallback=False, client=_client(handler))
    nlq_parse.run(llm, "q2_nlq", "질문", "2026-10", "2026-09")
    assert "fallbacks" not in seen["body"] and not seen["beta"]


def test_refusal_and_truncation_are_not_silently_accepted(tmp_path):
    for stop in ("refusal", "max_tokens"):
        llm = LLM("live", tmp_path, Reporter(file=io.StringIO()),
                  client=_client(lambda r, s=stop: httpx2.Response(200, json=_reply(GOOD, stop_reason=s))))
        with pytest.raises(AIError):
            nlq_parse.run(llm, "q2_nlq", "질문", "2026-10", "2026-09")


def test_auth_error_message(tmp_path):
    llm = LLM("live", tmp_path, Reporter(file=io.StringIO()),
              client=_client(lambda r: httpx2.Response(401, json={"type": "error", "error": {
                  "type": "authentication_error", "message": "invalid x-api-key"}})))
    with pytest.raises(AIError, match="API 키"):
        nlq_parse.run(llm, "q2_nlq", "질문", "2026-10", "2026-09")
