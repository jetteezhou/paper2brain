"""Cursor Cloud Agents API: start agents on this Mac's worker and record what they do.
Docs: https://cursor.com/docs/cloud-agent/api/endpoints"""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from . import store

API = "https://api.cursor.com/v1"
TERMINAL = {"FINISHED", "ERROR", "CANCELLED", "EXPIRED"}


def config():
    path = store.home() / "config.json"
    cfg = json.loads(path.read_text()) if path.exists() else {}
    return {
        "api_key": os.environ.get("CURSOR_API_KEY") or cfg.get("cursor_api_key"),
        "machine": os.environ.get("P2B_MACHINE") or cfg.get("machine"),
        "repo": cfg.get("repo") or "https://github.com/jetteezhou/paper2brain",
        "model": cfg.get("model"),
        "auto_agent": cfg.get("auto_agent", True),
        "max_agents": int(cfg.get("max_agents", 2)),
        "run_timeout_min": int(cfg.get("run_timeout_min", 60)),
    }


def status():
    cfg = config()
    missing = [k for k, v in (("CURSOR_API_KEY", cfg["api_key"]), ("P2B_MACHINE", cfg["machine"])) if not v]
    return {"enabled": not missing, "missing": missing, "machine": cfg["machine"], "auto_agent": cfg["auto_agent"]}


class AgentError(RuntimeError):
    pass


def _request(method, path, body=None, accept="application/json", timeout=None):
    cfg = config()
    if not cfg["api_key"]:
        raise AgentError("未配置 CURSOR_API_KEY")
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json", "Accept": accept},
    )
    try:
        return urllib.request.urlopen(req, timeout=timeout or (60 if accept == "application/json" else 900))
    except urllib.error.HTTPError as e:
        raise AgentError(f"Cursor API {e.code}：{e.read().decode(errors='replace')[:300]}") from None


def _json(method, path, body=None, timeout=None):
    with _request(method, path, body, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _latest_run(agent_id):
    """After a create or follow-up whose response never arrived: the run it started, if it went through. A latest run
    we already track is an earlier one, so the request did not land."""
    info = _json("GET", f"/agents/{agent_id}")
    if not info.get("latestRunId") or store.get_run(info["latestRunId"]):
        raise AgentError("Cursor 没有收到这次请求，请重试")
    return {"id": info["latestRunId"], "status": "RUNNING"}


def _prompt(text, images):
    p = {"text": text}
    if images:
        p["images"] = [{"data": base64.b64encode(data).decode(), "mimeType": mime} for data, mime in images[:5]]
    return p


LIVE = ("CREATING", "RUNNING", "QUEUED")
# ponytail: images of a waiting run live only in memory; after a restart that run starts without them.
_images = {}
_pump_lock = threading.Lock()


def _spawn(fn, *args):
    threading.Thread(target=fn, args=args, daemon=True).start()


def _bind(kind, ref, agent_id):
    """Remember which agent works on what, so the next message continues the same conversation."""
    if kind in ("capture", "find"):
        store.update_capture(ref, agent_id=agent_id)
    if kind == "chat":
        store.set_thread_agent(ref, agent_id)
    if kind == "find":
        store.set_thread_agent(store.thread_for(f"find:{ref}")["id"], agent_id)
    if kind == "distill":
        store.kv_set(DISTILL_AGENT, agent_id)


def start_async(kind, ref, text, images=(), name=None, agent_id=None, prepare=None):
    """Queue a run and return its placeholder id at once; the UI shows it as waiting / starting. Follows up on
    agent_id when given, otherwise creates a new agent on this Mac (about a minute). The new agent's id is chosen
    here and stored with the placeholder, so a restart mid-create can still find it. prepare(placeholder) runs
    before anything is sent (e.g. to add the chat messages)."""
    if not config()["machine"]:
        raise AgentError("未配置 P2B_MACHINE（本机 worker 的名字）")
    placeholder, new = f"pending-{uuid.uuid4()}", not agent_id
    agent_id = agent_id or f"bc-{uuid.uuid4()}"
    store.add_run(placeholder, agent_id, kind, ref, text, "WAITING", launch={"name": name, "new": new})
    if images:
        _images[placeholder] = list(images)
    if prepare:
        prepare(placeholder)
    if new:
        _bind(kind, ref, agent_id)
    pump()
    return placeholder


def pump():
    """Start waiting runs, oldest first, while fewer than max_agents are live; at most one live run per agent
    (Cursor answers a second one with 409 agent_busy)."""
    with _pump_lock:
        runs = store.unfinished_runs()
        busy = {r["agent_id"] for r in runs if r["status"] in LIVE}
        room = config()["max_agents"] - sum(r["status"] in LIVE for r in runs)
        for r in runs:
            if room <= 0:
                break
            if r["status"] != "WAITING" or r["agent_id"] in busy:
                continue
            busy.add(r["agent_id"])
            room -= 1
            store.update_run(r["id"], status="CREATING", started_at=store.now())
            _spawn(_launch, r)


def _send(agent_id, text, images=(), name=None, new=False):
    cfg = config()
    try:
        if not new:
            return _json("POST", f"/agents/{agent_id}/runs", {"prompt": _prompt(text, images)}, timeout=180)["run"]
        body = {"agentId": agent_id, "prompt": _prompt(text, images), "env": {"type": "machine", "name": cfg["machine"]},
                "repos": [{"url": cfg["repo"]}]}
        if name:
            body["name"] = name[:100]
        if cfg["model"]:
            body["model"] = {"id": cfg["model"]}
        return _json("POST", "/agents", body, timeout=180)["run"]
    except (TimeoutError, urllib.error.URLError, ConnectionError):
        # creating an agent can outlast the response timeout and still succeed
        return _latest_run(agent_id)
    except AgentError as e:
        if new and "agent_id_conflict" in str(e):  # our agent id already exists: an earlier attempt went through
            return _latest_run(agent_id)
        raise


def _launch(r):
    placeholder, launch = r["id"], r["launch"] or {}
    try:
        run = _send(r["agent_id"], r["prompt"], _images.pop(placeholder, ()), launch.get("name"), launch.get("new"))
    except Exception as e:
        store.update_run(placeholder, status="ERROR", error=str(e))
        if launch.get("new"):
            store.forget_agent(r["agent_id"])
        pump()
        return
    stopped = (store.get_run(placeholder) or {}).get("status") in ("CANCELLED", "EXPIRED")
    store.replace_run(placeholder, run["id"], r["agent_id"], store.get_run(placeholder)["status"] if stopped else run.get("status", "CREATING"))
    if stopped:
        try:
            cancel(r["agent_id"], run["id"])
        except AgentError:
            pass
        pump()
        return
    watch(r["agent_id"], run["id"])


def expire_stale():
    """A run still live after run_timeout_min (hung, or an API that keeps failing) is stopped, so queues move on."""
    minutes = config()["run_timeout_min"]
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    for r in store.stale_runs(cutoff):
        store.update_run(r["id"], status="EXPIRED", error=f"超过 {minutes} 分钟没有完成，已停止")
        if not r["id"].startswith("pending-"):
            try:
                cancel(r["agent_id"], r["id"])
            except Exception:
                pass
    pump()


def watch(agent_id, run_id):
    _spawn(_watch, agent_id, run_id)


def _watch(agent_id, run_id):
    try:
        _follow(agent_id, run_id)
    finally:
        pump()


def _follow(agent_id, run_id):
    text, tools, last_flush = "", {}, 0.0

    def flush(force=False, **extra):
        nonlocal last_flush
        if force or time.time() - last_flush > 0.8:
            store.update_run(run_id, text=text, tools=list(tools.values()), **extra)
            last_flush = time.time()

    try:
        with _request("GET", f"/agents/{agent_id}/runs/{run_id}/stream", accept="text/event-stream") as r:
            event, data = None, []
            for raw in r:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif not line and event:
                    payload = json.loads("\n".join(data) or "{}")
                    if event == "status":
                        flush(True, status=payload.get("status", "RUNNING"))
                    elif event == "assistant":
                        text += payload.get("text", "")
                        flush()
                    elif event == "tool_call":
                        tools[payload["callId"]] = {
                            "name": payload.get("name"), "status": payload.get("status"), "args": _short(payload.get("args")),
                        }
                        flush()
                    elif event == "result":
                        text = payload.get("text") or text
                        flush(True, status=payload.get("status", "FINISHED"), duration_ms=payload.get("durationMs"))
                        return
                    elif event == "error":
                        raise AgentError(payload.get("message", "stream error"))
                    event, data = None, []
    except Exception:
        pass  # stream dropped or expired: fall back to polling the run
    while True:
        if (store.get_run(run_id) or {}).get("status") not in LIVE:
            return  # stopped here (timed out, deleted)
        try:
            run = _json("GET", f"/agents/{agent_id}/runs/{run_id}")
        except Exception as e:
            store.update_run(run_id, error=str(e))
            if not config()["api_key"]:
                return
            time.sleep(10)
            continue
        if run.get("status") in TERMINAL:
            store.update_run(
                run_id, status=run["status"], text=run.get("result") or text, tools=list(tools.values()),
                duration_ms=run.get("durationMs"),
            )
            return
        store.update_run(run_id, status=run.get("status", "RUNNING"))
        time.sleep(3)


def _short(args):
    """Tool arguments for display only: the command or path, not file contents."""
    if not isinstance(args, dict):
        return None
    for key in ("command", "path", "target_file", "query", "pattern"):
        if args.get(key):
            return str(args[key])[:300]
    return None


def cancel(agent_id, run_id):
    _json("POST", f"/agents/{agent_id}/runs/{run_id}/cancel")


def resume_watchers():
    """After a restart: keep watching live runs; a run whose request was in flight is found by its agent id or, when
    it never reached Cursor, queued to be sent again (a repeated create is idempotent); waiting runs wait on."""
    for r in store.unfinished_runs():
        if not r["id"].startswith("pending-"):
            watch(r["agent_id"], r["id"])
        elif r["status"] != "WAITING" and not r["agent_id"]:
            store.update_run(r["id"], status="ERROR", error="服务重启时 agent 还没创建完成，请重试")
        elif r["status"] != "WAITING":
            try:
                run = _latest_run(r["agent_id"])
            except Exception:
                store.update_run(r["id"], status="WAITING")
                continue
            store.replace_run(r["id"], run["id"], r["agent_id"], run["status"])
            watch(r["agent_id"], run["id"])
    pump()


# ---------- what we ask the librarian agent to do; the details live in librarian/*.md ----------

def process_capture(capture_id):
    cap = store.get_capture(capture_id)
    images = []
    for a in cap["attachments"]:
        att = store.attachment(a["id"])
        if att["mime"] in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            images.append((open(att["path"], "rb").read(), att["mime"]))
    label = cap["title"] or cap["raw"][:40] or "截图"
    text = (
        f"你是 paper2brain 的论文管理员。先读 librarian/README.md 和 librarian/capture.md，"
        f"然后处理捕获 {capture_id}（已入库并做过规则预处理，用 ./p2b show capture {capture_id} 查看）。"
    )
    return start_async("capture", capture_id, text, images, name=f"收下：{label}")


def scope_text(scope):
    kind, _, ref = scope.partition(":")
    if kind == "paper":
        p = store.get_paper(ref)
        return (
            f"论文 {ref}《{p['title'] if p else ''}》。先运行 `./p2b show paper {ref}` 了解快读卡、想法和笔记；"
            f"需要细节时用 `./p2b text {ref} --pages 3-5` 读原文。"
        )
    if kind == "topic":
        return f"主题「{ref}」下的论文。先运行 `./p2b papers --topic '{ref}'`。"
    if kind == "thoughts":
        return "用户记下的想法。先运行 `./p2b thoughts`，再结合相关论文回答。"
    if kind == "find":
        cap = store.get_capture(ref) or {}
        return (
            f"用户请你找的一组论文（捕获 {ref}）：「{cap.get('raw', '')}」。先运行 `./p2b show capture {ref}` 看已找到的论文；"
            f"比较时一点对应一篇。还要补找时按 librarian/find.md 关联到这个捕获。"
        )
    if kind == "papers":
        listed = "；".join(f"{pid}《{(store.get_paper(pid, with_anchors=False) or {}).get('title', '')}》" for pid in ref.split("|"))
        return f"用户选中一起讨论的几篇论文：{listed}。先逐篇运行 `./p2b show paper <id>`；比较时一点对应一篇，引用页码。"
    if kind == "anchor":
        a = store.get_anchor(ref) or {}
        pid, page, section = a.get("paper_id"), a.get("page"), a.get("section")
        p = (store.get_paper(pid, with_anchors=False) or {}) if pid else {}
        where = {"card": "快读卡", "report": "深读报告", "pdf": "原文"}.get(a.get("surface"), "")
        at = (f"第 {page} 页" if page else "") + (f"（{section}）" if section else "")
        ctx = f"需要上下文时用 `./p2b text {pid} --pages {max(1, page - 1)}-{page + 1}`；" if page else ""
        return (
            f"论文 {pid}《{p.get('title', '')}》的{where}{at}里，用户选中了这段：「{a.get('quote', '')}」。"
            f"先运行 `./p2b show paper {pid}`；{ctx}围绕这段回答：它在说什么、为什么这样做、"
            f"和别的工作或用户的研究有什么关系，或者回答用户的具体问题。回答要短，引用页码。"
        )
    if kind == "thought":
        t = store.get_thought(ref) or {}
        about = f"，关于论文 {t['about_paper']}《{t.get('paper_title') or ''}》" if t.get("about_paper") else ""
        gist = f"，提炼为「{t['gist']}」" if t.get("gist") else ""
        return (
            f"用户的一个想法（{ref}）：原话「{t.get('text', '')}」{gist}{about}。先运行 `./p2b thought show {ref}`，"
            f"再按 librarian/thought.md 的“围绕一条想法讨论”一节，帮用户把这个念头往下推。"
        )
    if kind == "research":
        x = store.get_research(ref)
        return (
            f"研究《{x['title'] if x else ''}》（{ref}）。先运行 `./p2b research show {ref}` 看三要素和素材，再读相关论文；"
            f"和用户一起把研究问题、研究方法、验证方案推敲清楚。用户要求调整时，按 librarian/research.md 用 propose 提交修改稿，"
            f"由用户确认；不要声称研究已确立。问到实现细节时读关联的代码仓库（librarian/code.md，只读）；"
            f"讨论摘要或引言时按 librarian/writing.md 提交写作修改稿。"
        )
    return "整个论文库。用 `./p2b search` 和 `./p2b papers` 检索。"


def discuss(scope, text, thread_id=None, start=False, images=()):
    """Continue the scope's latest discussion, or an older one the user jumped back to. start=True opens a new
    research: the agent first reads what the user brought and drafts, instead of just answering."""
    thread = store.thread_for(scope, thread_id)
    if thread["agent_id"]:
        prompt, name = text, None
    else:
        opening = "这是研究的开端：按 librarian/research.md 的“开启研究”一节处理。\n" if start else ""
        prompt = (
            f"先读 librarian/README.md 和 librarian/discuss.md。\n{opening}讨论范围：{scope_text(scope)}\n"
            f"讨论线程 ID：{thread['id']}（关联研究素材或笔记时用 --thread）\n\n用户{'的原话' if start else '的问题'}：{text}"
        )
        name = f"{'开启研究' if start else '讨论'}：{text[:40]}"

    def prepare(placeholder):
        store.add_message(thread["id"], "user", text)
        store.add_message(thread["id"], "assistant", "", placeholder)

    return start_async("chat", thread["id"], prompt, images, name=name, agent_id=thread["agent_id"], prepare=prepare)


def find_papers(capture_id):
    """The request opens the discussion of the found set (scope find:<capture>): the same agent searches, links its
    picks to the capture, then compares them; follow-ups continue with it."""
    cap = store.get_capture(capture_id)
    thread = store.thread_for(f"find:{capture_id}")
    prompt = (
        f"先读 librarian/README.md、librarian/find.md 和 librarian/discuss.md。\n"
        f"用户请你找论文（捕获 ID：{capture_id}，讨论线程 ID：{thread['id']}）。\n\n用户的原话：{cap['raw']}"
    )

    def prepare(placeholder):
        store.add_message(thread["id"], "user", cap["raw"])
        store.add_message(thread["id"], "assistant", "", placeholder)

    return start_async("find", capture_id, prompt, name=f"找论文：{cap['raw'][:40]}", prepare=prepare)


def write_card(pid):
    p = store.get_paper(pid)
    text = (
        f"先读 librarian/README.md 和 librarian/capture.md，按第 3、4 节为论文 {pid}《{p['title']}》写快读卡并归入主题"
        f"（这篇已经在库里，不用关联捕获）。用 ./p2b card put 提交，直到校验通过。"
    )
    return start_async("card", pid, text, name=f"快读卡：{p['title'][:60]}")


DISTILL_AGENT, DISTILL_TRIED = "distill_agent", "distill_tried"


def distill_pending():
    """Fill empty thought gists in batches through one long-lived agent: a follow-up starts in seconds, while a new
    agent on this machine takes about a minute. Each thought is tried at most twice."""
    if not status()["enabled"] or any(r["kind"] == "distill" for r in store.unfinished_runs()):
        return
    tried = json.loads(store.kv_get(DISTILL_TRIED) or "{}")
    ids = [i for i in store.pending_gists() if tried.get(i, 0) < 2]
    if not ids:
        return
    last = store.last_run("distill")
    if last and last["status"] == "ERROR" and "agent_busy" not in (last["error"] or ""):
        store.kv_set(DISTILL_AGENT, "")  # that agent is gone (archived or expired); start a fresh one
    store.kv_set(DISTILL_TRIED, json.dumps({**tried, **{i: tried.get(i, 0) + 1 for i in ids}}))
    text = f"先读 librarian/README.md 和 librarian/thought.md，按“提炼想法”一节处理这些想法：{' '.join(ids)}"
    start_async("distill", "thoughts", text, name="提炼想法", agent_id=store.kv_get(DISTILL_AGENT) or None)


DEEP_TRIED = "deepread_tried"
_deep_lock = threading.Lock()


def deepread_pending():
    """Marking a paper "待深读" asks the agent to have its report ready before the user opens it. Reports are
    written one at a time, and each paper is tried automatically only once; regenerating stays a manual action."""
    if not status()["enabled"]:
        return None
    with _deep_lock:
        if any(r["kind"] == "deepread" for r in store.unfinished_runs()):
            return None
        tried = set(json.loads(store.kv_get(DEEP_TRIED) or "[]"))
        pid = next((p for p in store.deep_queue() if p not in tried), None)
        if not pid:
            return None
        store.kv_set(DEEP_TRIED, json.dumps(sorted(tried | {pid})))
        return deep_read(pid)


def structure_experiment(eid):
    e = store.get_experiment(eid)
    text = (
        f"先读 librarian/README.md 和 librarian/experiment.md，整理实验 {eid}（属于研究 {e['research_id']}），"
        f"用 ./p2b experiment put {eid} --base-rev {e['revision']} 提交，直到校验通过。"
    )
    return start_async("experiment", eid, text, name=f"整理实验：{(e['title'] or e['raw'])[:40]}")


def code_map(rid, repo_id, pid):
    repo, paper = store.get_repo(repo_id), store.get_paper(pid)
    text = (
        f"先读 librarian/README.md 和 librarian/code.md，为研究 {rid} 做论文 {pid}《{paper['title']}》"
        f"和代码仓库 {repo['name']}（仓库 id {repo_id}，本地路径 {repo['path']}）的方法—代码对照，"
        f"用 ./p2b codemap put 提交，直到校验通过。仓库只读，不要修改任何文件。"
    )
    return start_async("codemap", rid, text, name=f"方法—代码对照：{repo['name']}")


def draft_writing(rid, instruction=None):
    w, x = store.get_writing(rid), store.get_research(rid)
    text = (
        f"先读 librarian/README.md 和 librarian/writing.md，为研究 {rid}《{x['title']}》起草或修改摘要和引言，"
        f"用 ./p2b writing propose {rid} --base-rev {w['revision']} 提交，直到校验通过。"
        + (f"\n用户的要求：{instruction}" if instruction else "")
    )
    return start_async("writing", rid, text, name=f"写作：{x['title'][:50]}")


def draft_research(rid):
    x = store.get_research(rid)
    text = (
        f"先读 librarian/README.md 和 librarian/research.md，为研究 {rid}《{x['title']}》起草三要素修改稿，"
        f"用 ./p2b research propose {rid} --base-rev {x['revision']} 提交，直到校验通过。修改稿由用户确认后才生效。"
    )
    return start_async("draft", rid, text, name=f"起草研究：{x['title'][:60]}")


def deep_read(pid):
    p = store.get_paper(pid)
    text = (
        f"先读 librarian/README.md 和 librarian/deepread.md，为论文 {pid}《{p['title']}》写深读报告，"
        f"用 ./p2b report put 提交，直到校验通过。"
    )
    return start_async("deepread", pid, text, name=f"深读：{p['title'][:60]}")
