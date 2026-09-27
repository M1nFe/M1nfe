"""Claude 호출은 전부 이 모듈 한 곳을 거친다.

세 가지 모드:
- live   : 실제 Claude API 호출. 응답을 recordings/<모델>/ 에 녹화한다. (ANTHROPIC_API_KEY 필요)
- replay : recordings/<모델>/ 에 녹화된 '실제 응답'을 다시 재생한다. (키 없이 시연 가능)
- mock   : mock_responses/ 의 '모의 응답'을 쓴다. 실제 AI 호출이 아니며 화면에 그렇게 표시한다.
"""
import base64
import copy
import datetime as dt
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

DEFAULT_MODEL = "claude-haiku-4-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass(frozen=True)
class ModelProfile:
    input_price: float        # USD / 1M 토큰
    output_price: float
    reasoning: str            # "adaptive": thinking adaptive + output_config.effort / "budget": thinking 예산(effort 미지원)
    server_fallback: bool     # 안전 분류기 거절 시 서버측 대체 모델 재시도(fallbacks="default") 사용
    label: str


MODELS = {
    "claude-haiku-4-5": ModelProfile(1.0, 5.0, "budget", False, "Haiku 4.5 — 가장 빠르고 저렴, 200K 컨텍스트"),
    "claude-sonnet-5": ModelProfile(2.0, 10.0, "adaptive", False, "Sonnet 5 — 중간"),
    "claude-opus-5": ModelProfile(5.0, 25.0, "adaptive", True, "Opus 5 — 가장 정확"),
}
# Haiku 4.5는 effort 파라미터를 받지 않는다(400). 같은 low/medium/high를 thinking 예산으로 바꾼다.
BUDGET_BY_EFFORT = {"low": None, "medium": 2048, "high": 4096}

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
    reasoning: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
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
    def __init__(self, mode: str, root: Path, reporter, model: str = DEFAULT_MODEL,
                 use_fallback: bool = True, client=None):
        if model not in MODELS:
            raise AIError(f"지원하지 않는 모델: {model} (가능: {', '.join(MODELS)})")
        self.mode = mode
        self.model = model
        self.profile = MODELS[model]
        self.rec_dir = root / "recordings" / model
        self.mock_dir = root / "mock_responses"
        self.rep = reporter
        self.use_fallback = use_fallback and self.profile.server_fallback
        self.calls: list[CallLog] = []
        self.client = client
        if mode == "live" and client is None:
            import anthropic   # live 모드에서만 필요
            self.client = anthropic.Anthropic()

    def reasoning(self, effort: str) -> tuple[dict, dict, str]:
        """(요청 최상위 파라미터, output_config 추가분, 화면 표시용 설명)"""
        if self.profile.reasoning == "adaptive":
            return {"thinking": {"type": "adaptive"}}, {"effort": effort}, f"effort={effort}"
        budget = BUDGET_BY_EFFORT[effort]
        if budget is None:
            return {}, {}, "thinking 끔(단순 작업)"
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}, {}, f"thinking 예산 {budget:,} 토큰"

    def _input_hash(self, system, content, schema, effort) -> str:
        blob = json.dumps({"model": self.model, "effort": effort, "system": system,
                           "content": _redact_blocks(content), "schema": schema},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def run(self, step_id: str, *, title: str, system: str, content, schema: dict,
            effort: str = "low", max_tokens: int = 16000) -> dict:
        """AI 한 번 호출. 반환값은 JSON Schema 검증을 통과한 dict."""
        h = self._input_hash(system, content, schema, effort)
        _, _, rlabel = self.reasoning(effort)
        log = CallLog(step_id, title, self.mode, None, rlabel)
        self.rep.say("AI", f"Claude 호출: {title}  [{MODE_LABEL[self.mode]}]")
        self.rep.note(f"모델 {self.model} · {rlabel} · 출력 형식은 JSON Schema로 고정(구조화 출력)")
        self.rep.md += ["", f"<details><summary>AI 호출 원문 — {step_id}</summary>", "",
                        "**시스템 프롬프트(AI에게 준 규칙)**", "", "```text", system, "```", "",
                        "**보낸 내용**", "", "```json",
                        json.dumps(_redact_blocks(content), ensure_ascii=False, indent=2), "```", "",
                        "**출력 형식(JSON Schema)**", "", "```json",
                        json.dumps(schema, ensure_ascii=False, indent=2), "```", "", "</details>", ""]

        if self.mode == "live":
            data = self._live(step_id, system, content, schema, effort, max_tokens, h, log)
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
                log.duration_s = rec.get("duration_s", 0.0)
                self.rep.note(f"녹화 시각 {rec.get('recorded_at')} · 응답 모델 {rec.get('model')}")
                if rec.get("input_hash") != h:
                    self.rep.warn("입력이 녹화 당시와 다릅니다(프롬프트·데이터 변경). live 모드로 다시 녹화하세요.")
            else:
                self.rep.note("※ 이 응답은 사람이 미리 써 둔 모의 응답입니다. 실제 AI 결과를 보려면 API 키를 넣고 live 모드로 실행하세요.")

        jsonschema.validate(data, schema)   # 모의·재생 응답도 같은 형식 검사를 받는다
        self.calls.append(log)
        return data

    def _live(self, step_id, system, content, schema, effort, max_tokens, h, log) -> dict:
        import anthropic
        top, oc_extra, _ = self.reasoning(effort)
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            # 고정 규칙은 캐시 대상. 모델별 최소 길이(Haiku 4.5는 4,096토큰)보다 짧으면 캐시되지 않을 뿐 오류는 아니다
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
            output_config={**oc_extra, "format": {"type": "json_schema", "schema": schema}},
            **top,
        )
        t0 = time.perf_counter()
        try:
            if self.use_fallback:
                # 안전 분류기가 거절하면 서버가 대체 모델로 자동 재시도(fallbacks="default")
                resp = self.client.beta.messages.create(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
            else:
                resp = self.client.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise AIError("API 키가 올바르지 않습니다(ANTHROPIC_API_KEY 확인).") from e
        except anthropic.BadRequestError as e:
            hint = "  — fallback beta가 원인이면 --no-fallback 으로 실행" if self.use_fallback else ""
            raise AIError(f"요청 오류(400): {e.message}{hint}") from e
        except anthropic.APIStatusError as e:
            raise AIError(f"API 오류 {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise AIError("네트워크 오류로 API에 연결하지 못했습니다.") from e
        log.duration_s = round(time.perf_counter() - t0, 2)

        if resp.stop_reason == "refusal":
            raise AIError(f"모델이 요청을 거절했습니다(refusal). 이 건은 수작업으로 넘깁니다: {resp.stop_details}")
        if resp.stop_reason == "max_tokens":
            raise AIError("출력이 max_tokens에서 잘렸습니다. 입력을 더 작게 나눠야 합니다.")
        text = next(b.text for b in resp.content if b.type == "text")
        data = json.loads(text)

        u = resp.usage
        p = self.profile
        log.model = resp.model
        log.input_tokens = u.input_tokens or 0
        log.output_tokens = u.output_tokens or 0
        log.cache_read_tokens = getattr(u, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        log.cost_usd = round((log.input_tokens * p.input_price
                              + cache_write * p.input_price * 1.25
                              + log.cache_read_tokens * p.input_price * 0.1
                              + log.output_tokens * p.output_price) / 1_000_000, 5)
        self.rep.note(f"응답 모델 {resp.model} · 입력 {log.input_tokens:,} / 출력 {log.output_tokens:,} 토큰"
                      f" · 약 ${log.cost_usd:.4f} · {log.duration_s}초")
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        (self.rec_dir / f"{step_id}.json").write_text(json.dumps({
            "step_id": step_id,
            "recorded_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": resp.model,
            "request_id": getattr(resp, "_request_id", None),
            "stop_reason": resp.stop_reason,
            "usage": u.to_dict() if hasattr(u, "to_dict") else None,
            "cost_usd": log.cost_usd,
            "duration_s": log.duration_s,
            "input_hash": h,
            "output": data,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return data


def pdf_block(path: Path) -> dict:
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
