"""모두온 AI 시연 실행기 (UI 없음, 터미널).

    python run_demo.py                                   # 자동 모드 선택(아래 설명)
    python run_demo.py --provider ollama                 # 내 PC의 오픈소스 모델(Ollama, 기본 qwen2.5:7b) — 키 불필요
    python run_demo.py --provider ollama --model gemma3:12b
    python run_demo.py --mode live                       # Claude API (기본 claude-haiku-4-5, ANTHROPIC_API_KEY 필요)
    python run_demo.py --mode replay --pause             # 녹화본으로 발표(단계마다 Enter)
    python run_demo.py --quiet                           # 화면 출력 없이 결과 파일만(OpenClaw 등 에이전트용)
"""
import argparse
import io
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from moduon_demo import scenes
from moduon_demo.console import Reporter
from moduon_demo.llm import DEFAULT_MODELS, LLM, AIError, _safe_dir
from moduon_demo.store import Store

ROOT = Path(__file__).resolve().parent
SCENES = (scenes.scene0_setup, scenes.scene1_excel, scenes.scene2_pdf, scenes.scene3_matching,
          scenes.scene4_anomaly, scenes.scene5_notice, scenes.scene6_confirm_and_calc,
          scenes.scene7_nlq, scenes.scene8_summary)


def rec_folder(provider: str, model: str) -> Path:
    return ROOT / "recordings" / (model if provider == "anthropic" else f"ollama__{_safe_dir(model)}")


def detect_mode(provider: str, model: str) -> str:
    """Ollama는 키가 필요 없으므로 live. Claude는 키가 있으면 live, 없으면 녹화본, 녹화본도 없으면 mock."""
    if provider == "ollama":
        return "live"
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "live"
    if all((rec_folder(provider, model) / f"{s}.json").exists() for s in scenes.STEP_IDS):
        return "replay"
    return "mock"


def run(mode: str, model: str | None = None, *, provider="anthropic", base_url=None, pause=False, quiet=False,
        width=None, use_fallback=True, tamper=True):
    """시연 전체를 실행하고 (ctx, 보고서 경로, 요약 JSON 경로)를 돌려준다."""
    model = model or DEFAULT_MODELS[provider]
    rep = Reporter(pause=pause and not quiet, width=width, file=io.StringIO() if quiet else None)
    tag = f"{model if provider == 'anthropic' else 'ollama__' + _safe_dir(model)}_{mode}"
    report = ROOT / "output" / f"demo_report_{tag}.md"
    summary = ROOT / "output" / f"demo_summary_{tag}.json"
    ctx = None
    try:
        llm = LLM(mode, ROOT, rep, model=model, provider=provider, use_fallback=use_fallback, base_url=base_url)
        ctx = scenes.Ctx(root=ROOT, store=Store(), llm=llm, rep=rep,
                         key=json.loads((ROOT / "data/answer_key.json").read_text(encoding="utf-8")),
                         tamper=tamper)
        for scene in SCENES:
            scene(ctx)
    finally:
        rep.save(report)
        if ctx is not None:
            summary.write_text(json.dumps({
                "mode": mode,
                "mode_label": ctx.llm.mode_label,
                "is_real_ai": mode in ("live", "replay"),
                "provider": provider,
                "model": model,
                "completed": len(ctx.llm.calls) == len(scenes.STEP_IDS),
                "what_ai_did": [dict(zip(["기능", "AI가 한 일", "AI 결과가 간 곳", "코드 검증", "정답 대조", "사람"], r))
                                for r in ctx.summary],
                "accuracy": {k: {"ok": v[0], "total": v[1]} for k, v in ctx.metrics.items()},
                "calls": [asdict(c) for c in ctx.llm.calls],
                "totals": {
                    "calls": len(ctx.llm.calls),
                    "input_tokens": sum(c.input_tokens for c in ctx.llm.calls),
                    "output_tokens": sum(c.output_tokens for c in ctx.llm.calls),
                    "seconds": round(sum(c.duration_s for c in ctx.llm.calls), 2),
                    "cost_usd": round(sum(c.cost_usd for c in ctx.llm.calls), 5),
                },
                "db": ctx.facts,
                "report_path": str(report.relative_to(ROOT)),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
    return ctx, report, summary


def main():
    ap = argparse.ArgumentParser(description="모두온 AI 시연 — AI가 정확히 무엇을 하는지 단계별로 보여 준다")
    ap.add_argument("--provider", choices=list(DEFAULT_MODELS), default="anthropic",
                    help="anthropic(Claude API) 또는 ollama(내 PC의 오픈소스 모델)")
    ap.add_argument("--model", help=f"기본: anthropic={DEFAULT_MODELS['anthropic']}, ollama={DEFAULT_MODELS['ollama']}")
    ap.add_argument("--mode", choices=["live", "replay", "mock"], help="기본: 자동 선택")
    ap.add_argument("--base-url", help="Ollama 주소 (기본: $OLLAMA_HOST 또는 http://localhost:11434)")
    ap.add_argument("--pause", action="store_true", help="단계마다 Enter를 기다림(발표용)")
    ap.add_argument("--quiet", action="store_true", help="화면 출력 없이 보고서·요약 JSON만 만든다(에이전트용)")
    ap.add_argument("--no-fallback", action="store_true", help="서버측 refusal fallback beta를 끔(Opus 5에서만 쓰임)")
    ap.add_argument("--no-tamper", action="store_true", help="'AI 오독 가상 상황' 검증 시연을 건너뜀")
    ap.add_argument("--width", type=int, help="터미널 출력 폭")
    args = ap.parse_args()

    model = args.model or DEFAULT_MODELS[args.provider]
    mode = args.mode or detect_mode(args.provider, model)
    try:
        _, report, summary = run(mode, model, provider=args.provider, base_url=args.base_url, pause=args.pause,
                                 quiet=args.quiet, width=args.width, use_fallback=not args.no_fallback,
                                 tamper=not args.no_tamper)
    except AIError as e:
        print(f"AI 호출 실패: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"\n전체 기록(AI에게 보낸 원문·응답 포함): {report.relative_to(ROOT)}")
    print(f"요약(JSON): {summary.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
