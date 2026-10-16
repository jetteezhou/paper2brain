"""iCloud Drive inbox: what the iPhone shortcut writes before calling the API, so nothing is lost while the Mac is
offline. Files: <capture_id>.json ({"id", "text", "thought"}) plus images named <capture_id>-<n>.<ext>."""
import json
import mimetypes
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import pipeline, store

# Test servers on a scratch P2B_HOME must point this elsewhere, or they would take the real captures.
INBOX = Path(os.environ.get("P2B_INBOX") or Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/paper2brain-inbox")
# The shortcut also calls the API right away; give that agent time to take the capture before we start a second one.
GRACE_SECONDS = 180


def drain():
    if not INBOX.exists():
        return []
    for placeholder in INBOX.glob(".*.icloud"):
        subprocess.run(["brctl", "download", str(placeholder)], capture_output=True)
    done = INBOX / "done"
    done.mkdir(exist_ok=True)
    out = []
    for f in sorted(INBOX.glob("*.json")):
        if time.time() - f.stat().st_mtime < GRACE_SECONDS:
            continue
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue  # still syncing; next round
        cid = data.get("id") or f.stem
        imgs = sorted(p for p in INBOX.glob(f"{cid}-*") if not p.name.endswith(".icloud"))
        images = [(p.name, p.read_bytes(), mimetypes.guess_type(p.name)[0] or "image/jpeg") for p in imgs]
        _, created = store.add_capture(cid, data.get("text", ""), "shortcut", data.get("thought"), images)
        if created:
            pipeline.process(cid)
        for p in (f, *imgs):
            shutil.move(str(p), done / p.name)
        out.append({"id": cid, "created": created})
    return out
