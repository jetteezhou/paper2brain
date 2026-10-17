"""iCloud Drive inbox: what the iPhone shortcut writes before calling the API, so nothing is lost while the Mac is
offline. Files: <capture_id>.json ({"id", "text", "thought", "local"}) plus images named <capture_id>-<n>.<ext>.
"local": true marks a capture the shortcut did not send to the API (a repeated clipboard)."""
import json
import mimetypes
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import pipeline, rules, store

# Test servers on a scratch P2B_HOME must point this elsewhere, or they would take the real captures.
INBOX = Path(os.environ.get("P2B_INBOX") or Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/paper2brain-inbox")
# The shortcut also calls the API right away; give that agent time to take the capture before we start a second one
# (creating it takes about a minute, then it reads the spec before running ./p2b capture add).
GRACE_SECONDS = 600


def drain():
    if not INBOX.exists():
        return []
    for placeholder in INBOX.glob(".*.icloud"):
        subprocess.run(["brctl", "download", str(placeholder)], capture_output=True)
    done = INBOX / "done"
    done.mkdir(exist_ok=True)
    out = []
    for f in sorted(INBOX.glob("*.json")):
        if f.name.endswith(".agent.json"):
            continue
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue  # still syncing; next round
        cid = data.get("id") or f.stem
        imgs = sorted(p for p in INBOX.glob(f"{cid}-*") if not p.name.endswith(".icloud"))
        reply_file = INBOX / f"{cid}.agent.json"
        try:
            reply = json.loads(reply_file.read_text())
        except (json.JSONDecodeError, OSError):
            reply = None
        agent = ((reply or {}).get("agent") or {}).get("id")
        # A bare thought or a repeated clipboard never went to the API, and a reply settles whether it did;
        # otherwise wait for the agent the shortcut started.
        handed_over = (data.get("text") or "").strip() and not data.get("local")
        if handed_over and reply is None and time.time() - f.stat().st_mtime < GRACE_SECONDS:
            continue
        images = [(p.name, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/jpeg") for p in imgs]
        try:
            got = pipeline.receive(cid, data.get("text", ""), "shortcut", data.get("thought"), images)
        except rules.Rejected:
            got = {"kind": "empty", "created": False}
        created = got["created"]
        if got["kind"] == "capture" and agent:
            _adopt(cid, agent, (reply.get("run") or {}))
        if got["kind"] == "capture" and created:
            pipeline.process(cid, agent=not agent)
        for p in (f, *imgs, *([reply_file] if reply is not None else [])):
            shutil.move(str(p), done / p.name)
        out.append({"id": cid, "created": created})
    for reply_file in INBOX.glob("*.agent.json"):  # replies that arrived after their capture was handled
        cid = reply_file.name.removesuffix(".agent.json")
        if (INBOX / f"{cid}.json").exists():
            continue
        try:
            reply = json.loads(reply_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if store.get_capture(cid) and (reply.get("agent") or {}).get("id"):
            _adopt(cid, reply["agent"]["id"], reply.get("run") or {})
        shutil.move(str(reply_file), done / reply_file.name)
    return out


def _adopt(cid, agent_id, run):
    """The agent the shortcut started: shown on the capture as working, and continued by "讨论这篇" on the PC."""
    from . import cursor_api
    store.update_capture(cid, agent_id=agent_id)
    if run.get("id") and not store.get_run(run["id"]):
        store.add_run(run["id"], agent_id, "capture", cid, "（iPhone 快捷指令启动）", run.get("status") or "RUNNING")
        cursor_api.watch(agent_id, run["id"])
