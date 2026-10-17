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

from . import cursor_api, export, inbox, pipeline, repos, rules, store, structure

WEB = Path(__file__).resolve().parent.parent / "web"
mimetypes.add_type("text/javascript", ".mjs")  # ES modules (PDF.js) must not be served as octet-stream
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
    return {"counts": store.counts(), "topics": store.topic_tree(), "topic_proposals": store.topic_proposals(), "agent": agent,
            "activity": store.activity()}


@route("POST", "/api/topics")
def topic_add(q, body):
    return {"path": store.add_topic(body.get("name"), body.get("parent"), "user")}


@route("POST", "/api/topics/edit")
def topic_edit(q, body):
    return {"path": store.edit_topic(body.get("path"), body.get("name"), body.get("parent"))}


@route("POST", "/api/topics/decide")
def topic_decide(q, body):
    store.decide_topic(body.get("path"), body.get("accept") is True)
    return {"ok": True}


@route("GET", r"/api/activity/(\d{4}-\d{2}-\d{2})")
def activity_day(q, body, day):
    return store.activity_day(day)


@route("GET", "/api/search")
def search(q, body):
    return store.search(q.get("q", ""))


# ---------- captures ----------

@route("GET", "/api/captures")
def captures(q, body):
    return store.list_captures(q.get("view", "inbox"))


@route("POST", "/api/captures")
def capture_add(q, body):
    images = []
    for i, img in enumerate(body.get("images") or [], 1):
        mime = img.get("mime") or "image/png"
        name = img.get("name") or f"file-{i}{mimetypes.guess_extension(mime) or ''}"
        images.append((f"{i}-{Path(name).name}", base64.b64decode(img["data"]), mime))
    got = pipeline.receive(str(uuid.uuid4()), body.get("text", ""), "web", body.get("thought"), images)
    if got["kind"] == "thought":
        return {"thread_id": got["thread_id"]}
    if got["kind"] == "duplicate":
        return {**got["capture"], "duplicate": True}
    bg(pipeline.process, got["capture"]["id"])
    return got["capture"]


@route("POST", "/api/find")
def find(q, body):
    """"帮我找……的论文": the agent searches, adds what it picks to the library under one capture, and opens a
    discussion of the set. Without the agent, the request is an ordinary capture and rules search arXiv by keyword."""
    text = (body.get("text") or "").strip()
    if not text:
        raise rules.Rejected("说说想找什么样的论文")
    if not cursor_api.status()["enabled"]:
        cap, _ = store.add_capture(str(uuid.uuid4()), text, "web")
        bg(pipeline.process, cap["id"], False)
        return {**cap, "agent": False}
    cap, _ = store.add_capture(str(uuid.uuid4()), text, "find")
    store.update_capture(cap["id"], status="finding", channel="find", title="找论文：" + pipeline.short_title(text, 60))
    cursor_api.find_papers(cap["id"])
    return {**store.get_capture(cap["id"]), "agent": True}


@route("GET", "/api/captures/([^/]+)")
def capture_get(q, body, cid):
    cap = store.get_capture(cid)
    if not cap:
        raise rules.Rejected("这条捕获不存在或已撤销")
    return cap


@route("GET", "/api/captures/([^/]+)/removal")
def capture_removal(q, body, cid):
    return store.capture_removal(cid)


@route("DELETE", "/api/captures/([^/]+)")
def capture_delete(q, body, cid):
    for r in (store.get_capture(cid) or {}).get("runs", []):
        if r["status"] in ("CREATING", "RUNNING") and not r["id"].startswith("pending-"):
            try:
                cursor_api.cancel(r["agent_id"], r["id"])
            except cursor_api.AgentError:
                pass  # finishing against a deleted capture only fails its own writes
    store.delete_capture(cid)
    return {"ok": True}


@route("POST", "/api/captures/([^/]+)/triage")
def capture_triage(q, body, cid):
    store.set_triage(cid, body["triage"])
    _queue_deepread(body["triage"])
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
    p = _paper(pid)
    s = structure.get(pid)
    return {**p, "figures": s["figures"], "key_figures": structure.key_figures(s, p["card"]), "outline": s["outline"],
            "key_sections": s["key"]}


@route("GET", r"/api/papers/([^/]+)/figures/(\d+)")
def paper_figure(q, body, pid, n):
    return Reply(store.paper_dir(pid, create=False) / "figures" / f"{int(n)}.png", "image/png")


@route("POST", "/api/papers/([^/]+)/card")
def paper_card(q, body, pid):
    return {"run_id": cursor_api.write_card(_paper(pid)["id"])}


@route("GET", "/api/papers/([^/]+)/pdf")
def paper_pdf(q, body, pid):
    p = _paper(pid)
    if not p["pdf_path"]:
        raise rules.Rejected("还没有 PDF")
    return Reply(p["pdf_path"], "application/pdf")


@route("POST", "/api/papers/([^/]+)/read")
def paper_read(q, body, pid):
    store.mark_read(_paper(pid)["id"])
    return {"ok": True}


@route("GET", "/api/papers/([^/]+)/pages")
def paper_pages(q, body, pid):
    return store.pages(pid)


@route("POST", "/api/papers/([^/]+)/pdf")
def paper_upload_pdf(q, body, pid):
    return pipeline.attach_pdf(pid, base64.b64decode(body["data"]))


@route("POST", "/api/papers/([^/]+)/anchors")
def anchor_add(q, body, pid):
    aid = store.add_anchor(pid, body.get("surface"), body.get("quote"), "user", body.get("page"), body.get("section"))
    scope = f"anchor:{aid}"
    text, error = (body.get("text") or "").strip(), None
    if text:
        try:
            cursor_api.discuss(scope, text)
        except cursor_api.AgentError as e:
            error = str(e)
    return {"anchor": store.get_anchor(aid), "thread": store.thread_for(scope), "error": error}


@route("DELETE", "/api/anchors/([^/]+)")
def anchor_delete(q, body, aid):
    store.delete_anchor(aid)
    return {"ok": True}


@route("POST", "/api/papers/([^/]+)/topics")
def paper_topics(q, body, pid):
    store.set_topics(pid, body.get("topics") or [], "user")
    return _paper(pid)


@route("POST", "/api/papers/([^/]+)/triage")
def paper_triage(q, body, pid):
    for c in _paper(pid)["captures"]:
        store.set_triage(c["id"], body["triage"])
    _queue_deepread(body["triage"])
    return {"ok": True}


def _queue_deepread(triage):
    if triage == "deep":
        try:
            cursor_api.deepread_pending()
        except cursor_api.AgentError:
            traceback.print_exc()


@route("POST", "/api/papers/([^/]+)/deepread")
def paper_deepread(q, body, pid):
    return {"run_id": cursor_api.deep_read(pid)}


# ---------- notes, discussion ----------

@route("POST", "/api/notes")
def note_add(q, body):
    return {"id": store.add_note(body.get("text"), "user", body.get("paper_id"), body.get("topic"), body.get("thread_id"))}


@route("GET", "/api/threads")
def thread_get(q, body):
    return store.thread_for(q.get("scope"), q.get("id"))


@route("GET", "/api/threads/recent")
def threads_recent(q, body):
    return store.recent_threads()


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


@route("POST", "/api/research/(?!start$)([^/]+)")
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
        store.accept_proposal(rid, body.get("base_rev"), body.get("fields"))
        if body.get("establish"):
            store.establish(rid, "user")
    return _research(rid)


@route("POST", "/api/research/([^/]+)/topics")
def research_topics(q, body, rid):
    store.set_research_topics(rid, body.get("topics") or [])
    return _research(rid)


@route("POST", "/api/research/([^/]+)/links")
def research_link(q, body, rid):
    store.link_research(rid, body["kind"], body["ref"], "user", body.get("role"))
    return _research(rid)


@route("POST", "/api/research/([^/]+)/find")
def research_find(q, body, rid):
    """ResearchRabbit-style: the research's papers are the seeds; what the user turned down last time is avoided.
    The search is a find capture kept among the research's materials, so each round stays visible."""
    x = _research(rid)
    if not any(l["kind"] == "paper" and not l["missing"] for l in x["links"]):
        raise rules.Rejected("素材里还没有论文，先关联一两篇作为种子")
    if not cursor_api.status()["enabled"]:
        raise rules.Rejected("需要先连接 agent")
    text = (body.get("text") or "").strip() or f"以研究「{x['title']}」素材里的论文为种子，找更多对口的论文"
    cap, _ = store.add_capture(str(uuid.uuid4()), text, "find")
    store.update_capture(cap["id"], status="finding", channel="find", title="找论文：" + pipeline.short_title(text, 60))
    store.link_research(rid, "capture", cap["id"], "user")
    cursor_api.find_papers(cap["id"], rid)
    return store.get_capture(cap["id"])


@route("POST", "/api/research/([^/]+)/unlink")
def research_unlink(q, body, rid):
    store.unlink_research(rid, body["kind"], body["ref"])
    return _research(rid)


@route("POST", "/api/research/([^/]+)/draft")
def research_draft(q, body, rid):
    return {"run_id": cursor_api.draft_research(rid)}


# ---------- research: code repos (read-only), experiments, writing ----------

def _sync_repo(repo_id, fresh):
    repo = store.get_repo(repo_id)
    try:
        sha = repos.clone(repo["url"], repo["path"]) if fresh else repos.pull(repo["path"])
        store.update_repo(repo_id, status="ready", head=sha, pulled_at=store.now(), error=None)
    except Exception as e:
        store.update_repo(repo_id, status="error" if fresh else "ready", error=str(e)[:400])


@route("POST", "/api/research/start")
def research_start(q, body):
    files = [(f.get("name") or "file", f.get("mime") or "application/octet-stream", base64.b64decode(f["data"])) for f in body.get("files") or []]
    links = [(x["kind"], x["ref"]) for x in body.get("links") or []]
    rid, images = pipeline.start_research(body.get("title"), body.get("text"), body.get("topics") or [], links, files)

    def clone(repo_id):
        store.update_repo(repo_id, status="cloning", error=None)
        bg(_sync_repo, repo_id, True)

    bg(pipeline.finish_start, rid, body.get("text") or "", body.get("repos") or [], images, clone)
    return {"id": rid}


@route("GET", "/api/docs/([^/]+)")
def doc_file(q, body, did):
    d = store.get_doc(did)
    if not d:
        raise rules.Rejected("附件不存在")
    return Reply(d["path"], d["mime"])


@route("POST", "/api/research/([^/]+)/repos")
def research_repo_add(q, body, rid):
    url = repos.normalize(body.get("url") or "")
    repo_id, _ = store.add_repo(url, repos.local_dir(store.home(), url))
    store.link_repo(rid, repo_id, body.get("role") or "baseline")
    if store.get_repo(repo_id)["status"] != "ready":
        store.update_repo(repo_id, status="cloning", error=None)
        bg(_sync_repo, repo_id, True)
    return _research(rid)


@route("POST", "/api/research/([^/]+)/repos/([^/]+)/unlink")
def research_repo_unlink(q, body, rid, repo_id):
    store.unlink_repo(rid, repo_id)
    return _research(rid)


@route("GET", "/api/repos/([^/]+)")
def repo_get(q, body, repo_id):
    repo = store.get_repo(repo_id)
    if not repo:
        raise rules.Rejected("仓库不存在")
    return {**repo, "commits": repos.log(repo["path"], 10) if repo["status"] == "ready" else []}


@route("POST", "/api/repos/([^/]+)/pull")
def repo_pull(q, body, repo_id):
    bg(_sync_repo, repo_id, store.get_repo(repo_id)["status"] == "error")
    return {"ok": True}


@route("POST", "/api/research/([^/]+)/codemap")
def research_codemap(q, body, rid):
    return {"run_id": cursor_api.code_map(rid, body["repo_id"], body["paper_id"])}


@route("POST", "/api/research/([^/]+)/experiments")
def experiment_add(q, body, rid):
    eid = store.add_experiment(rid, body.get("raw"), "user", body.get("title"))
    if cursor_api.status()["enabled"] and (body.get("raw") or "").strip():
        cursor_api.structure_experiment(eid)
    return _research(rid)


@route("POST", "/api/experiments/([^/]+)")
def experiment_update(q, body, eid):
    fields = {k: body[k] for k in ("title", "target", "setup", "results", "conclusion", "conclusion_text", "status") if k in body}
    store.update_experiment(eid, "user", body.get("base_rev"), **fields)
    return store.get_experiment(eid)


@route("DELETE", "/api/experiments/([^/]+)")
def experiment_delete(q, body, eid):
    store.delete_experiment(eid)
    return {"ok": True}


@route("POST", "/api/experiments/([^/]+)/structure")
def experiment_structure(q, body, eid):
    return {"run_id": cursor_api.structure_experiment(eid)}


@route("POST", "/api/research/([^/]+)/writing")
def writing_update(q, body, rid):
    sections = {k: (body[k] or "").strip() or None for k in ("abstract", "introduction") if k in body}
    store.update_writing(rid, body.get("base_rev"), "user", **sections)
    return _research(rid)


@route("POST", "/api/research/([^/]+)/writing/proposal")
def writing_proposal(q, body, rid):
    if body.get("action") == "discard":
        store.discard_writing(rid)
    else:
        store.accept_writing(rid, body.get("base_rev"), body.get("fields"))
    return _research(rid)


@route("POST", "/api/research/([^/]+)/writing/draft")
def writing_draft(q, body, rid):
    return {"run_id": cursor_api.draft_writing(rid, (body.get("instruction") or "").strip() or None)}


@route("GET", "/api/research/([^/]+)/export")
def writing_export(q, body, rid):
    return export.research_export(rid)


@route("POST", "/api/threads")
def thread_new(q, body):
    """text: a line the user only writes down; the discussion opens with it and nobody is asked to answer."""
    tid = store.new_thread(body["scope"], body.get("parent_id"), body.get("text"), fork=body.get("fork_message_id"))
    return store.thread_for(thread_id=tid)


@route("POST", "/api/threads/([^/]+)")
def thread_edit(q, body, tid):
    if "title" in body:
        store.set_thread_title(tid, body["title"], "user")
    if "parent_id" in body:
        store.move_thread(tid, body["parent_id"], "user")
    return store.thread_for(thread_id=tid)


@route("POST", "/api/chat")
def chat(q, body):
    """reply: false writes the line into the discussion without asking the agent (also works with no agent)."""
    text = (body.get("text") or "").strip()
    if not text:
        raise rules.Rejected("问题不能为空")
    if body.get("reply") is False:
        return {"message_id": store.add_message(store.thread_for(body["scope"], body.get("thread_id"))["id"], "user", text)}
    return {"run_id": cursor_api.discuss(body["scope"], text, body.get("thread_id"))}


@route("GET", "/api/runs/([^/]+)")
def run_get(q, body, rid):
    return store.get_run(rid)


@route("POST", "/api/runs/([^/]+)/cancel")
def run_cancel(q, body, rid):
    r = store.get_run(rid)
    if r["id"].startswith("pending-"):
        store.update_run(rid, status="CANCELLED")
        cursor_api.pump()
    else:
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

    def _refuse(self, method):
        """Any web page the user has open can send requests to 127.0.0.1. Host must name this server (DNS rebinding);
        writes must come from our own page (Origin) as JSON, which a cross-site form or no-cors fetch cannot send.
        Clients that send no Origin at all (curl) are not browsers and are let through."""
        host, port = self.headers.get("Host", ""), self.server.server_address[1]
        if host not in {f"{h}:{port}" for h in ("127.0.0.1", "localhost", self.server.server_address[0])}:
            return 403, "Host 不对：只接受 127.0.0.1 或 localhost 上的访问"
        if method == "GET":
            return None
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{host}":
            return 403, "跨站请求被拒绝"
        if method == "POST" and self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return 415, "请求体必须是 application/json"
        return None

    def _dispatch(self, method):
        refused = self._refuse(method)
        if refused:
            return self._json(refused[0], {"error": refused[1]})
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
        for job in (store.backup, cursor_api.expire_stale, inbox.drain, cursor_api.distill_pending, cursor_api.deepread_pending,
                    structure.backfill):
            try:
                job()
            except Exception:
                traceback.print_exc()
        time.sleep(20)


def serve(host="127.0.0.1", port=8765):
    store.counts()
    cursor_api.resume_watchers()
    bg(_poll_inbox)
    print(f"paper2brain 已启动：http://{host}:{port}  数据目录：{store.home()}")
    a = cursor_api.status()
    print("Agent：" + (f"已配置，本机 worker = {a['machine']}" if a["enabled"] else f"未配置（缺 {', '.join(a['missing'])}），只做规则预处理"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()
