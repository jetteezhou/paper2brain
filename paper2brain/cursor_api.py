"""Cursor Cloud Agents API: start agents on this Mac's worker and record what they do.
Docs: https://cursor.com/docs/cloud-agent/api/endpoints"""
import base64
import json
import os
import threading
import time
import urllib.error
import urllib.request

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
    }


def status():
    cfg = config()
    missing = [k for k, v in (("CURSOR_API_KEY", cfg["api_key"]), ("P2B_MACHINE", cfg["machine"])) if not v]
    return {"enabled": not missing, "missing": missing, "machine": cfg["machine"], "auto_agent": cfg["auto_agent"]}


class AgentError(RuntimeError):
    pass


def _request(method, path, body=None, accept="application/json"):
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
        return urllib.request.urlopen(req, timeout=60 if accept == "application/json" else 900)
    except urllib.error.HTTPError as e:
        raise AgentError(f"Cursor API {e.code}：{e.read().decode(errors='replace')[:300]}") from None


def _json(method, path, body=None):
    with _request(method, path, body) as r:
        return json.loads(r.read() or b"{}")


def _prompt(text, images):
    p = {"text": text}
    if images:
        p["images"] = [{"data": base64.b64encode(data).decode(), "mimeType": mime} for data, mime in images[:5]]
    return p


def start(kind, ref, text, images=(), name=None, agent_id=None):
    """Follow up on agent_id when given, otherwise create a new agent on this Mac. Returns (agent_id, run_id).
    The run is watched in the background; its progress lands in the runs table."""
    cfg = config()
    if not cfg["machine"]:
        raise AgentError("未配置 P2B_MACHINE（本机 worker 的名字）")
    if agent_id:
        run = _json("POST", f"/agents/{agent_id}/runs", {"prompt": _prompt(text, images)})["run"]
    else:
        body = {
            "prompt": _prompt(text, images),
            "env": {"type": "machine", "name": cfg["machine"]},
            "repos": [{"url": cfg["repo"]}],
        }
        if name:
            body["name"] = name[:100]
        if cfg["model"]:
            body["model"] = {"id": cfg["model"]}
        res = _json("POST", "/agents", body)
        run, agent_id = res["run"], res["agent"]["id"]
    store.add_run(run["id"], agent_id, kind, ref, text, run.get("status", "CREATING"))
    watch(agent_id, run["id"])
    return agent_id, run["id"]


def watch(agent_id, run_id):
    threading.Thread(target=_watch, args=(agent_id, run_id), daemon=True).start()


def _watch(agent_id, run_id):
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
    for run in store.unfinished_runs():
        watch(run["agent_id"], run["id"])


# ---------- what we ask the librarian agent to do; the details live in librarian/*.md ----------

def process_capture(capture_id):
    cap = store.get_capture(capture_id)
    images = []
    for a in cap["attachments"]:
        att = store.attachment(a["id"])
        images.append((open(att["path"], "rb").read(), att["mime"]))
    label = cap["title"] or cap["raw"][:40] or "截图"
    text = (
        f"你是 paper2brain 的论文管理员。先读 librarian/README.md 和 librarian/capture.md，"
        f"然后处理捕获 {capture_id}（已入库并做过规则预处理，用 ./p2b show capture {capture_id} 查看）。"
    )
    agent_id, run_id = start("capture", capture_id, text, images, name=f"收下：{label}")
    store.update_capture(capture_id, agent_id=agent_id)
    return run_id


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
    return "整个论文库。用 `./p2b search` 和 `./p2b papers` 检索。"


def discuss(scope, text):
    thread = store.thread_for(scope)
    if thread["agent_id"]:
        agent_id, run_id = start("chat", thread["id"], text, agent_id=thread["agent_id"])
    else:
        prompt = f"先读 librarian/README.md 和 librarian/discuss.md。\n讨论范围：{scope_text(scope)}\n\n用户的问题：{text}"
        agent_id, run_id = start("chat", thread["id"], prompt, name=f"讨论：{text[:40]}")
        store.set_thread_agent(thread["id"], agent_id)
    store.add_message(thread["id"], "user", text)
    store.add_message(thread["id"], "assistant", "", run_id)
    return run_id


def deep_read(pid):
    p = store.get_paper(pid)
    text = (
        f"先读 librarian/README.md 和 librarian/deepread.md，为论文 {pid}《{p['title']}》写深读报告，"
        f"用 ./p2b report put 提交，直到校验通过。"
    )
    return start("deepread", pid, text, name=f"深读：{p['title'][:60]}")[1]
