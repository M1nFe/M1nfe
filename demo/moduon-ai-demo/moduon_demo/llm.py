"""AI 호출은 전부 이 모듈 한 곳을 거친다.

두 가지 제공자(provider):
- anthropic : Claude API (ANTHROPIC_API_KEY 필요, 기본 모델 claude-haiku-4-5)
- ollama    : 내 PC에서 돌리는 오픈소스 모델 (키 불필요, 기본 모델 qwen2.5:7b)

세 가지 모드:
- live   : 실제 AI 호출. 응답을 recordings/<모델>/ 에 녹화한다.
- replay : recordings/<모델>/ 에 녹화된 '실제 응답'을 다시 재생한다. (키·인터넷 없이 시연 가능)
- mock   : mock_responses/ 의 '모의 응답'을 쓴다. 실제 AI 호출이 아니며 화면에 그렇게 표시한다.
"""
import base64
import copy
import datetime as dt
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"
DEFAULT_MODELS = {"anthropic": DEFAULT_MODEL, "ollama": DEFAULT_OLLAMA_MODEL}
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
    "live": "실제 AI 호출",
    "replay": "녹화된 실제 AI 응답 재생",
    "mock": "모의 응답 — 실제 AI 호출 아님",
}

# Ollama 설정: 프롬프트가 잘리지 않게 컨텍스트를 넉넉히, 결과가 매번 같도록 temperature 0
OLLAMA_OPTIONS = {"temperature": 0, "num_ctx": 8192, "num_predict": 4096}
OLLAMA_TIMEOUT_S = 600          # CPU에서 도는 로컬 모델은 느릴 수 있다


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


def _pdf_text(b64: str) -> str:
    """PDF(base64) → 페이지 표시가 붙은 텍스트. 로컬 텍스트 모델은 PDF를 직접 읽지 못하므로 코드가 텍스트를 뽑아 준다."""
    import pdfplumber
    with pdfplumber.open(io.BytesIO(base64.b64decode(b64))) as pdf:
        return "\n".join(f"[페이지 {i}]\n{p.extract_text() or ''}" for i, p in enumerate(pdf.pages, 1))


def to_plain_text(content) -> str:
    """Claude 형식의 content 블록 목록을 텍스트 한 덩어리로 바꾼다(PDF는 텍스트 추출본으로)."""
    parts = []
    for b in content:
        if b.get("type") == "text":
            parts.append(b["text"])
        elif b.get("type") == "document" and b["source"].get("media_type") == "application/pdf":
            parts.append("<document_text source=\"PDF에서 코드가 추출한 텍스트\">\n"
                         f"{_pdf_text(b['source']['data'])}\n</document_text>")
        else:
            raise AIError(f"로컬 모델로 보낼 수 없는 입력 형식: {b.get('type')}")
    return "\n\n".join(parts)


_THINK = re.compile(r"^\s*<think>.*?</think>\s*", re.S)


def _safe_dir(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", name)


class LLM:
    def __init__(self, mode: str, root: Path, reporter, model: str | None = None, provider: str = "anthropic",
                 use_fallback: bool = True, client=None, base_url: str | None = None, record: bool = True,
                 history: list | None = None):
        if provider not in DEFAULT_MODELS:
            raise AIError(f"지원하지 않는 제공자: {provider} (가능: {', '.join(DEFAULT_MODELS)})")
        model = model or DEFAULT_MODELS[provider]
        if provider == "anthropic" and model not in MODELS:
            raise AIError(f"지원하지 않는 Claude 모델: {model} (가능: {', '.join(MODELS)})")
        self.mode = mode
        self.provider = provider
        self.model = model
        self.profile = MODELS.get(model, ModelProfile(0.0, 0.0, "local", False, f"Ollama 로컬 모델 {model}"))
        folder = model if provider == "anthropic" else f"ollama__{_safe_dir(model)}"
        self.rec_dir = root / "recordings" / folder
        self.mock_dir = root / "mock_responses"
        self.rep = reporter
        self.use_fallback = use_fallback and self.profile.server_fallback
        self.calls: list[CallLog] = []
        self.record = record                  # False: 녹화본을 남기지 않는다(실행 프로그램은 발표용 녹화본을 덮어쓰지 않음)
        self.history = history if history is not None else []   # 호출마다 보낸 원문·응답(실행 프로그램의 'AI 기록')
        self.client = client
        host = base_url or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
        self.base_url = (host if host.startswith("http") else f"http://{host}").rstrip("/")
        if mode == "live" and provider == "anthropic" and client is None:
            import anthropic   # Claude live 모드에서만 필요
            self.client = anthropic.Anthropic()
        if mode == "live" and provider == "ollama":
            self._ollama_check()

    # ---------- 표시용 ----------
    @property
    def display_name(self) -> str:
        return "Claude" if self.provider == "anthropic" else f"Ollama 로컬 모델({self.model})"

    @property
    def mode_label(self) -> str:
        if self.mode == "mock":
            return MODE_LABEL["mock"]
        where = "Claude API" if self.provider == "anthropic" else f"Ollama 로컬 {self.model}"
        return f"{MODE_LABEL[self.mode]} — {where}"

    def reasoning(self, effort: str) -> tuple[dict, dict, str]:
        """(요청 최상위 파라미터, output_config 추가분, 화면 표시용 설명)"""
        if self.provider == "ollama":
            return {}, {}, "temperature 0"
        if self.profile.reasoning == "adaptive":
            return {"thinking": {"type": "adaptive"}}, {"effort": effort}, f"effort={effort}"
        budget = BUDGET_BY_EFFORT[effort]
        if budget is None:
            return {}, {}, "thinking 끔(단순 작업)"
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}, {}, f"thinking 예산 {budget:,} 토큰"

    def _input_hash(self, system, content, schema, effort) -> str:
        blob = json.dumps({"provider": self.provider, "model": self.model, "effort": effort, "system": system,
                           "content": _redact_blocks(content), "schema": schema},
                          ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    # ---------- 공통 진입점 ----------
    def run(self, step_id: str, *, title: str, system: str, content, schema: dict,
            effort: str = "low", max_tokens: int = 16000) -> dict:
        """AI 한 번 호출. 반환값은 JSON Schema 검증을 통과한 dict."""
        h = self._input_hash(system, content, schema, effort)
        _, _, rlabel = self.reasoning(effort)
        log = CallLog(step_id, title, self.mode, None, rlabel)
        self.rep.say("AI", f"{self.display_name} 호출: {title}  [{self.mode_label}]")
        self.rep.note(f"모델 {self.model} · {rlabel} · 출력 형식은 JSON Schema로 고정(구조화 출력)")
        if self.provider == "ollama" and any(b.get("type") == "document" for b in content):
            self.rep.note("로컬 모델은 PDF를 직접 읽지 못하므로, 코드가 PDF에서 뽑은 텍스트를 보낸다")
        self.rep.md += ["", f"<details><summary>AI 호출 원문 — {step_id}</summary>", "",
                        "**시스템 프롬프트(AI에게 준 규칙)**", "", "```text", system, "```", "",
                        "**보낸 내용**", "", "```json",
                        json.dumps(_redact_blocks(content), ensure_ascii=False, indent=2), "```", "",
                        "**출력 형식(JSON Schema)**", "", "```json",
                        json.dumps(schema, ensure_ascii=False, indent=2), "```", "", "</details>", ""]

        if self.mode == "live":
            if self.provider == "ollama":
                data, raw = self._live_ollama(system, content, schema, log)
            else:
                data, raw = self._live_anthropic(system, content, schema, effort, max_tokens, log)
            if self.record:
                self._record(step_id, h, log, data, raw)
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
                self.rep.note("※ 이 응답은 사람이 미리 써 둔 모의 응답입니다. 실제 AI 결과를 보려면 live 모드로 실행하세요.")

        jsonschema.validate(data, schema)   # 모의·재생 응답도 같은 형식 검사를 받는다
        self.calls.append(log)
        self._remember(log, system, content, schema, data)
        return data

    def _remember(self, log, system, content, schema, data):
        self.history.append({
            "no": len(self.history) + 1, "step_id": log.step_id, "title": log.title, "mode": self.mode,
            "mode_label": self.mode_label, "provider": self.provider, "model": log.model or self.model,
            "reasoning": log.reasoning, "input_tokens": log.input_tokens, "output_tokens": log.output_tokens,
            "duration_s": log.duration_s, "cost_usd": log.cost_usd, "system": system,
            "content": _redact_blocks(content), "schema": schema, "output": data})

    def mock_output(self, step_id: str, *, title: str, system: str, content, schema: dict, output: dict) -> dict:
        """모의 모드에서 녹화 파일 대신 부르는 쪽이 고른 모의 응답을 쓴다(실행 프로그램의 이름별 매칭 등).
        형식 검사와 호출 기록은 run()과 똑같이 한다."""
        assert self.mode == "mock"
        log = CallLog(step_id, title, "mock", None, "모의 응답")
        self.rep.say("AI", f"{self.display_name} 호출: {title}  [{self.mode_label}]")
        jsonschema.validate(output, schema)
        self.calls.append(log)
        self._remember(log, system, content, schema, output)
        return output

    def _record(self, step_id, h, log, data, raw_usage):
        self.rep.note(f"응답 모델 {log.model} · 입력 {log.input_tokens:,} / 출력 {log.output_tokens:,} 토큰"
                      f" · 약 ${log.cost_usd:.4f} · {log.duration_s}초")
        self.rec_dir.mkdir(parents=True, exist_ok=True)
        (self.rec_dir / f"{step_id}.json").write_text(json.dumps({
            "step_id": step_id,
            "recorded_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "provider": self.provider,
            "model": log.model,
            "usage": raw_usage,
            "cost_usd": log.cost_usd,
            "duration_s": log.duration_s,
            "input_hash": h,
            "output": data,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- Claude ----------
    def _live_anthropic(self, system, content, schema, effort, max_tokens, log):
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
        data = json.loads(next(b.text for b in resp.content if b.type == "text"))

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
        return data, (u.to_dict() if hasattr(u, "to_dict") else None)

    # ---------- Ollama (로컬 오픈소스 모델) ----------
    def _ollama(self, path: str, payload: dict | None = None) -> dict:
        req = urllib.request.Request(
            self.base_url + path,
            data=None if payload is None else json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT_S) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 404 and "not found" in detail:
                raise AIError(f"Ollama에 모델이 없습니다. 먼저 받으세요:  ollama pull {self.model}") from e
            raise AIError(f"Ollama 오류 {e.code}: {detail}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise AIError(f"Ollama 서버({self.base_url})에 연결할 수 없습니다. "
                          "Ollama 앱을 켜거나 터미널에서 `ollama serve`를 실행하세요.") from e

    def _ollama_check(self):
        names = {m.get("name") for m in self._ollama("/api/tags").get("models", [])}
        want = self.model if ":" in self.model else f"{self.model}:latest"
        if want not in names:
            raise AIError(f"Ollama에 '{self.model}' 모델이 없습니다. 먼저 받으세요:  ollama pull {self.model}"
                          + (f"  (설치된 모델: {', '.join(sorted(n for n in names if n))})" if names else ""))

    def _live_ollama(self, system, content, schema, log):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": to_plain_text(content)}]
        t0 = time.perf_counter()
        prompt_tok = out_tok = 0
        for attempt in range(2):          # 형식이 어긋나면 오류 내용을 알려 주고 한 번 더 요청
            resp = self._ollama("/api/chat", {"model": self.model, "messages": messages, "format": schema,
                                              "stream": False, "options": OLLAMA_OPTIONS})
            prompt_tok += resp.get("prompt_eval_count") or 0
            out_tok += resp.get("eval_count") or 0
            if resp.get("done_reason") == "length":
                raise AIError("로컬 모델 출력이 길이 제한에서 잘렸습니다(num_predict). 입력을 줄이거나 제한을 늘리세요.")
            text = _THINK.sub("", resp.get("message", {}).get("content", ""))
            try:
                data = json.loads(text)
                jsonschema.validate(data, schema)
                break
            except (json.JSONDecodeError, jsonschema.ValidationError) as e:
                if attempt == 1:
                    raise AIError(f"로컬 모델이 정해진 형식(JSON Schema)에 맞게 답하지 못했습니다: {str(e)[:200]}") from e
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"형식 오류: {str(e)[:300]}\n정해진 JSON Schema에 맞게 JSON만 다시 출력하라."}]
        log.duration_s = round(time.perf_counter() - t0, 2)
        log.model = resp.get("model", self.model)
        log.input_tokens, log.output_tokens = prompt_tok, out_tok
        log.cost_usd = 0.0                # 로컬 실행: API 비용 없음(전기·장비 비용은 별도)
        return data, {"input_tokens": prompt_tok, "output_tokens": out_tok}


def pdf_block(path: Path) -> dict:
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}
