"""모두온 AI 콘솔 — 내 PC에서만 열리는 웹 화면(실행 프로그램).

    python -m moduon_demo.app.server              # http://127.0.0.1:8765 를 열고 브라우저를 띄운다
    python -m moduon_demo.app.server --port 9000 --no-browser

- 파이썬 표준 라이브러리만 쓴다(추가 설치 없음).
- 127.0.0.1에만 묶는다. 다른 사이트가 이 화면에 요청을 보내지 못하도록 Host 확인 + POST 전용 헤더를 요구한다.
"""
import argparse
import base64
import json
import os
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from ..llm import DEFAULT_MODELS, MODELS
from .service import Jobs, Session, UserError

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).resolve().parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml"}
CLAUDE_LABELS = {"claude-haiku-4-5": "Claude Haiku 4.5", "claude-sonnet-5": "Claude Sonnet 5", "claude-opus-5": "Claude Opus 5"}


class App:
    def __init__(self, root: Path = ROOT, base_url: str | None = None, prefer: str | None = None):
        self.root = root
        host = base_url or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
        self.ollama_url = (host if host.startswith("http") else f"http://{host}").rstrip("/")
        self.jobs = Jobs()
        self.session = Session(root, **self.initial_engine(prefer), base_url=self.ollama_url)

    def ollama_models(self) -> list[str] | None:
        """설치된 Ollama 모델 목록. 서버가 꺼져 있으면 None."""
        try:
            with urllib.request.urlopen(self.ollama_url + "/api/tags", timeout=1.5) as r:
                return sorted(m.get("name", "") for m in json.loads(r.read()).get("models", []))
        except Exception:
            return None

    def initial_engine(self, prefer: str | None) -> dict:
        if prefer == "mock":
            return {"provider": "ollama", "model": None, "mode": "mock"}
        models = self.ollama_models() or []
        want = DEFAULT_MODELS["ollama"]
        if want in models:
            return {"provider": "ollama", "model": want, "mode": "live"}
        if models:
            return {"provider": "ollama", "model": models[0], "mode": "live"}
        return {"provider": "ollama", "model": None, "mode": "mock"}

    def engines(self) -> dict:
        models = self.ollama_models()
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        return {"ollama": {"reachable": models is not None, "models": models or [], "url": self.ollama_url,
                           "default": DEFAULT_MODELS["ollama"]},
                "claude": {"has_key": has_key, "models": [[m, CLAUDE_LABELS.get(m, m)] for m in MODELS]}}

    # ───────────── 요청 처리: (상태 코드, 본문, 콘텐츠 형식)
    def handle(self, method: str, path: str, body: dict):
        s, jobs = self.session, self.jobs
        parts = [p for p in path.split("/") if p]
        if method == "GET":
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if parts[:1] == ["static"] and len(parts) == 2:
                return self._static(parts[1])
            if path == "/screen":
                return 200, s.screen_html(), TYPES[".html"]
            if path == "/api/state":
                return self._json({**s.state(), "engines": self.engines(),
                                   "job": jobs.get(jobs.running) if jobs.running else None})
            if parts[:2] == ["api", "source"] and len(parts) == 3:
                return self._json(s.source_detail(parts[2]))
            if path == "/api/matches":
                return self._json(s.matches())
            if path == "/api/review":
                return self._json(s.review())
            if path == "/api/history":
                return self._json({"calls": s.history, "audit": s.audit()})
            if parts[:2] == ["api", "job"] and len(parts) == 3:
                return self._json(jobs.get(parts[2]))
            return 404, "없는 주소입니다.", "text/plain; charset=utf-8"

        # POST
        if path == "/api/engine":
            if jobs.running and jobs.jobs[jobs.running]["status"] == "running":
                raise UserError("작업이 끝난 뒤에 엔진을 바꿔 주세요.")
            provider, mode, model = body.get("provider"), body.get("mode"), body.get("model")
            if provider == "anthropic" and mode == "live" and not self.engines()["claude"]["has_key"]:
                raise UserError("Claude를 쓰려면 터미널에서 ANTHROPIC_API_KEY를 설정한 뒤 프로그램을 다시 실행하세요.")
            s.set_engine(provider, model, mode)
            return self._json(s.engine_info())
        if path == "/api/reset":
            if jobs.running and jobs.jobs[jobs.running]["status"] == "running":
                raise UserError("작업이 끝난 뒤에 다시 시작해 주세요.")
            e = s.engine_info()
            self.session = Session(self.root, provider=e["provider"], model=e["model"], mode=e["mode"], base_url=self.ollama_url)
            return self._json({"ok": True})
        if path == "/api/process_all":
            return self._job(jobs.start("샘플 전산 모두 자동 처리", s.process_all))
        if parts[:2] == ["api", "process"] and len(parts) == 3:
            src = s.source_detail(parts[2])
            return self._job(jobs.start(f"{src['title']} 처리", s.process, parts[2]))
        if path == "/api/upload":
            if body.get("text") is not None:
                data = str(body["text"]).encode("utf-8")
            else:
                try:
                    data = base64.b64decode(body.get("data_b64") or "", validate=True)
                except Exception as e:
                    raise UserError("파일을 읽지 못했습니다.") from e
            name = Path(str(body.get("filename") or "붙여넣은 글.txt")).name[:80]
            sid = s.add_upload(body.get("kind"), body.get("partner"), name, data)
            return self._job(jobs.start(f"{name} 처리", s.process, sid), source_id=sid)
        if parts[:2] == ["api", "mapping"] and len(parts) == 3:
            cols, rows = body.get("columns") or [], body.get("header_rows") or []
            return self._job(jobs.start("엑셀 매핑 승인 → 규칙 파서", s.approve_mapping, parts[2], cols,
                                        [int(r) for r in rows]))
        if path == "/api/matches/ai":
            return self._job(jobs.start("상품명 AI 판정", s.ai_match_pending))
        if path == "/api/matches/confirm_all":
            return self._job(jobs.start("AI 제안대로 상품명 확정", self._confirm_all))
        if parts[:2] == ["api", "matches"] and len(parts) == 3:
            return self._job(jobs.start("상품명 확정", s.confirm_match, int(parts[2]), str(body.get("choice"))))
        if path == "/api/review/approve_safe":
            return self._json({"approved": s.approve_safe()})
        if path == "/api/review/approve_group":
            return self._json({"approved": s.approve_group(str(body.get("group")))})
        if path == "/api/review/explain":
            return self._job(jobs.start("이상 건 AI 설명", s.explain_pending))
        if path == "/api/review/manual":
            return self._json(s.add_manual(body.get("partner"), body.get("product_id"), body.get("plan_id") or "",
                                           body.get("field_code"), body.get("condition_key") or "base", body.get("value")))
        if parts[:2] == ["api", "review"] and len(parts) == 3:
            return self._json(s.decide(int(parts[2]), str(body.get("action")), body.get("value")))
        if path == "/api/promote":
            return self._json(s.promote())
        if path == "/api/nlq":
            return self._job(jobs.start("자연어 조회", s.nlq, str(body.get("question") or "")))
        return 404, "없는 주소입니다.", "text/plain; charset=utf-8"

    def _confirm_all(self, progress):
        n = 0
        for m in self.session.matches():
            sugg = m["suggestion"]
            if not sugg:
                continue
            choice = sugg["product_id"] or ("NEW" if sugg["decision"] in ("new_product_candidate", "no_match") else None)
            if choice:
                progress(f"{m['raw_name']} → {choice}")
                self.session.confirm_match(m["id"], choice, progress)
                n += 1
        return {"confirmed": n}

    def _static(self, name: str):
        p = (STATIC / name).resolve()
        if p.parent != STATIC or not p.is_file():
            return 404, "없는 파일입니다.", "text/plain; charset=utf-8"
        return 200, p.read_bytes(), TYPES.get(p.suffix, "application/octet-stream")

    @staticmethod
    def _json(obj, code=200):
        return code, json.dumps(obj, ensure_ascii=False, default=str), "application/json; charset=utf-8"

    def _job(self, jid: str, **extra):
        return self._json({"job_id": jid, **extra})


def make_handler(app: App, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "ModuonConsole/1"

        def log_message(self, fmt, *args):   # 터미널을 조용하게
            pass

        def _send(self, code, body, ctype):
            data = body if isinstance(body, bytes) else str(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _dispatch(self, method):
            if self.headers.get("Host") not in allowed_hosts:          # DNS 리바인딩 방지
                return self._send(403, "허용되지 않은 주소입니다.", "text/plain; charset=utf-8")
            body = {}
            if method == "POST":
                if self.headers.get("X-Moduon") != "1":                 # 다른 사이트의 요청(CSRF) 방지
                    return self._send(403, "허용되지 않은 요청입니다.", "text/plain; charset=utf-8")
                n = int(self.headers.get("Content-Length") or 0)
                if n > 16 * 1024 * 1024:
                    return self._send(413, *App._json({"error": "요청이 너무 큽니다."}, 413)[1:])
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except json.JSONDecodeError:
                    return self._send(*App._json({"error": "잘못된 요청입니다."}, 400))
            try:
                self._send(*app.handle(method, urlparse(self.path).path, body))
            except UserError as e:
                self._send(*App._json({"error": str(e)}, 400))
            except Exception as e:
                self._send(*App._json({"error": f"{type(e).__name__}: {e}"}, 500))

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")
    return Handler


def serve(port: int = 8765, open_browser: bool = True, base_url: str | None = None, prefer: str | None = None):
    url = f"http://127.0.0.1:{port}/"
    app = App(base_url=base_url, prefer=prefer)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
    except OSError:
        print(f"포트 {port}를 이미 쓰고 있습니다. 콘솔이 이미 켜져 있다면 브라우저에서 {url} 를 여세요.")
        print(f"다른 포트로 켜려면: ./run.sh app --port {port + 1}")
        if open_browser:
            webbrowser.open(url)
        return
    e = app.session.engine_info()
    print(f"모두온 AI 콘솔: {url}")
    print(f"AI 엔진: {e['label']}" + ("" if e["mode"] == "live" else
          "  (Ollama가 꺼져 있거나 모델이 없어 모의 응답으로 시작합니다. 화면 위쪽에서 바꿀 수 있습니다)"))
    print("끝내려면 이 창에서 Ctrl+C")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료했습니다.")
    finally:
        httpd.server_close()


def main():
    ap = argparse.ArgumentParser(description="모두온 AI 콘솔(웹 화면) 실행")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="브라우저를 자동으로 열지 않음")
    ap.add_argument("--base-url", help="Ollama 주소 (기본: $OLLAMA_HOST 또는 http://localhost:11434)")
    ap.add_argument("--mock", action="store_true", help="모의 응답으로 시작(Ollama가 있어도)")
    args = ap.parse_args()
    serve(args.port, not args.no_browser, args.base_url, "mock" if args.mock else None)


if __name__ == "__main__":
    main()
