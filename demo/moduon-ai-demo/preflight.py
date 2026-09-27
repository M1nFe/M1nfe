"""시연 전 점검 — `./run.sh check`

발표 직전에 한 번 실행한다. 항목마다 ✅/❌와, ❌이면 바로 입력할 명령을 보여 준다.

    python preflight.py                       # 기본 모델 qwen2.5:7b
    python preflight.py --model qwen2.5:3b
"""
import argparse
import importlib.util
import shutil
import sys

from rich.console import Console
from rich.table import Table

from moduon_demo.llm import DEFAULT_MODELS, LLM, AIError
from moduon_demo.console import Reporter


# 항목 이름 → 무엇에 필요한가. 발표(present)는 녹화본 재생이라 Ollama가 없어도 된다.
FOR_PRESENT = {"Python 3.10 이상", "필요한 패키지", "리허설 녹화본", "녹화본이 최신 코드와 맞음"}


def check(model: str, base_url: str | None = None) -> list[tuple[str, bool, str, str]]:
    """(항목, 통과 여부, 내용, 해결 명령) 목록."""
    out = []
    ok = sys.version_info >= (3, 10)
    out.append(("Python 3.10 이상", ok, sys.version.split()[0], "" if ok else "brew install python@3.12"))

    missing = [m for m in ("anthropic", "jsonschema", "openpyxl", "pdfplumber", "rich")
               if importlib.util.find_spec(m) is None]
    out.append(("필요한 패키지", not missing, "모두 설치됨" if not missing else f"없음: {', '.join(missing)}",
                "rm -rf .venv && ./run.sh check" if missing else ""))

    try:
        probe = LLM("replay", _root(), Reporter(file=_devnull()), model=model, provider="ollama", base_url=base_url)
        tags = probe._ollama("/api/tags")
        names = sorted(m.get("name", "") for m in tags.get("models", []))
        out.append(("Ollama 서버", True, probe.base_url, ""))
        want = model if ":" in model else f"{model}:latest"
        has = want in names
        out.append((f"모델 {model}", has, "받아 둠" if has else f"설치된 모델: {', '.join(names) or '없음'}",
                    "" if has else f"ollama pull {model}"))
    except AIError as e:
        out.append(("Ollama 서버", False, str(e)[:80], "새 터미널 창에서: ollama serve"))

    from run_demo import run   # 순환 import 방지: 여기서 가져온다
    try:
        ctx, _, _ = run("replay", model, provider="ollama", base_url=base_url, quiet=True)
        warns = ctx.rep.warnings
        acc = ", ".join(f"{k.split(' ', 1)[-1]} {v[0]}/{v[1]}" for k, v in ctx.metrics.items()
                        if not k.startswith("(비교)"))
        out.append(("리허설 녹화본", True, "있음", ""))
        out.append(("녹화본이 최신 코드와 맞음", not warns,
                    acc if not warns else f"다름 {len(warns)}건 (발표 화면에 경고가 뜸)",
                    "" if not warns else "./run.sh rehearse"))
    except AIError:
        out.append(("리허설 녹화본", False, "없음", "./run.sh rehearse"))
    except Exception as e:   # 녹화본은 있지만 재생 중 코드 오류 → 점검표에 보여 주고 끝까지 점검한다
        out.append(("리허설 녹화본", False, f"재생 중 오류: {type(e).__name__}: {e}"[:80],
                    "git pull 후 ./run.sh rehearse"))

    cols = shutil.get_terminal_size((80, 24)).columns
    out.append(("터미널 폭 100칸 이상", cols >= 100, f"현재 {cols}칸",
                "" if cols >= 100 else "터미널 창을 넓히거나 전체 화면(⌃⌘F)"))
    return out


def _root():
    from pathlib import Path
    return Path(__file__).resolve().parent


def _devnull():
    import io
    return io.StringIO()


def main():
    ap = argparse.ArgumentParser(description="시연 전 점검")
    ap.add_argument("--model", default=DEFAULT_MODELS["ollama"])
    ap.add_argument("--base-url")
    args = ap.parse_args()

    rows = check(args.model, args.base_url)
    con = Console()
    t = Table(title=f"시연 전 점검 — Ollama {args.model}", title_justify="left")
    for c in ("항목", "필요한 때", "상태", "내용", "해결 명령"):
        t.add_column(c)
    for name, ok, detail, fix in rows:
        need = "발표·리허설" if name in FOR_PRESENT else ("권장" if name.startswith("터미널") else "리허설")
        t.add_row(name, need, "✅" if ok else ("⚠️" if need == "권장" else "❌"), detail, fix)
    con.print(t)
    present_ok = all(ok for name, ok, _, _ in rows if name in FOR_PRESENT)
    rehearse_ok = all(ok for name, ok, _, _ in rows if name not in FOR_PRESENT and not name.startswith("터미널")
                      and not name.startswith("리허설") and not name.startswith("녹화본"))
    if present_ok:
        con.print("[bold green]발표 준비 완료.[/] [bold]./run.sh present[/]  (녹화본 재생, 단계마다 Enter"
                  + (". Ollama는 발표에 필요 없음)" if not rehearse_ok else ")"))
        return
    if rehearse_ok:
        con.print("[bold yellow]리허설이 필요합니다.[/] [bold]./run.sh rehearse[/] 실행 후 다시 ./run.sh check")
    else:
        con.print("[bold red]준비 안 됨.[/] ❌ 항목의 '해결 명령'을 위에서부터 차례로 실행한 뒤 다시 ./run.sh check")
    sys.exit(1)


if __name__ == "__main__":
    main()
