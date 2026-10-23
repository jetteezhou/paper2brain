"""Local web server: JSON API for the web UI plus static files. Same store and rules as the CLI."""
import base64
import contextlib
import email.utils
import hashlib
import hmac
import json
import mimetypes
import os
import re
import subprocess
import time
import traceback
import urllib.parse
import uuid
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import accounts, cli, cursor_api, export, handoff, inbox, overleaf, pipeline, repos, rules, store, structure, sync, translate

WEB = Path(__file__).resolve().parent.parent / "web"
mimetypes.add_type("text/javascript", ".mjs")  # ES modules (PDF.js) must not be served as octet-stream
ROUTES = []


def route(method, pattern):
    def deco(fn):
        ROUTES.append((method, re.compile("^" + pattern + "$"), fn))
        return fn
    return deco


def bg(fn, *args):
    store.spawn(fn, *args)


class Reply:
    def __init__(self, path, mime=None, revalidate=False):
        self.path, self.mime, self.revalidate = Path(path), mime, revalidate


# ---------- state ----------

@route("GET", "/api/state")
def state(q, body):
    agent = {**cursor_api.status(), "home": str(store.home()), "repo_dir": str(WEB.parent), "repo": cursor_api.config()["repo"]}
    return {"counts": store.counts(), "topics": store.topic_tree(), "topic_proposals": store.topic_proposals(), "agent": agent,
            "activity": store.activity(), "working": store.working(), "recent": store.recent_work(),
            "account": _account() if store.multi() else None}


# ---------- the shared server: accounts, libraries, members, agents elsewhere ----------

def _me():
    return accounts.ME.get() or {}


def _account():
    me = _me()
    space = me["library"] != me["user"]["library"]
    return {"user": {"id": me["user"]["id"], "name": me["user"]["name"]}, "library": me["library"], "role": me["role"],
            "libraries": accounts.libraries(me["user"]["id"]), "members": accounts.members(me["library"]) if space else [],
            "agent_member": accounts.agent_member(me["library"]) if space else me["user"]["id"],
            "login": accounts.login_name(me["user"]["id"]), "admin": accounts.is_admin(me["user"]["id"])}


@contextlib.contextmanager
def _scope(me):
    """A request works in its library; in a shared space it sees only the member's own discussions and runs."""
    with store.at_home(store.library_home(me["library"])), store.viewing(me["user"] if me["library"] != me["user"]["library"] else None):
        yield


def _shared(me):
    """{research id: its space} for the spaces this user is in; in their personal library such a research is a pointer."""
    out = {}
    if store.multi() and me["library"] == me["user"]["library"]:
        for lib in accounts.libraries(me["user"]["id"]):
            if lib["kind"] == "space":
                with store.at_home(store.library_home(lib["id"])), store.viewing(me["user"]):
                    for x in store.list_research():
                        out[x["id"]] = {**x, "space": {"id": lib["id"], "name": lib["name"], "role": lib["role"]}}
    return out


def _owner():
    if store.multi() and _me().get("role") != "owner":
        raise rules.Rejected("只有这个空间的所有者能做这一步")


@route("POST", "/api/join")
def join(q, body):
    user, token, lib = accounts.redeem(body.get("code"), body.get("name"), _me().get("user"), body.get("password"))
    return {"ok": True, "name": user["name"], "_cookies": {**({"p2b_session": token} if token else {}), "p2b_lib": lib}}


@route("POST", "/api/login")
def sign_in(q, body):
    return {"ok": True, "_cookies": {"p2b_session": accounts.login(body.get("name"), body.get("password"))}}


@route("POST", "/api/me/password")
def set_password(q, body):
    accounts.set_password(_me()["user"]["id"], body.get("name"), body.get("password"), body.get("old"))
    return {"ok": True}


@route("POST", "/api/libraries/switch")
def library_switch(q, body):
    if not accounts.role(_me()["user"]["id"], body.get("library")):
        raise rules.Rejected("你不在这个论文库里")
    return {"ok": True, "_cookies": {"p2b_lib": body["library"]}}


@route("POST", "/api/me/agent-token")
def agent_token(q, body):
    """The token setup.sh of paper2brain-client puts on the user's Mac; a new one replaces the old."""
    return {"token": accounts.issue(_me()["user"]["id"], "agent")}


@route("POST", "/api/agent/connect")
def agent_connect(q, body):
    """The setup on the user's Mac (paper2brain-client) checks its token here. Its Cursor key and quick search API
    stay on the Mac, which makes those calls itself."""
    return {"ok": True}


@route("POST", "/api/cli")
def cli_remote(q, body):
    """./p2b from an agent on a member's Mac (paper2brain-client); the server chose the library already."""
    if not isinstance(body.get("argv"), list) or not isinstance(body.get("files") or {}, dict):
        raise rules.Rejected("请求格式不对")
    argv, me = [str(a) for a in body["argv"]], _me()
    space = _pointer_space(me, argv) if store.multi() else None
    if not space:
        out, files = cli.run_remote(argv, body.get("files") or {})
        if store.multi() and (shared := _shared(me)):  # pointers show what their space has now
            if argv[:2] == ["research", "list"]:
                out = [shared.get(x["id"], x) for x in out]
            elif argv[:1] == ["show"] and isinstance(out, dict) and isinstance(out.get("research"), list):
                out["research"] = [{**x, **{k: shared[x["id"]][k] for k in ("title", "stage")}, "space": shared[x["id"]]["space"]}
                                   if x["id"] in shared else x for x in out["research"]]
        return {"out": out, "files": files}
    if space["role"] == "viewer" and not {"show", "list"} & set(argv[:2]):
        raise rules.Rejected(f"这个研究在共享空间「{space['name']}」里，你在那里只能查看")
    _bring_along(me, argv, store.library_home(space["id"]))
    with store.at_home(store.library_home(space["id"])), store.viewing(me["user"]):
        out, files = cli.run_remote(argv, body.get("files") or {})
    return {"out": out, "files": files}


def _bring_along(me, argv, dst):
    """Linking something of the personal library to a shared research copies it into the space first: a paper for
    everyone, a discussion (or the one a message is in) as the user's private one there, with the paper it is about."""
    if argv[:2] != ["research", "link"] or len(argv) < 5:
        return
    kind, ref = argv[3], argv[4]
    if kind == "paper":
        store.copy_paper(cli.to_pid(ref), dst)
    elif kind in ("thread", "message"):
        try:
            tid = ref if kind == "thread" else store.message(ref)["thread_id"]
        except rules.Rejected:
            return
        scope = tid and store.copy_thread(tid, dst, me["user"]["id"])
        if scope and scope.startswith("paper:"):
            store.copy_paper(scope[len("paper:"):], dst)


def _pointer_space(me, argv):
    """From the personal library, a command about a shared research (its id, or one of its experiments) runs in its
    space: the pointer here keeps only what was there when it was shared."""
    shared, tokens = _shared(me), set(argv)
    for rid, x in shared.items():
        if rid in tokens:
            return x["space"]
    if argv[:1] == ["experiment"]:
        for sp in {x["space"]["id"]: x["space"] for x in shared.values()}.values():
            with store.at_home(store.library_home(sp["id"])), store.viewing(me["user"]):
                if any(store.get_experiment(t) for t in argv[2:3]):
                    return sp
    return None


@route("POST", "/api/runner/claim")
def runner_claim(q, body):
    """A member's Mac (paper2brain-client's runner) asks for a run to do, Cursor or TCodex; waits up to 25 s for one.
    It says which backends it has, its worker's name and its Cursor account's models (cursor_api.claim)."""
    me = _me()["user"]
    deadline = time.time() + min(max(float(body.get("wait", 25)), 0), 25)
    while True:
        job = cursor_api.claim(accounts.agent_libraries(me), me, body.get("backends"), body.get("machine"), body.get("models"),
                               body.get("take"))
        if job or time.time() >= deadline:
            return {"job": job}
        cursor_api.wait_for_work(me["library"], min(1, max(deadline - time.time(), 0)))


@route("POST", "/api/runner/update")
def runner_update(q, body):
    """Progress and the result of a run the Mac took ({"live": false} tells it the run was stopped here), or the
    answer to a quick search call ({call_id, result | error})."""
    if body.get("call_id"):
        return {"live": cursor_api.answer(_me()["user"]["library"], body["call_id"], body.get("result"), body.get("error"))}
    if _me()["library"] not in accounts.agent_libraries(_me()["user"]):
        raise rules.Rejected("这个论文库的 agent 不由你的 Mac 负责")
    return {"live": cursor_api.progress(body.get("run_id"), body.get("text"), body.get("tools"), body.get("session"),
                                        body.get("done") is True, body.get("error"), body.get("duration_ms"), body.get("status"))}


@route("POST", "/api/spaces")
def space_create(q, body):
    """Share a research: copy it, with what it uses, into a new space of which the user is the owner."""
    lib = accounts.create_space(_me()["user"], body.get("name"), body.get("research_id"))
    return {"library": lib, "_cookies": {"p2b_lib": lib}}


@route("POST", "/api/research/([^/]+)/move-home")
def research_move_home(q, body, rid):
    """The space's owner takes this research back into their own library."""
    _owner()
    me = _me()
    if me["library"] == me["user"]["library"]:
        raise rules.Rejected("它已经在你的个人库里了")
    lib = accounts.move_home(me["user"], me["library"], rid)
    return {"library": lib, "_cookies": {"p2b_lib": lib}}


@route("POST", "/api/members/invite")
def member_invite(q, body):
    _owner()
    accounts.check_admin(_me()["user"]["id"], body.get("admin_password"))
    return {"code": accounts.invite("join", _me()["library"], body.get("role") or "editor", by=_me()["user"]["id"])}


def _admin(body):
    accounts.check_admin(_me()["user"]["id"], body.get("admin_password"))
    lib = body.get("library")
    if lib not in accounts.all_libraries():
        raise rules.Rejected("没有这个论文库")
    return store.at_home(store.library_home(lib))


@route("POST", "/api/admin/backups")
def admin_backups(q, body):
    """Super admin (second password): every library, and the snapshots of the chosen one."""
    with _admin(body):
        return {"libraries": accounts.restorable(), "backups": store.backups(), "research": store.snapshot_research(body["backup"]) if body.get("backup") else []}


@route("POST", "/api/admin/restore")
def admin_restore(q, body):
    """Roll a whole library, or one research in it, back to a snapshot."""
    with _admin(body), store.viewing(None):  # every member's discussions roll back, not just the admin's
        store.restore(body.get("backup"), body.get("research_id") or None)
    return {"ok": True}


@route("POST", "/api/members/agent")
def member_agent(q, body):
    _owner()
    accounts.set_agent(_me()["library"], body.get("user_id"))
    return {"ok": True}


@route("POST", "/api/members/([0-9a-f]{32})")
def member_role(q, body, uid):
    if uid != _me()["user"]["id"] or body.get("role"):  # anyone may leave; everything else is the owner's
        _owner()
    accounts.set_role(_me()["library"], uid, body.get("role"))
    return {"ok": True}


@route("POST", "/api/topics")
def topic_add(q, body):
    return {"path": store.add_topic(body.get("name"), body.get("parent"), "user")}


@route("POST", "/api/topics/edit")
def topic_edit(q, body):
    return {"path": store.edit_topic(body.get("path"), body.get("name"), body.get("parent"))}


@route("POST", "/api/topics/delete")
def topic_delete(q, body):
    store.delete_topic(body.get("path"))
    return {"ok": True}


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


@route("GET", "/api/search/ai")
def search_ai(q, body):
    return cursor_api.ai_search(q.get("q", "").strip()) if q.get("q", "").strip() else None


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
    if store.multi():  # the shared researches using it count too; their pointers here are left out
        shared, me = _shared(_me()), _me()
        p["research"] = [x for x in p["research"] if x["id"] not in shared]
        for lib in {x["space"]["id"]: x["space"] for x in shared.values()}.values():
            with store.at_home(store.library_home(lib["id"])), store.viewing(me["user"]):
                p["research"] += [{**x, "space": lib} for x in store.research_of_paper(pid)]
    s = structure.get(pid)
    return {**p, "figures": s["figures"], "key_figures": structure.key_figures(s, p["card"]), "outline": s["outline"],
            "key_sections": s["key"], "zh": translate.status(p)}


@route("POST", "/api/papers/([^/]+)/add")
def paper_add(q, body, pid):
    """A citation the user clicked: a paper the library doesn't list yet goes in as if its link had been pasted."""
    p = store.get_paper(pid, with_anchors=False) or pipeline.resolve_paper(pid)
    if p["listed"]:
        return {**p, "added": False}
    cap, _ = store.add_capture(str(uuid.uuid4()), rules.id_label(p["id"]), "web")
    store.link(cap["id"], p["id"], "user", evidence="用户点开了引用")
    store.update_capture(cap["id"], status="mapped")
    if cursor_api.status()["enabled"] and not p.get("card"):
        bg(cursor_api.process_capture, cap["id"])
    return {**store.get_paper(p["id"], with_anchors=False), "added": True}


@route("POST", "/api/papers/([^/]+)/name")
def paper_name(q, body, pid):
    store.set_short_name(pid, body.get("name"), "user")
    return {"ok": True}


@route("DELETE", "/api/papers/([^/]+)")
def paper_delete(q, body, pid):
    try:
        return {"trash_id": store.delete_paper(pid)}
    except KeyError:
        raise rules.Rejected("这篇论文不存在或已删除")


@route("DELETE", "/api/threads/([^/]+)")
def thread_delete(q, body, tid):
    try:
        return {"trash_id": store.delete_thread(tid)}
    except KeyError:
        raise rules.Rejected("这段讨论不存在或已删除")


@route("GET", "/api/trash")
def trash_list(q, body):
    return store.list_trash()


@route("POST", "/api/trash/([^/]+)/restore")
def trash_restore(q, body, tid):
    return store.restore_trash(tid)


@route("DELETE", "/api/trash/([^/]+)")
def trash_purge(q, body, tid):
    store.purge_trash(tid)
    return {"ok": True}


@route("GET", r"/api/papers/([^/]+)/figures/(\d+)")
def paper_figure(q, body, pid, n):
    return Reply(store.paper_dir(pid, create=False) / "figures" / f"{int(n)}.png", "image/png", revalidate=True)


@route("POST", "/api/papers/([^/]+)/card")
def paper_card(q, body, pid):
    return {"run_id": cursor_api.write_card(_paper(pid)["id"])}


@route("GET", "/api/papers/([^/]+)/pdf")
def paper_pdf(q, body, pid):
    p = _paper(pid)
    if not p["pdf_path"]:
        raise rules.Rejected("还没有 PDF")
    if q.get("lang") == "zh":
        return Reply(translate.zh_path(p["id"]), "application/pdf", revalidate=True)
    return Reply(p["pdf_path"], "application/pdf", revalidate=True)


@route("POST", "/api/papers/([^/]+)/read")
def paper_read(q, body, pid):
    store.mark_read(_paper(pid)["id"])
    return {"ok": True}


@route("GET", "/api/papers/([^/]+)/pages")
def paper_pages(q, body, pid):
    return store.pages(pid)


# a paper's card, pages and the list: the browser keeps a copy and asks each time, unchanged costs a 304 (discussions don't)
REVALIDATE = {papers, paper_get, paper_pages}


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
    """shared=1 in a personal library on the shared server: also the researches of the spaces the user is in."""
    shared = _shared(_me()) if store.multi() else {}
    out = [x for x in store.list_research("" if q.get("unsorted") else q.get("topic")) if x["id"] not in shared]  # pointers
    return out + list(shared.values()) if q.get("shared") else out


@route("POST", "/api/research")
def research_add(q, body):
    links = [(x["kind"], x["ref"]) for x in body.get("links") or []]
    return store.get_research(store.add_research(body.get("title"), "user", body.get("summary"), body.get("topics") or [], links))


def _not_pointer(rid):
    """In the personal library a shared research is only a pointer: changes go to the space."""
    if rid in (shared := _shared(_me())):
        raise rules.Rejected(f"这个研究已共享到「{shared[rid]['space']['name']}」，请到空间里改")


def _research(rid):
    x = store.get_research(rid)
    if not x:
        raise rules.Rejected("这个研究不存在或已删除")
    return x


@route("GET", "/api/research/([^/]+)")
def research_get(q, body, rid):
    """A pointer in the personal library answers with its space, and the page opens the research there."""
    if store.multi() and rid in (shared := _shared(_me())):
        return shared[rid]
    x = _research(rid)
    if x["writing"]["overleaf"]:
        x["overleaf_changed"] = overleaf.changed(x["writing"]["overleaf"])
    return x


@route("POST", "/api/research/(?!start$)([^/]+)")
def research_update(q, body, rid):
    store.update_research(rid, body.get("base_rev"), body.get("title"), body.get("summary"), body.get("body"))
    return _research(rid)


@route("DELETE", "/api/research/([^/]+)")
def research_delete(q, body, rid):
    _owner()
    store.delete_research(rid)
    return {"ok": True}


@route("POST", "/api/research/([^/]+)/establish")
def research_establish(q, body, rid):
    _owner()
    store.establish(rid, "user", body.get("established", True))
    return _research(rid)


@route("POST", "/api/research/([^/]+)/proposal")
def research_proposal(q, body, rid):
    if body.get("action") == "discard":
        store.discard_proposal(rid)
    else:
        if body.get("establish"):
            _owner()
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


@route("POST", "/api/research/([^/]+)/diagrams")
def research_diagram_draw(q, body, rid):
    _research(rid)
    name, kind = body.get("name") or None, body.get("kind") or "method"
    if kind not in ("method", "motivation"):
        raise rules.Rejected("图的类型是 method 或 motivation")
    if name and not store.diagram_dir(rid, name).is_dir():
        raise rules.Rejected("这张图不存在或已删除")
    return {"run_id": cursor_api.draw_diagram(rid, (body.get("instruction") or "").strip() or None, name, kind)}


# The HTML source goes out as plain text: the agent wrote it, it must not run as a page of this app.
DIAGRAM_MIME = {"figure.png": "image/png", "figure.pdf": "application/pdf", "figure.html": "text/plain; charset=utf-8"}


@route("GET", r"/api/research/([^/]+)/diagrams/([^/]+)/(figure\.(?:png|pdf|html))")
def research_diagram_file(q, body, rid, name, f):
    return Reply(store.diagram_dir(_research(rid)["id"], name) / f, DIAGRAM_MIME[f])


@route("DELETE", "/api/research/([^/]+)/diagrams/([^/]+)")
def research_diagram_delete(q, body, rid, name):
    store.delete_diagram(_research(rid)["id"], name)
    return {"ok": True}


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


@route("POST", "/api/research/([^/]+)/plan")
def plan_update(q, body, rid):
    store.update_plan(rid, rules.validate_plan(body.get("tasks")), "user", body.get("base_rev"))
    return {"ok": True}


@route("POST", "/api/research/([^/]+)/plan/proposal")
def plan_proposal(q, body, rid):
    if body.get("action") == "discard":
        store.discard_plan(rid)
    else:
        store.accept_plan(rid, body.get("base_rev"))
    return {"ok": True}


@route("POST", "/api/research/([^/]+)/plan/draft")
def plan_draft(q, body, rid):
    return {"run_id": cursor_api.draft_plan(rid, (body.get("instruction") or "").strip() or None)}


def _context(rid):
    return "\n".join(filter(None, ((store.get_research(rid) or {}).get("body") or {}).values()))


@route("POST", "/api/research/([^/]+)/figures")
def figures_update(q, body, rid):
    """The user's own edit: the figures, and optionally the setup and which experiment result fills which cell."""
    cur = store.get_figures(rid)
    design = rules.validate_setup(body["setup"], store.paper_ids()) if body.get("setup") else None
    figures = rules.validate_figures(body.get("figures", cur["figures"]), _context(rid), design or cur["design"])
    cells = rules.validate_cells(body["cells"], figures, store.get_research(rid)["experiments"]) if "cells" in body else None
    store.update_figures(rid, figures, "user", body.get("base_rev"), design)
    if cells:
        store.fill_cells(rid, cells)
    return {"ok": True}


@route("POST", "/api/research/([^/]+)/figures/proposal")
def figures_proposal(q, body, rid):
    if body.get("action") == "discard":
        store.discard_figures(rid)
        return {"ok": True}
    store.accept_figures(rid, body.get("base_rev"))
    x = store.get_research(rid)
    if cursor_api.status()["enabled"] and any(e["results"] for e in x["experiments"]):
        return {"ok": True, "run_id": cursor_api.fill_figures(rid)}
    return {"ok": True}


@route("POST", "/api/research/([^/]+)/figures/draft")
def figures_draft(q, body, rid):
    return {"run_id": cursor_api.draft_figures(rid, (body.get("instruction") or "").strip() or None)}


@route("POST", "/api/research/([^/]+)/related")
def related_update(q, body, rid):
    store.update_related(rid, rules.validate_related(body.get("groups"), store.paper_ids()), "user", body.get("base_rev"))
    return {"ok": True}


@route("POST", "/api/research/([^/]+)/related/proposal")
def related_proposal(q, body, rid):
    if body.get("action") == "discard":
        store.discard_related(rid)
    else:
        store.accept_related(rid, body.get("base_rev"))
    return {"ok": True}


@route("POST", "/api/research/([^/]+)/related/draft")
def related_draft(q, body, rid):
    return {"run_id": cursor_api.draft_related(rid)}


def _overleaf(rid):
    pid = store.get_writing(rid)["overleaf"]
    if not pid:
        raise rules.Rejected("这个研究还没关联 Overleaf 项目")
    return pid


@route("POST", "/api/research/([^/]+)/overleaf")
def overleaf_link(q, body, rid):
    _owner()
    pid = overleaf.project_id(body.get("url"))
    overleaf.clone(pid)
    store.set_overleaf(rid, pid)
    return {"ok": True}


@route("POST", "/api/research/([^/]+)/overleaf/unlink")
def overleaf_unlink(q, body, rid):
    _owner()
    store.set_overleaf(rid, None)  # the local checkout stays, unpushed edits included
    return {"ok": True}


@route("GET", "/api/research/([^/]+)/overleaf")
def overleaf_changes(q, body, rid):
    pid = _overleaf(rid)
    return {"project": pid, **overleaf.changes(pid)}


@route("POST", "/api/research/([^/]+)/overleaf/(pull|push|discard)")
def overleaf_action(q, body, rid, action):
    _owner()
    pid = _overleaf(rid)
    if action == "push":
        overleaf.push(pid, (body.get("message") or "").strip())
    else:
        getattr(overleaf, action)(pid)
    return {"ok": True}


@route("GET", "/api/agent/models")
def agent_models(q, body):
    return {"modules": [{"id": k, "label": v, "model": cursor_api.config()["models"].get(k)} for k, v in cursor_api.MODULES.items()],
            "fallback": cursor_api.config()["model"], "models": cursor_api.list_models()}


@route("POST", "/api/agent/models")
def agent_model_set(q, body):
    _owner()
    cursor_api.set_model(body.get("module"), body.get("model"))
    return {"ok": True}


@route("GET", "/api/agent/search-api")
def search_api_get(q, body):
    api = cursor_api.search_api() or {}
    return {"base_url": api.get("base_url", ""), "model": api.get("model", ""), "web_model": api.get("web_model", ""), "has_key": bool(api.get("api_key"))}


@route("POST", "/api/agent/search-api")
def search_api_set(q, body):
    _owner()
    if store.multi():
        raise rules.Rejected("快速搜索接口在你的 Mac 上配置：运行 ~/paper2brain-client/setup.sh --search")
    cursor_api.set_search_api(body.get("base_url"), body.get("model"), body.get("api_key"), body.get("web_model"))
    return {"ok": True}


@route("GET", "/api/agent/translate")
def translate_get(q, body):
    return {"engine": translate.engine(), "engines": translate.ENGINES, "installed": translate.BIN.exists()}


@route("POST", "/api/agent/translate")
def translate_set(q, body):
    _owner()
    translate.set_engine(body.get("engine"))
    return {"ok": True}


PULL_LOG = Path("/tmp/paper2brain-pull.log")


@route("GET", "/api/handoff")
def handoff_get(q, body):
    if store.multi():
        raise rules.Rejected("多用户服务器不用 iCloud 交接")
    m = handoff.meta() if handoff.DROP.exists() else None
    return {"me": handoff.ME, "holder": handoff.holder(), "sync": sync.enabled(), "folder": str(handoff.DROP),
            "snapshot": m and {"host": m.get("host"), "owner": m.get("owner"), "at": m.get("at")},
            "pull_log": PULL_LOG.read_text()[-1500:] if PULL_LOG.exists() else ""}


@route("POST", "/api/handoff/pull")
def handoff_pull(q, body):
    """Take the library from iCloud. pull stops this server and starts it again, so it runs in its own session."""
    if store.multi():
        raise rules.Rejected("多用户服务器不用 iCloud 交接")
    if not handoff.meta():
        raise rules.Rejected("iCloud 里还没有快照：先在另一台 Mac 上 push，或等它自动放一份")
    with PULL_LOG.open("w") as log:
        subprocess.Popen([str(WEB.parent / ".venv/bin/python"), str(WEB.parent / "ops/handoff.py"), "pull"], cwd=WEB.parent,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return {"ok": True}


@route("POST", "/api/agent/login")
def agent_login(q, body):
    return cursor_api.login(body.get("cli"))


@route("POST", "/api/agent/backend")
def agent_backend_set(q, body):
    _owner()
    cursor_api.set_backend(body.get("backend"))
    return {"ok": True}


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
    """reply: false writes the line into the discussion without asking the agent (also works with no agent).
    redo: true replaces the last answer: its question is asked again, on the given model if any."""
    text = (body.get("text") or "").strip()
    model = body.get("model") and cursor_api.clean_model(body["model"])
    if body.get("redo") is True:
        return {"run_id": cursor_api.discuss(body["scope"], "", body.get("thread_id"), model=model, redo=True)}
    images = [(base64.b64decode(i["data"]), i["mime"]) for i in body.get("images") or []
              if i.get("mime") in ("image/png", "image/jpeg", "image/gif", "image/webp")][:5]
    if not text and not (images and body.get("reply") is not False):
        raise rules.Rejected("问题不能为空")
    if body.get("reply") is False:
        return {"message_id": store.add_message(store.thread_for(body["scope"], body.get("thread_id"))["id"], "user", text)}
    if images:
        text = f"{text}\n\n（附了 {len(images)} 张图）".strip()
    return {"run_id": cursor_api.discuss(body["scope"], text, body.get("thread_id"), images=images, model=model)}


@route("POST", "/api/quick")
def quick(q, body):
    return cursor_api.quick_answer(body.get("quote"), body.get("question"), body.get("scope"), body.get("pid"), body.get("page"),
                                   body.get("surface"), web=body.get("web") is True)


@route("POST", "/api/find/web")
def find_web(q, body):
    return cursor_api.web_find(body.get("text"))


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

    def _send(self, code, data, mime="application/json; charset=utf-8", headers=(), cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj, revalidate=False):
        cookies = obj.pop("_cookies", {}) if isinstance(obj, dict) else {}
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        headers = [("Set-Cookie", f"{k}={v}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax{secure}") for k, v in cookies.items()]
        data = json.dumps(obj, ensure_ascii=False, default=str).encode()
        if not revalidate:
            return self._send(code, data, headers=headers)
        tag = hashlib.sha256(data).hexdigest()[:32]
        # Caddy's compression marks the tag weak or suffixes it, so look for ours inside what comes back
        if tag in self.headers.get("If-None-Match", ""):
            return self._not_modified(("ETag", f'"{tag}"'))
        self._send(code, data, headers=[*headers, ("ETag", f'"{tag}"')], cache="no-cache")

    def _not_modified(self, header):
        self.send_response(304)
        self.send_header(*header)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

    # account routes work in any library, also for a viewer; an agent token reaches only AGENT
    AGENT = {"/api/agent/connect", "/api/cli", "/api/runner/claim", "/api/runner/update"}
    ACCOUNT = {"/api/join", "/api/login", "/api/libraries/switch", "/api/me/agent-token", "/api/admin/backups", "/api/admin/restore", "/api/me/password", *AGENT}

    def _who(self, method, path, body):
        """On the shared server: (who is asking and in which library, None) or (None, (code, error)).
        The library is the one picked in this browser (cookie p2b_lib), else the user's own; for an agent's ./p2b,
        the one its run belongs to."""
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        kind, _, bearer = self.headers.get("Authorization", "").partition(" ")
        token = bearer if kind == "Bearer" else (cookies["p2b_session"].value if "p2b_session" in cookies else None)
        got = accounts.auth(token)
        if not got:
            return None, None if path in ("/api/join", "/api/login") else (401, "请先登录")
        user, kind = got
        if kind == "agent" and path not in self.AGENT:
            return None, (403, "本机 agent 的口令只能用来运行 ./p2b")
        if path == "/api/cli":
            lib = accounts.agent_library(user, body.get("agent_id"), body.get("library"))
        elif path == "/api/runner/update":
            lib = body.get("library") or user["library"]
        else:
            lib = cookies["p2b_lib"].value if "p2b_lib" in cookies else user["library"]
        role = accounts.role(user["id"], lib)
        if not role:
            lib, role = user["library"], "owner"
        if role == "viewer" and method != "GET" and path not in self.ACCOUNT:
            return None, (403, "你在这个空间里只能查看")
        return {"user": user, "library": lib, "role": role}, None

    def _file(self, path, mime=None, revalidate=False):
        """revalidate (the page's own files): the browser keeps a copy and asks each time, an unchanged file costs a 304."""
        if not path.is_file():
            return self._json(404, {"error": "文件不存在"})
        mime = mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if not revalidate:
            return self._send(200, path.read_bytes(), mime)
        stamp = email.utils.formatdate(path.stat().st_mtime, usegmt=True)
        if self.headers.get("If-Modified-Since") == stamp:
            return self._not_modified(("Last-Modified", stamp))
        # byte ranges: PDF.js shows the first pages of a large PDF before the rest has come over a slow line
        size, rng = path.stat().st_size, re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if rng and int(rng[1]) < size:
            start, end = int(rng[1]), min(int(rng[2] or size - 1), size - 1)
            with path.open("rb") as f:
                f.seek(start)
                data = f.read(end - start + 1)
            return self._send(206, data, mime, headers=[("Content-Range", f"bytes {start}-{end}/{size}"), ("Last-Modified", stamp)], cache="no-cache")
        self._send(200, path.read_bytes(), mime, headers=[("Last-Modified", stamp), ("Accept-Ranges", "bytes")], cache="no-cache")

    def _refuse(self, method):
        """Any web page the user has open can send requests to 127.0.0.1. Host must name this server (DNS rebinding);
        writes must come from our own page (Origin) as JSON, which a cross-site form or no-cors fetch cannot send.
        Clients that send no Origin at all (curl) are not browsers and are let through. From the LAN, Host is the IP
        the client actually reached (a rebinding domain never is); P2B_HOSTS adds other names. A password then guards
        every request, loopback included, since a tunnel or proxy reaches us from 127.0.0.1."""
        host, port, here = self.headers.get("Host", ""), self.server.server_address[1], self.connection.getsockname()[0]
        extra = _hosts()
        if host not in {f"{h}:{port}" for h in ("127.0.0.1", "localhost", here, *extra)} | set(extra):
            return 403, "Host 不对：只接受 127.0.0.1、localhost、本机 IP 或 P2B_HOSTS 里的地址"
        password = _password()
        if not password and not here.startswith("127.") and not store.multi():
            return 403, "从别的设备访问要先在 config.json 里设 password"
        if password:
            kind, _, cred = self.headers.get("Authorization", "").partition(" ")
            try:
                given = base64.b64decode(cred).decode().partition(":")[2] if kind == "Basic" else ""
            except ValueError:
                given = ""
            if not hmac.compare_digest(given.encode(), password.encode()):
                return 401, "需要访问口令"
        if method == "GET":
            return None
        origin = self.headers.get("Origin")
        if origin is not None and origin not in (f"http://{host}", f"https://{host}"):
            return 403, "跨站请求被拒绝"
        if method == "POST" and self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return 415, "请求体必须是 application/json"
        return None

    def _dispatch(self, method):
        refused = self._refuse(method)
        if refused and refused[0] == 401:
            data = json.dumps({"error": refused[1]}, ensure_ascii=False).encode()
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="paper2brain", charset="UTF-8"')
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if refused:
            return self._json(refused[0], {"error": refused[1]})
        url = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(url.path)
        if method == "GET" and not path.startswith("/api/"):
            if store.multi() and path in ("/", "/index.html") and not self._who(method, path, {})[0]:
                return self._send(302, b"", headers=[("Location", "/join.html")])
            target = (WEB / path.lstrip("/")).resolve() if path != "/" else WEB / "index.html"
            if WEB.resolve() not in target.parents and target != WEB / "index.html":
                return self._json(404, {"error": "not found"})
            return self._file(target, revalidate=True)
        q = dict(urllib.parse.parse_qsl(url.query))
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except json.JSONDecodeError:
            return self._json(400, {"error": "请求体不是合法 JSON"})
        me, scope = None, contextlib.nullcontext()
        if store.multi():
            try:
                me, refused = self._who(method, path, body if isinstance(body, dict) else {})
            except rules.Rejected as e:
                refused = (403, str(e))
            if refused:
                return self._json(refused[0], {"error": refused[1]})
            if me:
                scope = _scope(me)
        for m, pattern, fn in ROUTES:
            # match before unquoting: paper IDs such as DOIs contain "/" (sent as %2F)
            match = pattern.match(url.path) if m == method else None
            if match:
                try:
                    accounts.ME.set(me)
                    with scope:
                        if me and method != "GET" and url.path.startswith("/api/research/"):
                            _not_pointer(urllib.parse.unquote(url.path.split("/")[3]))
                        out = fn(q, body, *map(urllib.parse.unquote, match.groups()))
                except rules.Rejected as e:
                    return self._json(400, {"error": str(e)})
                except cursor_api.AgentError as e:
                    return self._json(502, {"error": str(e)})
                except Exception as e:
                    traceback.print_exc()
                    return self._json(500, {"error": f"{type(e).__name__}: {e}"})
                return self._file(out.path, out.mime, out.revalidate) if isinstance(out, Reply) else self._json(200, out, fn in REVALIDATE)
        self._json(404, {"error": "not found"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")


AGENT_JOBS = (cursor_api.distill_pending, cursor_api.name_pending, cursor_api.deepread_pending, cursor_api.card_pending)


def _run(jobs):
    for job in jobs:
        try:
            job()
        except Exception:
            traceback.print_exc()


def _poll_inbox():
    while True:
        if store.multi():  # every library in turn; iCloud (inbox, handoff, sync) is for one person's Macs
            for lib in accounts.all_libraries():
                with store.at_home(store.library_home(lib)):
                    _run((store.backup, cursor_api.expire_stale, structure.backfill, translate.backfill, *AGENT_JOBS))
            _run((accounts.backup_hub,))
        else:
            _run((store.backup, cursor_api.expire_stale, structure.backfill, translate.backfill, sync.run, lambda: bg(handoff.auto_push),
                  *((inbox.drain, *AGENT_JOBS) if handoff.holder() else ())))
        time.sleep(20)


def _password():
    if store.multi():
        return None  # each request carries its user's token instead (Handler._who)
    return os.environ.get("P2B_PASSWORD") or store.config().get("password")


def _hosts():
    """P2B_HOSTS (comma-separated) and config.json "hosts", read on every request so a new tunnel domain needs no restart."""
    mine = [] if store.multi() else list(store.config().get("hosts") or [])  # the shared server has no library of its own
    return [h.strip() for h in os.environ.get("P2B_HOSTS", "").split(",") + mine if h.strip()]


def _lan_ip():
    """This Mac's address on the office network: the default route's interface. A VPN client (iOA) routes every
    other destination through its own tunnel, whose address LAN peers cannot reach."""
    try:
        route = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True, timeout=5).stdout
        iface = re.search(r"interface:\s*(\S+)", route).group(1)
        return subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, AttributeError, subprocess.TimeoutExpired):
        return None  # not macOS, or offline


def serve(host=None, port=8765):
    """host None: the LAN too when a password is set (other devices on the office network), else only this Mac."""
    if store.multi():
        return serve_multi(host or "127.0.0.1", port)
    host = host or ("0.0.0.0" if _password() else "127.0.0.1")
    if (host not in ("127.0.0.1", "localhost") or _hosts()) and not _password():
        raise SystemExit("对本机以外开放（--host 或 P2B_HOSTS）时必须设置 P2B_PASSWORD：这个服务能让 agent 在本机执行命令")
    store.counts()
    cursor_api.resume_watchers()
    bg(_poll_inbox)
    lan = _lan_ip() if host == "0.0.0.0" else None
    print(f"paper2brain 已启动：http://127.0.0.1:{port}" + (f"  内网：http://{lan}:{port}" if lan else "") + f"  数据目录：{store.home()}", flush=True)
    a = cursor_api.status()
    print("Agent：" + (f"已配置，本机 worker = {a['machine']}" if a["enabled"] else f"未配置（缺 {', '.join(a['missing'])}），只做规则预处理"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


def serve_multi(host, port):
    """The shared server (P2B_ROOT): every user signs in, each library is its own directory, agents run on the
    members' own Macs. Meant to sit behind an HTTPS proxy (ops/Caddyfile), with P2B_HOSTS naming its domain."""
    clash = [k for k in ("P2B_HOME", "CURSOR_API_KEY", "P2B_MACHINE", "P2B_PASSWORD") if os.environ.get(k)]
    if clash:
        raise SystemExit(f"多用户模式下不能设置 {'、'.join(clash)}：每个人的 key 和 worker 存在各自的论文库里")
    if not _hosts():
        raise SystemExit("设置 P2B_HOSTS 为网页的域名（例如 p2b.example.com），否则浏览器的请求会被拒绝")
    for lib in accounts.all_libraries():
        with store.at_home(store.library_home(lib)):
            cursor_api.resume_watchers()
    bg(_poll_inbox)
    print(f"paper2brain 多用户服务已启动：http://{host}:{port}  域名：{', '.join(_hosts())}  数据：{accounts.root()}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
