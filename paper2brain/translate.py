"""Chinese copies of the PDFs, by PDFMathTranslate (pdf2zh): layout, formulas and figures kept. It runs as its own
program from .venv-pdf2zh (its pinned dependencies clash with ours), one paper at a time across every library.
paper-zh.pdf sits next to paper.pdf; paper-zh.err records a failure so the background loop does not retry it.

The engine (config.json "pdf2zh_service") is by default the quick search API (Gemini), which pdf2zh reaches through a
bridge on 127.0.0.1 that hands every paragraph to cursor_api._chat: on the shared server that is the member's Mac,
whose key and proxy never reach the server (and Gemini is region-locked there anyway)."""
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager, nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import cursor_api, rules, store

BIN = Path(__file__).resolve().parent.parent / ".venv-pdf2zh" / "bin" / "pdf2zh"
ENGINES = {"search": "快速搜索接口（和快速回答同一个模型）", "bing": "Bing（免费）", "google": "Google（免费，服务器上常被限流）"}
_busy = threading.Lock()


def engine():
    e = store.config().get("pdf2zh_service")
    return e if e in ENGINES else "search"


def set_engine(e):
    if e not in ENGINES:
        raise rules.Rejected(f"没有这个翻译引擎：{e}")
    cursor_api._config_write(pdf2zh_service=e)
    for d in (store.home() / "papers").glob("*"):  # failures under the old engine get another try
        (d / "paper-zh.err").unlink(missing_ok=True)


def zh_path(pid):
    return store.paper_dir(pid, create=False) / "paper-zh.pdf"


def status(p):
    """'done', 'failed' or 'pending' for a paper with a PDF; None without a PDF or with pdf2zh not installed."""
    if not p.get("pdf_path") or not BIN.exists():
        return None
    if zh_path(p["id"]).exists():
        return "done"
    return "failed" if zh_path(p["id"]).with_suffix(".err").exists() else "pending"


@contextmanager
def _bridge(api):
    """An OpenAI-compatible /chat/completions on a free local port, for this one pdf2zh run; yields pdf2zh's env."""
    key, home = secrets.token_hex(16), store.home()

    class Bridge(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.headers.get("Authorization") != f"Bearer {key}":
                return self.send_error(401)
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            prompt = "\n\n".join(m["content"] for m in body.get("messages") or [] if isinstance(m.get("content"), str))
            try:
                with store.at_home(home):
                    text = cursor_api._chat(api, prompt)
                code, out = 200, {"id": "p2b", "object": "chat.completion", "created": int(time.time()), "model": body.get("model", ""),
                                  "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}
            except cursor_api.AgentError as e:  # 5xx: the client retries twice, then pdf2zh keeps that paragraph in English
                code, out = 502, {"error": {"message": str(e)}}
            data = json.dumps(out, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield {"OPENAI_BASE_URL": f"http://127.0.0.1:{httpd.server_address[1]}/v1", "OPENAI_API_KEY": key, "OPENAI_MODEL": "quick"}
    finally:
        httpd.shutdown()
        httpd.server_close()


def translate(pid):
    src = Path(store.get_paper(pid, with_anchors=False)["pdf_path"])
    e, api = engine(), cursor_api.search_api()
    if e == "search" and not api:
        return  # the quick search API is off (or the Mac that calls it is): the paper stays pending
    with tempfile.TemporaryDirectory() as tmp, (_bridge(api) if e == "search" else nullcontext({})) as env:
        try:
            r = subprocess.run([str(BIN), str(src), "-li", "en", "-lo", "zh", "-s", "openai" if e == "search" else e, "-t", "4", "-o", tmp],
                               capture_output=True, text=True, timeout=3600, env={**os.environ, **env})
            err = None if r.returncode == 0 else (r.stderr or r.stdout)[-2000:]
        except subprocess.TimeoutExpired:
            err = "pdf2zh 超过一小时没跑完"
        out = Path(tmp) / f"{src.stem}-mono.pdf"
        if err or not out.exists():
            zh_path(pid).with_suffix(".err").write_text(err or "pdf2zh 没有输出文件")
        else:
            shutil.move(out, zh_path(pid))


def backfill():
    """Start translating the next paper that needs it, unless one is already running (background loop)."""
    if not BIN.exists() or (engine() == "search" and not cursor_api.search_api()) or not _busy.acquire(blocking=False):
        return
    pid = next((p["id"] for p in store.list_papers() if status(store.get_paper(p["id"], with_anchors=False)) == "pending"), None)
    if not pid:
        return _busy.release()
    store.spawn(_run, pid)


def _run(pid):
    try:
        translate(pid)
    finally:
        _busy.release()
