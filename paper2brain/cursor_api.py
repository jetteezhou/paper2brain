"""Cursor Cloud Agents API: start agents on this Mac's worker and record what they do.
Docs: https://cursor.com/docs/cloud-agent/api/endpoints"""
import base64
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from . import rules, store

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
        "models": cfg.get("models") or {},
        "auto_agent": cfg.get("auto_agent", True),
        "max_agents": int(cfg.get("max_agents", 5)),
        "run_timeout_min": int(cfg.get("run_timeout_min", 60)),
        "backend": cfg.get("backend") or "cursor",
    }


# Both backends can run at once: each module's model names its backend, and an agent stays on the backend it was
# created on (its id says which), so a follow-up never crosses over.
BACKENDS = ("cursor", "tcodex")


def _config_write(**fields):
    path = store.home() / "config.json"
    cfg = json.loads(path.read_text()) if path.exists() else {}
    cfg.update(fields)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    return cfg


def set_backend(backend):
    """The backend for modules left on "default"."""
    if backend not in BACKENDS:
        raise rules.Rejected(f"没有这个后端：{backend}")
    _config_write(backend=backend)


def _missing(backend):
    if backend == "tcodex":
        return [] if _tcodex_bin() else ["tcodex 命令"]
    cfg = config()
    return [k for k, v in (("CURSOR_API_KEY", cfg["api_key"]), ("P2B_MACHINE", cfg["machine"])) if not v]


def status():
    cfg, ready = config(), [b for b in BACKENDS if not _missing(b)]
    names = {"cursor": cfg["machine"], "tcodex": "TCodex"}
    return {"enabled": bool(ready), "missing": [] if ready else _missing(cfg["backend"]), "machine": " + ".join(names[b] for b in ready),
            "auto_agent": cfg["auto_agent"], "backend": cfg["backend"], "backends": {b: b in ready for b in BACKENDS}}


def _backend_of(model=None, agent_id=None):
    if agent_id:
        return "tcodex" if agent_id.startswith("codex-") else "cursor"
    return (model.get("backend") or "cursor") if model else config()["backend"]


class AgentError(RuntimeError):
    pass


# The parts of the app that can each run on their own model (config.json "models": {module: {"id", "params"}}).
MODULES = {
    "research": "研究：研究页的讨论、起草三要素、计划、相关工作、写作、改论文、代码对照、整理实验",
    "diagram": "画图：研究页的动机图和方法框架图（没设时用研究的模型）",
    "discuss": "讨论：论文、主题、论文库、划词、找论文",
    "capture": "收下：整理收下的内容、写快读卡",
    "deepread": "深读报告",
    "distill": "给讨论起标题",
}
KIND_MODULE = {"draft": "research", "writing": "research", "plan": "research", "related": "research", "codemap": "research", "experiment": "research",
               "figures": "research", "diagram": "diagram", "motivation": "diagram", "find": "discuss", "chat": "discuss", "capture": "capture", "card": "capture", "deepread": "deepread", "distill": "distill"}


def model_for(module):
    """The module's model ({"id", "params", "backend"}; no backend means Cursor), else the old single Cursor "model"
    when the default backend is Cursor, else None: the default backend's default model. Drawing was part of research
    and keeps its model until set on its own."""
    cfg = config()
    return cfg["models"].get(module) or (module == "diagram" and cfg["models"].get("research")) or ({"id": cfg["model"]} if cfg["model"] and cfg["backend"] == "cursor" else None)


def set_model(module, model):
    if module not in MODULES:
        raise rules.Rejected(f"没有这个模块：{module}")
    if model is not None and not (isinstance(model.get("id"), str) and model.get("backend", "cursor") in BACKENDS
                                  and all(isinstance(p.get("id"), str) and isinstance(p.get("value"), str) for p in model.get("params") or [])):
        raise rules.Rejected("模型格式不对")
    path = store.home() / "config.json"
    models = (json.loads(path.read_text()) if path.exists() else {}).get("models") or {}
    if model:
        models[module] = {"id": model["id"], **({"params": model["params"]} if model.get("params") else {}),
                          **({"backend": "tcodex"} if model.get("backend") == "tcodex" else {})}
    else:
        models.pop(module, None)
    _config_write(models=models)


_models_cache = {}


def list_models():
    """The models of every ready backend, each tagged with its backend, with their parameters (context window,
    reasoning effort...). Cursor's list is cached 10 minutes."""
    items = []
    if not _missing("cursor"):
        if time.time() - _models_cache.get("at", 0) > 600:
            _models_cache.update(items=_json("GET", "/models")["items"], at=time.time())
        items += [{**m, "backend": "cursor"} for m in _models_cache["items"]]
    if not _missing("tcodex"):
        items += TCODEX_MODELS
    return items


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


def start_async(kind, ref, text, images=(), name=None, agent_id=None, prepare=None, module=None):
    """Queue a run and return its placeholder id at once; the UI shows it as waiting / starting. Follows up on
    agent_id when given, otherwise creates a new agent on this Mac (about a minute) on the module's model; a
    follow-up keeps the model its agent was created with. The new agent's id is chosen here and stored with the
    placeholder, so a restart mid-create can still find it. prepare(placeholder) runs before anything is sent
    (e.g. to add the chat messages)."""
    placeholder, new = f"pending-{uuid.uuid4()}", not agent_id
    model = model_for(module or KIND_MODULE.get(kind, "discuss")) if new else None
    backend = _backend_of(model, agent_id)
    if _missing(backend):
        raise AgentError(f"{'TCodex' if backend == 'tcodex' else 'Cursor'} 后端未配置：缺 " + "、".join(_missing(backend)))
    agent_id = agent_id or f"{'codex' if backend == 'tcodex' else 'bc'}-{uuid.uuid4()}"
    store.add_run(placeholder, agent_id, kind, ref, text, "WAITING", launch={"name": name, "new": new, "model": model})
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


def _send(agent_id, text, images=(), name=None, new=False, model=None):
    cfg = config()
    try:
        if not new:
            return _json("POST", f"/agents/{agent_id}/runs", {"prompt": _prompt(text, images)}, timeout=180)["run"]
        body = {"agentId": agent_id, "prompt": _prompt(text, images), "env": {"type": "machine", "name": cfg["machine"]},
                "repos": [{"url": cfg["repo"]}]}
        if name:
            body["name"] = name[:100]
        if model:
            body["model"] = {k: v for k, v in model.items() if k != "backend"}
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
    if _backend_of(agent_id=r["agent_id"]) == "tcodex":
        return _codex_launch(r)
    try:
        run = _send(r["agent_id"], r["prompt"], _images.pop(placeholder, ()), launch.get("name"), launch.get("new"), launch.get("model"))
    except Exception as e:
        store.update_run(placeholder, status="ERROR", error=str(e))
        # ponytail: guesses Cursor's wording for an agent that is gone (archived / expired); the next message then
        # starts a fresh agent instead of failing on the old one forever.
        if launch.get("new") or re.search(r"Cursor API (404|410)|archived", str(e), re.I):
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


# ---------- TCodex backend: `tcodex exec` (OpenAI Codex CLI) run in this repo instead of a Cursor cloud agent ----------

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EFFORT = [{"id": "effort", "displayName": "思考强度", "values": [{"value": v} for v in ("low", "medium", "high")]}]
# ponytail: copied from the TCodex wiki; edit when the platform adds or drops models.
TCODEX_MODELS = [{"id": m, "displayName": f"TCodex · {m}", "backend": "tcodex", "parameters": _EFFORT} for m in (
    "gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5",
    "glm-5.3-ioa", "glm-5.2-ioa", "glm-5.3-flash-ioa", "deepseek-v4-pro-ioa", "deepseek-v4.1-flash", "hy4-preview-ioa", "hy3-ioa")]
_procs = {}
# launchd starts the server with only the system PATH; tcodex (and the node an npm install needs) usually live here.
_PATH = os.pathsep.join([os.environ.get("PATH", ""), os.path.expanduser("~/.local/bin"), "/opt/homebrew/bin", "/usr/local/bin"])


def _tcodex_bin():
    return shutil.which("tcodex", path=_PATH)


def _codex_launch(r):
    placeholder, launch = r["id"], r["launch"] or {}
    images = _images.pop(placeholder, ())
    run_id = f"tc-{uuid.uuid4()}"
    try:
        if (store.get_run(placeholder) or {}).get("status") in ("CANCELLED", "EXPIRED"):
            return
        store.replace_run(placeholder, run_id, r["agent_id"], "RUNNING")
        _codex(r["agent_id"], run_id, r["prompt"], images, launch.get("model"))
    except Exception as e:
        store.update_run(run_id, status="ERROR", error=str(e))
    finally:
        _procs.pop(run_id, None)
        pump()


def _codex_args(session, text, images, model):
    # Cursor agents on this machine run unsandboxed too: the librarian needs the network and writes the data dir.
    args = [_tcodex_bin() or "tcodex", "exec", "--json", "--skip-git-repo-check", "--dangerously-bypass-approvals-and-sandbox", "-C", REPO_DIR]
    if model:
        args += ["-m", model["id"]]
        args += [a for p in model.get("params") or [] if p["id"] == "effort" for a in ("-c", f"model_reasoning_effort={p['value']}")]
    args += [f"--image={path}" for path in images]
    return args + (["resume", session, text] if session else [text])


def _codex(agent_id, run_id, text, images, model):
    """One Codex turn. Our agent id maps to Codex's session id (kv), so a follow-up resumes the same conversation."""
    key, started = f"codex:{agent_id}", time.time()
    out, tools, error = "", {}, None
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for i, (data, mime) in enumerate(images):
            paths.append(os.path.join(tmp, f"{i}.{mime.split('/')[-1]}"))
            with open(paths[-1], "wb") as f:
                f.write(data)
        with open(os.path.join(tmp, "stderr"), "w+") as err, \
                subprocess.Popen(_codex_args(store.kv_get(key), text, paths, model), cwd=REPO_DIR, stdin=subprocess.DEVNULL,
                                 env={**os.environ, "PATH": _PATH}, stdout=subprocess.PIPE, stderr=err, text=True, start_new_session=True) as p:
            _procs[run_id] = p
            for line in p.stdout:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                kind, item = ev.get("type"), ev.get("item") or {}
                if kind == "thread.started":
                    store.kv_set(key, ev["thread_id"])
                elif kind == "turn.failed":
                    error = (ev.get("error") or {}).get("message")
                elif kind == "error":
                    error = ev.get("message")
                elif item.get("type") == "agent_message":
                    out = item.get("text") or out
                elif item.get("type") in ("command_execution", "file_change", "mcp_tool_call", "web_search"):
                    tools[item["id"]] = {"name": item.get("tool") or item["type"], "status": "done" if kind == "item.completed" else "running",
                                         "args": _short(item) or _short({"path": ", ".join(c.get("path", "") for c in item.get("changes") or [])})}
                else:
                    continue
                store.update_run(run_id, text=out, tools=list(tools.values()))
            p.wait()
            err.seek(0)
            if p.returncode and not error:
                error = err.read()[-300:].strip() or f"tcodex 退出码 {p.returncode}"
    if (store.get_run(run_id) or {}).get("status") not in LIVE:
        return  # cancelled or expired here
    if error and not out:
        store.forget_agent(agent_id)  # a session that failed to start or resume: the next message starts fresh
        store.kv_set(key, "")
    store.update_run(run_id, status="ERROR" if error else "FINISHED", error=error, text=out, tools=list(tools.values()),
                     duration_ms=int((time.time() - started) * 1000))


def cancel(agent_id, run_id):
    if run_id.startswith("tc-"):
        store.update_run(run_id, status="CANCELLED")
        if run_id in _procs:
            os.killpg(_procs[run_id].pid, signal.SIGTERM)
        return
    _json("POST", f"/agents/{agent_id}/runs/{run_id}/cancel")


def resume_watchers():
    """After a restart: keep watching live runs; a run whose request was in flight is found by its agent id or, when
    it never reached Cursor, queued to be sent again (a repeated create is idempotent); waiting runs wait on."""
    for r in store.unfinished_runs():
        if r["id"].startswith("tc-"):
            store.update_run(r["id"], status="ERROR", error="服务重启，TCodex 进程已中断，请重试")
        elif not r["id"].startswith("pending-"):
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
            f"论文 {ref}《{p['title'] if p else ''}》。先运行 `./p2b show paper {ref}` 了解快读卡、用户说过的话和笔记；"
            f"需要细节时用 `./p2b text {ref} --pages 3-5` 读原文。"
        )
    if kind == "topic":
        return f"主题「{ref}」下的论文。先运行 `./p2b papers --topic '{ref}'`。"
    if kind == "capture":
        return (
            f"用户收下的一条内容（捕获 {ref}），这段讨论从他收下时说的话开始。先运行 `./p2b show capture {ref}`，"
            f"已关联论文的再用 `./p2b show paper <id>` 看快读卡。"
        )
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
    if kind == "research":
        x = store.get_research(ref)
        paper = (f"这个研究关联了 Overleaf 项目（`./p2b overleaf show {ref}` 给出本地目录）；用户要改论文时，按 librarian/writing.md "
                 f"的“改 Overleaf 论文”一节直接改本地文件，不要 commit 或 push。") if x and x["writing"]["overleaf"] else ""
        return (
            f"研究《{x['title'] if x else ''}》（{ref}）。再读 librarian/research.md，你是这个研究的合作研究者，按其中“研究讨论”一节做："
            f"先运行 `./p2b research show {ref}` 看三要素、素材、计划、实验和写作的现状；每一轮都让研究更清楚、更可做或更接近结果；"
            f"共识要落到研究页上时，三要素、计划、写作都只提交修改稿，由用户确认，不要声称研究已确立。"
            f"今天是 {datetime.now():%Y-%m-%d}。{paper}"
        )
    if kind == "writing":
        x = store.get_research(ref)
        paper = (f"论文在 Overleaf 写（`./p2b overleaf show {ref}` 给出本地目录），要改正文时按 librarian/writing.md 的“改 Overleaf 论文”一节"
                 f"直接改本地文件，不要 commit 或 push。") if x and x["writing"]["overleaf"] else ""
        return (
            f"研究《{x['title'] if x else ''}》（{ref}）的**写作讨论**：这里只迭代论文怎么写。再读 librarian/writing.md，"
            f"先运行 `./p2b writing show {ref}` 和 `./p2b research show {ref}` 看草稿、三要素和实验记录；摘要与引言用 `./p2b writing propose` "
            f"提交修改稿，Related Work 用 `./p2b related propose`（见 librarian/related.md）。{paper}"
            f"不要提交三要素或计划的修改稿；写作中发现三要素有问题，指出来，请用户到研究的概览讨论里调整。"
        )
    if kind == "related":
        x = store.get_research(ref)
        return (
            f"研究《{x['title'] if x else ''}》（{ref}）的**相关工作讨论**：这里只维护相关工作（研究方向分组、每条的 Related Work 段落和我们的区别，以及和最接近工作的对比表）。"
            f"再读 librarian/related.md，先运行 `./p2b related show {ref}` 和 `./p2b research show {ref}` 看现有方向、三要素和素材；"
            f"缺的论文先补找入库，改动（包括对比表，写进 `comparison` 字段）用 `./p2b related propose` 提交修改稿，"
            f"不要只把表或段落贴在回复里。不要提交三要素、计划或写作的修改稿；"
            f"发现新颖性有风险或三要素要调整，指出来，请用户到研究的概览讨论里调整。"
        )
    if kind == "plan":
        x = store.get_research(ref)
        return (
            f"研究《{x['title'] if x else ''}》（{ref}）的**计划讨论**：这里只排任务、看进度、对截止。再读 librarian/plan.md，"
            f"先运行 `./p2b plan show {ref}` 和 `./p2b research show {ref}` 看现有任务、三要素、实验和写作进度；改动用 `./p2b plan propose` "
            f"提交修改稿。今天是 {datetime.now():%Y-%m-%d}。不要提交三要素、写作或相关工作的修改稿；发现三要素要调整，指出来，请用户到研究的概览讨论里调整。"
        )
    if kind == "code":
        x = store.get_research(ref)
        return (
            f"研究《{x['title'] if x else ''}》（{ref}）的**代码讨论**：这里只围绕关联的代码仓库——复现细节、方法从哪里接进去、论文和代码的出入。"
            f"再读 librarian/code.md（尤其“讨论时读代码”一节），先运行 `./p2b research show {ref}` 看仓库和已有的方法—代码对照，"
            f"用 `./p2b repo <仓库 id>` 拿本地路径后只读地看代码；回答引用文件路径和行号。仓库只读，绝不修改、提交或推送。"
            f"不要提交三要素、计划或写作的修改稿。"
        )
    if kind == "experiment":
        x = store.get_research(ref)
        return (
            f"研究《{x['title'] if x else ''}》（{ref}）的**实验讨论**：这里只围绕实验——设计预期图表和下一个实验、解读结果、判断它支持还是反驳三要素。"
            f"再读 librarian/experiment.md，先运行 `./p2b research show {ref}` 看三要素、预期图表（`figures`）和已有实验记录；用户贴来新结果时按其中步骤用 "
            f"`./p2b experiment add` 记下、整理并填进预期图表的格子；要增删改预期图表时用 `./p2b figures-plan propose` 提交修改稿。"
            f"数字只能出自原始结果。不要提交三要素、计划或写作的修改稿；结论动摇了三要素，指出来，请用户到研究的概览讨论里调整。"
        )
    return "整个论文库。用 `./p2b search` 和 `./p2b papers` 检索。"


# Follow-ups sent from the web start with this, so the agent can tell them from what the user types in the Cursor app
# (those it logs back with ./p2b discuss log; see librarian/README.md).
WEB_MARK = "（网页讨论）"
# Long-lived agents read the spec once; each follow-up points back at its current version so new fields reach them.
LAND_SPECS = ("research", "writing", "related", "plan", "experiment")


def discuss(scope, text, thread_id=None, start=False, images=()):
    """Continue the scope's latest discussion, or an older one the user jumped back to. start=True opens a new
    research: the agent first reads what the user brought and drafts, instead of just answering."""
    thread = store.thread_for(scope, thread_id)
    if not thread["agent_id"] and scope.startswith("paper:") and model_for("capture") == model_for("discuss"):
        # the agent that took this paper's capture (from the phone, too) already knows it, and what was asked there
        agent = next((c["agent_id"] for c in reversed(store.get_paper(scope[6:], with_anchors=False)["captures"])
                      if c.get("agent_id")), None)
        if agent:
            store.set_thread_agent(thread["id"], agent)
            thread["agent_id"] = agent
    if thread["agent_id"] and any(m["run_id"] for m in thread["messages"]):
        spec = next((k for k in LAND_SPECS if scope.startswith(k + ":")), None)
        prompt, name = WEB_MARK + text + (f"\n\n（要落到研究页上的改动，先重读 librarian/{spec}.md 的当前版本，"
                                          f"按其中格式用 propose 提交修改稿，不要只贴在回复里。）" if spec else ""), None
    else:
        opening = "这是研究的开端：按 librarian/research.md 的“开启研究”一节处理。\n" if start else ""
        up = thread["parent"]
        up = f"这段讨论是从另一段分出来的：{up['id']}「{up['title'] or up['first'] or ''}」，需要更多上下文时用 `./p2b thread show {up['id']}`。\n" if up else ""
        # a branch inherits its ancestors up to the fork point, then what was said in it so far
        said = [(m["role"], m["text"]) for seg in thread["path"] for m in seg["messages"]] + \
               [(m["role"], m["run_text"] or m["text"]) for m in thread["messages"]]
        said = [f"{'用户' if role == 'user' else 'agent'}：{text[:600]}" for role, text in said if text][-14:]
        said = "这条讨论路径上已经说过的（从根到这里，按时间）：\n" + "\n".join(said) + "\n" if said else ""
        prompt = (
            f"先读 librarian/README.md 和 librarian/discuss.md。\n{opening}讨论范围：{scope_text(scope)}\n{up}"
            f"讨论线程 ID：{thread['id']}（关联研究素材或笔记时用 --thread）\n\n{said}用户{'的原话' if start else '的问题'}：{text}"
        )
        name = f"{'开启研究' if start else '讨论'}：{text[:40]}"

    def prepare(placeholder):
        store.add_message(thread["id"], "user", text)
        store.add_message(thread["id"], "assistant", "", placeholder)

    return start_async("chat", thread["id"], prompt, images, name=name, agent_id=thread["agent_id"], prepare=prepare,
                       module="research" if scope.startswith(("research:", "writing:", "related:", "plan:", "code:", "experiment:")) else "discuss")


ROLE_NAMES = {"method": "方法借鉴", "baseline": "baseline", "support": "支持证据", "counter": "反面证据", "reproduce": "待复现"}


def _seeds_text(rid):
    x = store.get_research(rid)
    title = lambda pid: (store.get_paper(pid, with_anchors=False) or {}).get("title", "")  # noqa: E731
    seeds = [f"- {l['ref']}《{l['label']}》" + (f"（用户标为：{ROLE_NAMES[l['role']]}）" if l["role"] else "")
             for l in x["links"] if l["kind"] == "paper" and not l["missing"]]
    rejected = [f"- {pid}《{title(pid)}》" for pid in store.rejected_in_research(rid)]
    return (
        f"\n\n这次是从研究出发找（按 librarian/find.md 的“从种子论文找”一节）：研究《{x['title']}》（{rid}），"
        f"`./p2b research show {rid}` 可看三要素。\n种子论文（已在研究里，不要重复挑）：\n" + "\n".join(seeds)
        + ("\n用户在之前几轮里标为不对路的（避开它们和同类）：\n" + "\n".join(rejected) if rejected else "")
    )


def find_papers(capture_id, research_id=None):
    """The request opens the discussion of the found set (scope find:<capture>): the same agent searches, links its
    picks to the capture, then compares them; follow-ups continue with it."""
    cap = store.get_capture(capture_id)
    thread = store.thread_for(f"find:{capture_id}")
    prompt = (
        f"先读 librarian/README.md、librarian/find.md 和 librarian/discuss.md。\n"
        f"用户请你找论文（捕获 ID：{capture_id}，讨论线程 ID：{thread['id']}）。\n\n用户的原话：{cap['raw']}"
        + (_seeds_text(research_id) if research_id else "")
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
    """Name untitled discussions in batches through one long-lived agent: a follow-up starts in seconds, while a new
    agent on this machine takes about a minute. Each discussion is tried at most twice."""
    if not status()["enabled"] or any(r["kind"] == "distill" for r in store.unfinished_runs()):
        return
    tried = json.loads(store.kv_get(DISTILL_TRIED) or "{}")
    ids = [i for i in store.pending_titles() if tried.get(i, 0) < 2]
    if not ids:
        return
    last = store.last_run("distill")
    if last and last["status"] == "ERROR" and "agent_busy" not in (last["error"] or ""):
        store.kv_set(DISTILL_AGENT, "")  # that agent is gone (archived or expired); start a fresh one
    store.kv_set(DISTILL_TRIED, json.dumps({**tried, **{i: tried.get(i, 0) + 1 for i in ids}}))
    text = f"先读 librarian/README.md 和 librarian/discuss.md，按“给讨论起标题”一节处理这些讨论：{' '.join(ids)}"
    start_async("distill", "threads", text, name="给讨论起标题", agent_id=store.kv_get(DISTILL_AGENT) or None)


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


CARD_TRIED = "card_tried"


def card_pending():
    """Every paper in the library gets a quick card: those that reach it without a capture agent writing one (found,
    added to a research, the capture agent gave up) are written one at a time, each tried automatically only once."""
    if not status()["enabled"]:
        return None
    with _deep_lock:
        if any(r["kind"] == "card" for r in store.unfinished_runs()):
            return None
        tried = set(json.loads(store.kv_get(CARD_TRIED) or "[]"))
        pid = next((p for p in store.card_queue() if p not in tried), None)
        if not pid:
            return None
        store.kv_set(CARD_TRIED, json.dumps(sorted(tried | {pid})))
        return write_card(pid)


def structure_experiment(eid):
    e = store.get_experiment(eid)
    text = (
        f"先读 librarian/README.md 和 librarian/experiment.md，整理实验 {eid}（属于研究 {e['research_id']}）：对照验证方案的成功线下结论，"
        f"用 ./p2b experiment put {eid} --base-rev {e['revision']} 提交，直到校验通过；"
        f"研究有预期图表时，再按“填预期图表”一节用 ./p2b figures-plan fill {e['research_id']} 把这条实验的结果填进对应格子。"
    )
    return start_async("experiment", eid, text, name=f"整理实验：{(e['title'] or e['raw'])[:40]}")


def draft_figures(rid, instruction=None):
    f, x = store.get_figures(rid), store.get_research(rid)
    text = (
        f"先读 librarian/README.md 和 librarian/experiment.md，按“实验设计”一节为研究 {rid}《{x['title']}》"
        f"{'更新' if f['figures'] else '设计'}实验，按论文 Experiments 一节组织：先定实验设置（研究问题、基准、对比方法、指标），"
        f"再设计主结果、消融、分析各要放的图表，每张回答一个研究问题、配好论文图注。"
        f"主结果必须和已发表的方法比（相关工作对比表里最接近的工作和领域最强的方法），自己方法的变体只算内部对照，"
        f"用 ./p2b figures-plan propose {rid} --base-rev {f['revision']} 提交，直到校验通过。"
        + (f"\n用户的要求：{instruction}" if instruction else "")
    )
    return start_async("figures", rid, text, name=f"预期图表：{x['title'][:50]}")


def fill_figures(rid):
    x = store.get_research(rid)
    text = (
        f"先读 librarian/README.md 和 librarian/experiment.md，按“填预期图表”一节，把研究 {rid}《{x['title']}》已有实验记录的结果"
        f"填进预期图表的格子：./p2b figures-plan fill {rid}，直到校验通过。只填有对应结果的格子，没有的留空。"
    )
    return start_async("figures", rid, text, name=f"填预期图表：{x['title'][:50]}")


def code_map(rid, repo_id, pid):
    repo, paper = store.get_repo(repo_id), store.get_paper(pid)
    text = (
        f"先读 librarian/README.md 和 librarian/code.md，为研究 {rid} 做论文 {pid}《{paper['title']}》"
        f"和代码仓库 {repo['name']}（仓库 id {repo_id}，本地路径 {repo['path']}）的方法—代码对照："
        f"复现关键细节一个不漏、按影响排序，并标出研究方法的切入点。用 ./p2b codemap put 提交，直到校验通过。仓库只读，不要修改任何文件。"
    )
    return start_async("codemap", rid, text, name=f"方法—代码对照：{repo['name']}")


def draw_diagram(rid, instruction=None, name=None, kind="method"):
    x, what = store.get_research(rid), {"method": "方法框架图", "motivation": "动机图"}[kind]
    text = (
        f"先读 librarian/README.md 和 librarian/diagram.md，为研究 {rid}《{x['title']}》"
        + (f"修改{what} {name}（./p2b diagram list {rid} 里有它的源文件，改完用同一个 --name 渲染覆盖）" if name
           else f"画论文的{what}（--kind {kind}）")
        + "，要达到大厂技术报告的插图水准："
        + ("先从研究问题、核心洞见和相关工作弄清要讲的对比" if kind == "motivation" else "先从研究方法、代码对照和关联仓库弄清结构")
        + "，写 HTML/SVG，用 ./p2b diagram render 渲染，再打开渲染出的 PNG 按检查清单逐条看、改、重新渲染，直到挑不出毛病。"
        "仓库只读，不要修改任何文件。"
        + (f"\n用户的要求：{instruction}" if instruction else "")
    )
    return start_async("diagram" if kind == "method" else "motivation", rid, text, name=f"{'改' if name else '画'}{what}：{x['title'][:50]}")


def draft_writing(rid, instruction=None):
    w, x = store.get_writing(rid), store.get_research(rid)
    text = (
        f"先读 librarian/README.md 和 librarian/writing.md，按“摘要与引言草稿”一节为研究 {rid}《{x['title']}》"
        f"{'修改' if w['abstract'] or w['introduction'] else '起草'}摘要和引言（数字只来自实验记录），"
        f"用 ./p2b writing propose {rid} --base-rev {w['revision']} 提交，直到校验通过。"
        + (f"\n用户的要求：{instruction}" if instruction else "")
    )
    return start_async("writing", rid, text, name=f"写作：{x['title'][:50]}")


def draft_related(rid):
    r, x = store.get_related(rid), store.get_research(rid)
    text = (
        f"先读 librarian/README.md 和 librarian/related.md，为研究 {rid}《{x['title']}》{'更新' if r['groups'] else '梳理'}相关工作"
        f"（分成几条研究方向，每条说清这条线做到哪、局限在哪、我们差在哪，并附一张最接近的工作和我们的对比表），"
        f"用 ./p2b related propose {rid} --base-rev {r['revision']} 提交，直到校验通过。"
    )
    return start_async("related", rid, text, name=f"相关工作：{x['title'][:50]}")


def draft_plan(rid, instruction=None):
    p, x = store.get_plan(rid), store.get_research(rid)
    text = (
        f"先读 librarian/README.md 和 librarian/plan.md，为研究 {rid}《{x['title']}》{'按实验记录更新进度、必要时重排' if p['tasks'] else '制定'}计划"
        f"（先验证最大的风险，每条任务做没做完一眼看得出），"
        f"用 ./p2b plan propose {rid} --base-rev {p['revision']} 提交，直到校验通过。今天是 {datetime.now():%Y-%m-%d}。"
        + (f"\n用户的要求：{instruction}" if instruction else "")
    )
    return start_async("plan", rid, text, name=f"计划：{x['title'][:50]}")


def draft_research(rid):
    x = store.get_research(rid)
    text = (
        f"先读 librarian/README.md 和 librarian/research.md，按“起草三要素”一节为研究 {rid}《{x['title']}》起草三要素修改稿："
        f"对照“好的研究长什么样”逐条写，素材没覆盖的写“待确认”。用 ./p2b research propose {rid} --base-rev {x['revision']} 提交，"
        f"直到校验通过。修改稿由用户确认后才生效。"
    )
    return start_async("draft", rid, text, name=f"起草研究：{x['title'][:60]}")


def deep_read(pid):
    p = store.get_paper(pid)
    text = (
        f"先读 librarian/README.md 和 librarian/deepread.md，为论文 {pid}《{p['title']}》写深读报告，"
        f"用 ./p2b report put 提交，直到校验通过。"
    )
    return start_async("deepread", pid, text, name=f"深读：{p['title'][:60]}")
