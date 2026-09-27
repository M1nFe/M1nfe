"""모델 비교 — 같은 시연을 여러 모델로 돌려 정답 대조·토큰·시간·비용을 나란히 본다.

    python compare_models.py                                          # Claude Haiku 4.5 vs Opus 5 (API 키 필요)
    python compare_models.py --models ollama:qwen2.5:7b ollama:gemma3:12b   # 로컬 오픈소스 모델끼리 (키 불필요)
    python compare_models.py --models ollama:qwen2.5:7b claude-haiku-4-5    # 로컬 vs Claude
    python compare_models.py --mode replay                            # 녹화본끼리 비교

모델 표기: Claude는 모델 이름 그대로, Ollama는 'ollama:<모델>' (예: ollama:qwen2.5:7b)
결과: 화면 표 + output/model_comparison.md
"""
import argparse
import sys

from rich.console import Console
from rich.table import Table

from moduon_demo.llm import AIError
from run_demo import ROOT, run


def parse_spec(spec: str) -> tuple[str, str]:
    return ("ollama", spec.split(":", 1)[1]) if spec.startswith("ollama:") else ("anthropic", spec)


def main():
    ap = argparse.ArgumentParser(description="같은 시연을 여러 모델로 돌려 비교")
    ap.add_argument("--models", nargs="+", default=["claude-haiku-4-5", "claude-opus-5"],
                    help="예: claude-haiku-4-5 claude-opus-5 ollama:qwen2.5:7b")
    ap.add_argument("--mode", choices=["live", "replay"], default="live")
    args = ap.parse_args()

    con = Console()
    results = {}
    for spec in args.models:
        provider, model = parse_spec(spec)
        con.print(f"[bold]▶ {spec} ({args.mode}) 실행 중…[/bold]")
        try:
            ctx, _, _ = run(args.mode, model, provider=provider, quiet=True)
        except AIError as e:
            con.print(f"[red]  실패: {e}[/red]")
            continue
        results[spec] = ctx
    if not results:
        sys.exit(1)

    features = list(next(iter(results.values())).metrics)
    t = Table(title=f"모델 비교 ({args.mode}) — 정답 대조는 시연용 소규모 정답표 기준")
    t.add_column("항목")
    for m in results:
        t.add_column(m)
    md = [f"# 모델 비교 ({args.mode})", "", "정답 대조는 시연용 소규모 정답표(`data/answer_key.json`) 기준입니다. "
          "표본이 작으므로 운영 결정은 실제 골든셋으로 다시 확인해야 합니다.", "",
          "| 항목 | " + " | ".join(results) + " |", "|---|" + "---|" * len(results)]

    def row(label, values):
        t.add_row(label, *values)
        md.append(f"| {label} | " + " | ".join(values) + " |")

    for f in features:
        row(f, [(lambda v: f"{v[0]}/{v[1]}" if v else "-")(c.metrics.get(f)) for c in results.values()])
    tot = {m: c.llm.calls for m, c in results.items()}
    row("AI 호출 수", [str(len(v)) for v in tot.values()])
    row("입력 토큰", [f"{sum(x.input_tokens for x in v):,}" for v in tot.values()])
    row("출력 토큰", [f"{sum(x.output_tokens for x in v):,}" for v in tot.values()])
    row("AI 응답 시간 합계(초)", [f"{sum(x.duration_s for x in v):.1f}" for v in tot.values()])
    row("API 비용(USD, 추정 · 로컬은 0)", [f"{sum(x.cost_usd for x in v):.4f}" for v in tot.values()])
    row("확정 반영 / 사람이 고친 값", [f"{c.facts.get('confirmed')} / {c.facts.get('human_edited')}"
                                  for c in results.values()])
    con.print(t)
    out = ROOT / "output" / "model_comparison.md"
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    con.print(f"[dim]저장: {out.relative_to(ROOT)}[/dim]")


if __name__ == "__main__":
    main()
