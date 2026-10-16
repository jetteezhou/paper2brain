"""Local web server: JSON API for the web UI plus static files. Same store and rules as the CLI."""
import base64
import json
import mimetypes
import re
import threading
import time
import traceback
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import cursor_api, inbox, pipeline, rules, store

WEB = Path(__file__).resolve().parent.parent / "web"
ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


def bg(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


class Reply:
    def __init__(self, path, mime=None):
        self.path, self.mime = Path(path), mime


# ---------- state ----------

@route("GET", "/api/state")
def state(q, body):
    agent = {**cursor_api.status(), "home": str(store.home()), "repo_dir": str(WEB.parent)}
    return {"counts": store.counts(), "topics": store.topic_tree(), "agent": agent}


@route("GET", "/api/search")
def search(q, body):
    return store.search(q.get("q", ""))


# ---------- captures ----------

@route("GET", "/api/captures")
def captures(q, body):
    return store.list_captures(q.get("view", "inbox"))


@route("POST", "/api/captures")
def capture_add(q, body):
    earlier = None if body.get("images") else store.same_capture(body.get("text"))
    if earlier:
        if (body.get("thought") or "").strip():
            store.add_thought(body["thought"], "user", capture_id=earlier)
        store.set_triage(earlier, "inbox")
        return {**store.get_capture(earlier), "duplicate": True}
    images = []
    for i, img in enumerate(body.get("images") or [], 1):
        mime = img.get("mime") or "image/png"
        name = img.get("name") or f"file-{i}{mimetypes.guess_extension(mime) or ''}"
        images.append((f"{i}-{Path(name).name}", base64.b64decode(img["data"]), mime))
    cap, _ = store.add_capture(str(uuid.uuid4()), body.get("text", ""), "web", body.get("thought"), images)
    bg(pipeline.process, cap["id"])
    return cap


@route("GET", "/api/captures/([^/]+)")
def capture_get(q, body, cid):
    cap = store.get_capture(cid)
    if not cap:
        raise rules.Rejected("这条捕获不存在或已撤销")
    return cap


@route("DELETE", "/api/captures/([^/]+)")
def capture_delete(q, body, cid):
    store.delete_capture(cid)
    return {"ok": True}


@route("POST", "/api/captures/([^/]+)/triage")
def capture_triage(q, body, cid):
    store.set_triage(cid, body["triage"])
    return {"ok": True}


@route("POST", "/api/captures/([^/]+)/link")
def capture_link(q, body, cid):
    pid = pipeline.resolve_paper(body["paper"])["id"]
    store.link(cid, pid, "user", evidence="用户确认")
    store.update_capture(cid, status="mapped")
    if cursor_api.status()["enabled"] and not (store.get_paper(pid) or {}).get("card"):
        bg(cursor_api.process_capture, cid)
    return store.get_capture(cid)


@route("POST", "/api/captures/([^/]+)/pdf")
def capture_pdf(q, body, cid):
    data = base64.b64decode(body["data"])
    if not data.startswith(b"%PDF"):
        raise rules.Rejected("这不是 PDF 文件")
    pid = pipeline.paper_from_pdf(data)["id"]
    store.link(cid, pid, "user", evidence="用户上传的 PDF")
    store.update_capture(cid, status="mapped")
    if cursor_api.status()["enabled"] and not (store.get_paper(pid) or {}).get("card"):
        bg(cursor_api.process_capture, cid)
    return store.get_capture(cid)


@route("POST", "/api/captures/([^/]+)/unlink")
def capture_unlink(q, body, cid):
    store.unlink(cid, body["paper"], "user")
    if not any(p["state"] == "confirmed" for p in store.get_capture(cid)["papers"]):
        store.update_capture(cid, status="unmapped")
    return store.get_capture(cid)


@route("POST", "/api/captures/([^/]+)/reprocess")
def capture_reprocess(q, body, cid):
    bg(pipeline.process, cid, body.get("agent", True))
    return {"ok": True}


@route("GET", "/api/attachments/([^/]+)")
def attachment(q, body, aid):
    a = store.attachment(aid)
    if not a:
        raise rules.Rejected("附件不存在")
    return Reply(a["path"], a["mime"])


# ---------- papers ----------

@route("GET", "/api/papers")
def papers(q, body):
    return store.list_papers("" if q.get("unsorted") else q.get("topic"))


def _paper(pid):
    p = store.get_paper(pid)
    if not p:
        raise rules.Rejected("论文不在库里")
    return p


@route("GET", "/api/papers/([^/]+)")
def paper_get(q, body, pid):
    return _paper(pid)


@route("GET", "/api/papers/([^/]+)/pdf")
def paper_pdf(q, body, pid):
    p = _paper(pid)
    if not p["pdf_path"]:
        raise rules.Rejected("还没有 PDF")
    return Reply(p["pdf_path"], "application/pdf")


@route("GET", "/api/papers/([^/]+)/pages")
def paper_pages(q, body, pid):
    return store.pages(pid)


@route("POST", "/api/papers/([^/]+)/pdf")
def paper_upload_pdf(q, body, pid):
    return pipeline.attach_pdf(pid, base64.b64decode(body["data"]))


@route("POST", "/api/papers/([^/]+)/topics")
def paper_topics(q, body, pid):
    store.set_topics(pid, body.get("topics") or [], "user")
    return _paper(pid)


@route("POST", "/api/papers/([^/]+)/triage")
def paper_triage(q, body, pid):
    for c in _paper(pid)["captures"]:
        store.set_triage(c["id"], body["triage"])
    return {"ok": True}


@route("POST", "/api/papers/([^/]+)/deepread")
def paper_deepread(q, body, pid):
    return {"run_id": cursor_api.deep_read(pid)}


# ---------- thoughts, notes, discussion ----------

@route("GET", "/api/thoughts")
def thoughts(q, body):
    return store.list_thoughts()


@route("POST", "/api/thoughts")
def thought_add(q, body):
    return {"id": store.add_thought(body.get("text"), "user", body.get("capture_id"), body.get("paper_id"))}


@route("POST", "/api/notes")
def note_add(q, body):
    return {"id": store.add_note(body.get("text"), "user", body.get("paper_id"), body.get("topic"), body.get("thread_id"))}


@route("GET", "/api/threads")
def thread_get(q, body):
    return store.thread_for(q.get("scope"), q.get("id"))


@route("GET", "/api/messages/([^/]+)")
def message_get(q, body, mid):
    return store.message(mid)


# ---------- research ----------

@route("GET", "/api/research")
def research_list(q, body):
    return store.list_research("" if q.get("unsorted") else q.get("topic"))


@route("POST", "/api/research")
def research_add(q, body):
    links = [(x["kind"], x["ref"]) for x in body.get("links") or []]
    return store.get_research(store.add_research(body.get("title"), "user", body.get("summary"), body.get("topics") or [], links))


def _research(rid):
    x = store.get_research(rid)
    if not x:
        raise rules.Rejected("这个研究不存在或已删除")
    return x


@route("GET", "/api/research/([^/]+)")
def research_get(q, body, rid):
    return _research(rid)


@route("POST", "/api/research/([^/]+)")
def research_update(q, body, rid):
    store.update_research(rid, body.get("base_rev"), body.get("title"), body.get("summary"), body.get("body"))
    return _research(rid)


@route("DELETE", "/api/research/([^/]+)")
def research_delete(q, body, rid):
    store.delete_research(rid)
    return {"ok": True}


@route("POST", "/api/research/([^/]+)/establish")
def research_establish(q, body, rid):
    store.establish(rid, "user", body.get("established", True))
    return _research(rid)


@route("POST", "/api/research/([^/]+)/proposal")
def research_proposal(q, body, rid):
    if body.get("action") == "discard":
        store.discard_proposal(rid)
    else:
        store.accept_proposal(rid, body.get("base_rev"))
        if body.get("establish"):
            store.establish(rid, "user")
    return _research(rid)


@route("POST", "/api/research/([^/]+)/topics")
def research_topics(q, body, rid):
    store.set_research_topics(rid, body.get("topics") or [])
    return _research(rid)


@route("POST", "/api/research/([^/]+)/links")
def research_link(q, body, rid):
    store.link_research(rid, body["kind"], body["ref"], "user")
    return _research(rid)


@route("POST", "/api/research/([^/]+)/unlink")
def research_unlink(q, body, rid):
    store.unlink_research(rid, body["kind"], body["ref"])
    return _research(rid)


@route("POST", "/api/research/([^/]+)/draft")
def research_draft(q, body, rid):
    return {"run_id": cursor_api.draft_research(rid)}


@route("POST", "/api/threads")
def thread_new(q, body):
    store.new_thread(body["scope"])
    return store.thread_for(body["scope"])


@route("POST", "/api/chat")
def chat(q, body):
    text = (body.get("text") or "").strip()
    if not text:
        raise rules.Rejected("问题不能为空")
    return {"run_id": cursor_api.discuss(body["scope"], text, body.get("thread_id"))}


@route("GET", "/api/runs/([^/]+)")
def run_get(q, body, rid):
    return store.get_run(rid)


@route("POST", "/api/runs/([^/]+)/cancel")
def run_cancel(q, body, rid):
    r = store.get_run(rid)
    cursor_api.cancel(r["agent_id"], rid)
    return {"ok": True}


# ---------- plumbing ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, data, mime="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode())

    def _file(self, path, mime=None):
        if not path.is_file():
            return self._json(404, {"error": "文件不存在"})
        self._send(200, path.read_bytes(), mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    def _dispatch(self, method):
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        if method == "GET" and not path.startswith("/api/"):
            target = (WEB / path.lstrip("/")).resolve() if path != "/" else WEB / "index.html"
            if WEB.resolve() not in target.parents and target != WEB / "index.html":
                return self._json(404, {"error": "not found"})
            return self._file(target)
        q = dict(urllib.parse.parse_qsl(url.query))
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except json.JSONDecodeError:
            return self._json(400, {"error": "请求体不是合法 JSON"})
        for m, pattern, fn in ROUTES:
            # match before unquoting: paper IDs such as DOIs contain "/" (sent as %2F)
            match = pattern.match(url.path) if m == method else None
            if match:
                try:
                    out = fn(q, body, *map(urllib.parse.unquote, match.groups()))
                except rules.Rejected as e:
                    return self._json(400, {"error": str(e)})
                except cursor_api.AgentError as e:
                    return self._json(502, {"error": str(e)})
                except Exception as e:
                    traceback.print_exc()
                    return self._json(500, {"error": f"{type(e).__name__}: {e}"})
                return self._file(out.path, out.mime) if isinstance(out, Reply) else self._json(200, out)
        self._json(404, {"error": "not found"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")


def _poll_inbox():
    while True:
        try:
            inbox.drain()
        except Exception:
            traceback.print_exc()
        time.sleep(30)


def serve(host="127.0.0.1", port=8765):
    store.counts()
    cursor_api.resume_watchers()
    bg(_poll_inbox)
    print(f"paper2brain 已启动：http://{host}:{port}  数据目录：{store.home()}")
    a = cursor_api.status()
    print("Agent：" + (f"已配置，本机 worker = {a['machine']}" if a["enabled"] else f"未配置（缺 {', '.join(a['missing'])}），只做规则预处理"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()
