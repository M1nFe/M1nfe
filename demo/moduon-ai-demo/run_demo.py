"""모두온 AI 시연 실행기 (UI 없음, 터미널).

    python run_demo.py                          # 키가 있으면 live, 없으면 녹화본(replay), 녹화본도 없으면 mock
    python run_demo.py --mode live              # 실제 Claude 호출 + 녹화 (기본 모델 claude-haiku-4-5)
    python run_demo.py --model claude-opus-5    # 다른 모델로 실행
    python run_demo.py --mode replay --pause    # 녹화본으로 발표(단계마다 Enter)
    python run_demo.py --quiet                  # 화면 출력 없이 결과 파일만(OpenClaw 등 에이전트용)
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
from moduon_demo.llm import DEFAULT_MODEL, LLM, MODE_LABEL, MODELS, AIError
from moduon_demo.store import Store

ROOT = Path(__file__).resolve().parent
SCENES = (scenes.scene0_setup, scenes.scene1_excel, scenes.scene2_pdf, scenes.scene3_matching,
          scenes.scene4_anomaly, scenes.scene5_notice, scenes.scene6_confirm_and_calc,
          scenes.scene7_nlq, scenes.scene8_summary)


def detect_mode(model: str) -> str:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "live"
    if all((ROOT / "recordings" / model / f"{s}.json").exists() for s in scenes.STEP_IDS):
        return "replay"
    return "mock"


def run(mode: str, model: str, *, pause=False, quiet=False, width=None, use_fallback=True, tamper=True):
    """시연 전체를 실행하고 (ctx, 보고서 경로, 요약 JSON 경로)를 돌려준다."""
    rep = Reporter(pause=pause and not quiet, width=width, file=io.StringIO() if quiet else None)
    tag = f"{model}_{mode}"
    report = ROOT / "output" / f"demo_report_{tag}.md"
    summary = ROOT / "output" / f"demo_summary_{tag}.json"
    ctx = None
    try:
        llm = LLM(mode, ROOT, rep, model=model, use_fallback=use_fallback)
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
                "mode_label": MODE_LABEL[mode],
                "is_real_ai": mode in ("live", "replay"),
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
    ap.add_argument("--mode", choices=["live", "replay", "mock"], help="기본: 자동 선택")
    ap.add_argument("--model", choices=list(MODELS), default=DEFAULT_MODEL, help=f"기본: {DEFAULT_MODEL}")
    ap.add_argument("--pause", action="store_true", help="단계마다 Enter를 기다림(발표용)")
    ap.add_argument("--quiet", action="store_true", help="화면 출력 없이 보고서·요약 JSON만 만든다(에이전트용)")
    ap.add_argument("--no-fallback", action="store_true", help="서버측 refusal fallback beta를 끔(Opus 5에서만 쓰임)")
    ap.add_argument("--no-tamper", action="store_true", help="'AI 오독 가상 상황' 검증 시연을 건너뜀")
    ap.add_argument("--width", type=int, help="터미널 출력 폭")
    args = ap.parse_args()

    mode = args.mode or detect_mode(args.model)
    try:
        _, report, summary = run(mode, args.model, pause=args.pause, quiet=args.quiet, width=args.width,
                                 use_fallback=not args.no_fallback, tamper=not args.no_tamper)
    except AIError as e:
        print(f"AI 호출 실패: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"\n전체 기록(AI에게 보낸 원문·응답 포함): {report.relative_to(ROOT)}")
    print(f"요약(JSON): {summary.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
