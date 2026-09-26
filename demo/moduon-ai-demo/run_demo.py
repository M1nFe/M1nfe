"""모두온 AI 시연 실행기 (UI 없음, 터미널).

    python run_demo.py                 # 키가 있으면 live, 없으면 녹화본(replay), 녹화본도 없으면 mock
    python run_demo.py --mode live     # 실제 Claude 호출 + 녹화
    python run_demo.py --mode replay   # 녹화된 실제 응답으로 재생(키 없이 시연)
    python run_demo.py --mode mock     # 모의 응답(실제 AI 아님)
    python run_demo.py --pause         # 발표용: 단계마다 Enter를 기다림
"""
import argparse
import json
import os
import sys
from pathlib import Path

from moduon_demo import scenes
from moduon_demo.console import Reporter
from moduon_demo.llm import LLM, AIError
from moduon_demo.store import Store

ROOT = Path(__file__).resolve().parent


def detect_mode() -> str:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return "live"
    if all((ROOT / "recordings" / f"{s}.json").exists() for s in scenes.STEP_IDS):
        return "replay"
    return "mock"


def main():
    ap = argparse.ArgumentParser(description="모두온 AI 시연 — AI가 정확히 무엇을 하는지 단계별로 보여 준다")
    ap.add_argument("--mode", choices=["live", "replay", "mock"], help="기본: 자동 선택")
    ap.add_argument("--pause", action="store_true", help="단계마다 Enter를 기다림(발표용)")
    ap.add_argument("--no-fallback", action="store_true", help="서버측 refusal fallback beta를 끔")
    ap.add_argument("--no-tamper", action="store_true", help="'AI 오독 가상 상황' 검증 시연을 건너뜀")
    ap.add_argument("--width", type=int, help="터미널 출력 폭")
    args = ap.parse_args()

    mode = args.mode or detect_mode()
    rep = Reporter(pause=args.pause, width=args.width)
    report = ROOT / "output" / f"demo_report_{mode}.md"
    try:
        llm = LLM(mode, ROOT, rep, use_fallback=not args.no_fallback)
        ctx = scenes.Ctx(root=ROOT, store=Store(), llm=llm, rep=rep,
                         key=json.loads((ROOT / "data/answer_key.json").read_text(encoding="utf-8")),
                         tamper=not args.no_tamper)
        for scene in (scenes.scene0_setup, scenes.scene1_excel, scenes.scene2_pdf, scenes.scene3_matching,
                      scenes.scene4_anomaly, scenes.scene5_notice, scenes.scene6_confirm_and_calc,
                      scenes.scene7_nlq, scenes.scene8_summary):
            scene(ctx)
    except AIError as e:
        rep.warn(f"AI 호출 실패: {e}")
        sys.exit(1)
    finally:
        rep.save(report)
        rep.console.print(f"\n[dim]전체 기록(AI에게 보낸 원문·응답 포함): {report.relative_to(ROOT)}[/dim]")


if __name__ == "__main__":
    main()
