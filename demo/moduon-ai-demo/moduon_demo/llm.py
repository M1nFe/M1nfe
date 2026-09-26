"""Claude 호출은 전부 이 모듈 한 곳을 거친다.

세 가지 모드:
- live   : 실제 Claude API 호출. 응답을 recordings/ 에 녹화한다. (ANTHROPIC_API_KEY 필요)
- replay : recordings/ 에 녹화된 '실제 응답'을 다시 재생한다. (키 없이 시연 가능)
- mock   : mock_responses/ 의 '모의 응답'을 쓴다. 실제 AI 호출이 아니며 화면에 그렇게 표시한다.
"""
import base64
import copy
import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

MODEL = "claude-opus-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PRICE_PER_MTOK = {"input": 5.0, "output": 25.0}   # claude-opus-5 기준(USD). 캐시 단가는 추정치

MODE_LABEL = {
    "live": "실제 Claude API 호출",
    "replay": "녹화된 실제 Claude 응답 재생",
    "mock": "모의 응답 — 실제 AI 호출 아님",
}


class AIError(Exception):
    pass


@dataclass
class CallLog:
    step_id: str
    title: str
    mode: str
    model: str | None
    effort: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    notes: list[str] = field(default_factory=list)


def _redact_blocks(content):
    """보고서·해시용: base64 문서는 길이와 해시만 남긴다."""
    out = copy.deepcopy(content)
    for b in out if isinstance(out, list) else []:
        src = b.get("source") if isinstance(b, dict) else None
        if src and src.get("type") == "base64":
            data = src["data"]
            src["data"] = f"<base64 {len(data)}자, sha256={hashlib.sha256(data.encode()).hexdigest()[:12]}>"
    return out


class LLM:
    def __init__(self, mode: str, root: Path, reporter, use_fallback: bool = True, client=None):
        self.mode = mode
        self.rec_dir = root / "recordings"
        self.mock_dir = root / "mock_responses"
        self.rep = reporter
        self.use_fallback = use_fallback
        self.calls: list[CallLog] = []
        self.client = client
        if mode == "live" and client is None:
            import anthropic   # live 모드에서만 필요
            self.client = anthropic.Anthropic()

    def _input_hash(self, system, content, schema, effort) -> str:
        blob = json.dumps({"model": MODEL, "effort": effort, "system": system,
                           "content": _redact_blocks(content), "schema": schema},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def run(self, step_id: str, *, title: str, system: str, content, schema: dict,
            effort: str = "low", max_tokens: int = 16000) -> dict:
        """AI 한 번 호출. 반환값은 JSON Schema 검증을 통과한 dict."""
        h = self._input_hash(system, content, schema, effort)
        log = CallLog(step_id, title, self.mode, None, effort)
        self.rep.say("AI", f"Claude 호출: {title}  [{MODE_LABEL[self.mode]}]")
        self.rep.note(f"모델 {MODEL} · effort={effort} · 출력 형식은 JSON Schema로 고정(구조화 출력)")
        self.rep.md += ["", f"<details><summary>AI 호출 원문 — {step_id}</summary>", "",
                        "**시스템 프롬프트(AI에게 준 규칙)**", "", "```text", system, "```", "",
                        "**보낸 내용**", "", "```json",
                        json.dumps(_redact_blocks(content), ensure_ascii=False, indent=2), "```", "",
                        "**출력 형식(JSON Schema)**", "", "```json",
                        json.dumps(schema, ensure_ascii=False, indent=2), "```", "", "</details>", ""]

        if self.mode == "live":
            data, log = self._live(step_id, system, content, schema, effort, max_tokens, h, log)
        else:
            path = (self.rec_dir if self.mode == "replay" else self.mock_dir) / f"{step_id}.json"
            if not path.exists():
                raise AIError(f"{path} 가 없습니다. live 모드로 한 번 실행해 녹화하거나 --mode mock을 쓰세요.")
            rec = json.loads(path.read_text(encoding="utf-8"))
            data = rec["output"]
            log.model = rec.get("model")
            if self.mode == "replay":
                u = rec.get("usage") or {}
                log.input_tokens, log.output_tokens = u.get("input_tokens", 0), u.get("output_tokens", 0)
                log.cache_read_tokens = u.get("cache_read_input_tokens") or 0
                log.cost_usd = rec.get("cost_usd", 0.0)
                self.rep.note(f"녹화 시각 {rec.get('recorded_at')} · 응답 모델 {rec.get('model')}")
                if rec.get("input_hash") != h:
                    self.rep.warn("입력이 녹화 당시와 다릅니다(프롬프트·데이터 변경). live 모드로 다시 녹화하세요.")
            else:
                self.rep.note("※ 이 응답은 사람이 미리 써 둔 모의 응답입니다. 실제 AI 결과를 보려면 API 키를 넣고 live 모드로 실행하세요.")

        jsonschema.validate(data, schema)   # 모의·재생 응답도 같은 형식 검사를 받는다
        self.calls.append(log)
        return data

    def _live(self, step_id, system, content, schema, effort, max_tokens, h, log):
        import anthropic
        kwargs = dict(
            model=MODEL,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        )
        try:
            if self.use_fallback:
                # 안전 분류기가 거절하면 서버가 대체 모델로 자동 재시도(fallbacks="default")
                resp = self.client.beta.messages.create(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                resp = self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise AIError("API 키가 올바르지 않습니다(ANTHROPIC_API_KEY 확인).") from e
        except anthropic.BadRequestError as e:
            raise AIError(f"요청 오류(400): {e.message}  — fallback beta가 원인이면 --no-fallback 으로 실행") from e
        except anthropic.APIStatusError as e:
            raise AIError(f"API 오류 {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise AIError("네트워크 오류로 API에 연결하지 못했습니다.") from e

        if resp.stop_reason == "refusal":
            raise AIError(f"모델이 요청을 거절했습니다(refusal). 이 건은 수작업으로 넘깁니다: {resp.stop_details}")
        if resp.stop_reason == "max_tokens":
            raise AIError("출력이 max_tokens에서 잘렸습니다. 입력을 더 작게 나눠야 합니다.")
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)

        u = resp.usage
        log.model = resp.model
        log.input_tokens = u.input_tokens or 0
        log.output_tokens = u.output_tokens or 0
        log.cache_read_tokens = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        log.cost_usd = round((log.input_tokens * PRICE_PER_MTOK["input"]
                              + cache_write * PRICE_PER_MTOK["input"] * 1.25
                              + log.cache_read_tokens * PRICE_PER_MTOK["input"] * 0.1
                              + log.output_tokens * PRICE_PER_MTOK["output"]) / 1_000_000, 5)
        self.rep.note(f"응답 모델 {resp.model} · 입력 {log.input_tokens:,} / 출력 {log.output_tokens:,} 토큰"
                      f" · 약 ${log.cost_usd:.4f}")
        self.rec_dir.mkdir(exist_ok=True)
        (self.rec_dir / f"{step_id}.json").write_text(json.dumps({
            "step_id": step_id,
            "recorded_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": resp.model,
            "request_id": getattr(resp, "_request_id", None),
            "stop_reason": resp.stop_reason,
            "usage": u.to_dict() if hasattr(u, "to_dict") else None,
            "cost_usd": log.cost_usd,
            "input_hash": h,
            "output": data,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return data, log


def pdf_block(path: Path) -> dict:
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
