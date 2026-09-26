"""터미널 출력 + 마크다운 보고서 동시 기록."""
import json
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table

ACTORS = {
    "AI":   ("bold magenta", "🤖 AI"),
    "코드": ("bold cyan", "⚙️  코드"),
    "사람": ("bold yellow", "🙋 사람"),
    "DB":   ("bold green", "🗄️  DB"),
    "계산": ("bold blue", "🧮 계산"),
    "정답": ("bold white", "📏 정답 대조"),
    "보안": ("bold red", "🛡️  보안"),
}


class Reporter:
    def __init__(self, pause: bool = False, width: int | None = None, file=None):
        self.console = Console(width=width, file=file)
        self.pause_enabled = pause
        self.md: list[str] = []

    # ---------- 구조 ----------
    def title(self, text: str, sub: str = ""):
        self.console.print(Panel(f"[bold]{escape(text)}[/bold]\n{escape(sub)}", border_style="bright_blue"))
        self.md += [f"# {text}", "", sub, ""]

    def scene(self, n: int, title: str, what_ai_does: str):
        self.console.print()
        self.console.print(Rule(f"[bold] 장면 {n}. {escape(title)} ", style="bright_blue"))
        self.console.print(Panel(escape(what_ai_does), title="이 장면에서 AI가 하는 일", border_style="magenta"))
        self.md += ["", f"## 장면 {n}. {title}", "", f"> **이 장면에서 AI가 하는 일:** {what_ai_does}", ""]

    def say(self, actor: str, text: str):
        style, label = ACTORS[actor]
        self.console.print(f"[{style}]{label}[/] {escape(text)}")
        self.md.append(f"- **{label.split(' ', 1)[-1].strip()}** {text}")

    def note(self, text: str):
        self.console.print(f"   [dim]{escape(text)}[/dim]")
        self.md.append(f"  - _{text}_")

    def warn(self, text: str):
        self.console.print(Panel(escape(text), border_style="red"))
        self.md += [""] + [f"> {'⚠️ ' if i == 0 else ''}{line}" for i, line in enumerate(text.splitlines())] + [""]

    # ---------- 데이터 ----------
    def json(self, title: str, obj, max_lines: int = 60):
        text = json.dumps(obj, ensure_ascii=False, indent=2)
        lines = text.splitlines()
        shown = "\n".join(lines[:max_lines]) + (f"\n… ({len(lines) - max_lines}줄 생략, 보고서에 전체 기록)"
                                                  if len(lines) > max_lines else "")
        self.console.print(Panel(Syntax(shown, "json", word_wrap=True, background_color="default"),
                                 title=escape(title), border_style="magenta"))
        self.md += ["", f"**{title}**", "", "```json", text, "```", ""]

    def text_block(self, title: str, text: str, lang: str = "text"):
        self.console.print(Panel(Syntax(text, lang, word_wrap=True, background_color="default"),
                                 title=escape(title), border_style="grey50"))
        self.md += ["", f"**{title}**", "", f"```{lang}", text, "```", ""]

    def table(self, title: str, columns: list[str], rows: list[list]):
        t = Table(title=escape(title), show_lines=False, title_justify="left")
        for c in columns:
            t.add_column(escape(c))
        for r in rows:
            t.add_row(*[escape("" if v is None else str(v)) for v in r])
        self.console.print(t)
        self.md += ["", f"**{title}**", "", "| " + " | ".join(columns) + " |",
                    "|" + "---|" * len(columns)]
        for r in rows:
            self.md.append("| " + " | ".join(("" if v is None else str(v)).replace("|", "\\|") for v in r) + " |")
        self.md.append("")

    def checks(self, title: str, checks):
        mark = {True: "✅ 통과", False: "❌ 실패", None: "➖ 해당 없음"}
        self.table(title, ["검사", "결과", "내용"], [[c.name, mark[c.ok], c.detail] for c in checks])

    def pause(self):
        if self.pause_enabled:
            self.console.input("[dim]  ⏎ Enter를 누르면 계속합니다…[/dim]")

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.md), encoding="utf-8")
