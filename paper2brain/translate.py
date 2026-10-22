"""Chinese copies of the PDFs, by PDFMathTranslate (pdf2zh): layout, formulas and figures kept. It runs as its own
program from .venv-pdf2zh (its pinned dependencies clash with ours), one paper at a time across every library.
paper-zh.pdf sits next to paper.pdf; paper-zh.err records a failure so the background loop does not retry it."""
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from . import store

BIN = Path(__file__).resolve().parent.parent / ".venv-pdf2zh" / "bin" / "pdf2zh"
_busy = threading.Lock()


def zh_path(pid):
    return store.paper_dir(pid, create=False) / "paper-zh.pdf"


def status(p):
    """'done', 'failed' or 'pending' for a paper with a PDF; None without a PDF or with pdf2zh not installed."""
    if not p.get("pdf_path") or not BIN.exists():
        return None
    if zh_path(p["id"]).exists():
        return "done"
    return "failed" if zh_path(p["id"]).with_suffix(".err").exists() else "pending"


def translate(pid):
    src = Path(store.get_paper(pid, with_anchors=False)["pdf_path"])
    # Google answers the server with 429. An AI service, e.g. "deepseek" or "openai:<model>", reads its key from
    # pdf2zh_env ({"DEEPSEEK_API_KEY": ...} or OPENAI_BASE_URL / OPENAI_API_KEY)
    cfg = store.config()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            r = subprocess.run([str(BIN), str(src), "-li", "en", "-lo", "zh", "-s", cfg.get("pdf2zh_service", "bing"),
                                "-t", str(cfg.get("pdf2zh_threads", 4)), "-o", tmp],
                               capture_output=True, text=True, timeout=3600, env={**os.environ, **cfg.get("pdf2zh_env", {})})
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
    if not BIN.exists() or not _busy.acquire(blocking=False):
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
