"""Move the library between Macs through iCloud Drive, one machine at a time.

  .venv/bin/python ops/handoff.py push   # on the Mac you are leaving: snapshot everything into iCloud
  .venv/bin/python ops/handoff.py pull   # on the Mac you switch to, once iCloud has synced

The live database never sits in iCloud (iCloud syncing SQLite's -wal/-shm files separately corrupts it, and launchd
jobs may not read iCloud files): push copies a consistent snapshot there, pull stops this Mac's server, keeps a
backup of its database, takes the snapshot, points stored paths at this Mac and starts the server again.
"""
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paper2brain import store  # noqa: E402

DROP = Path(os.environ.get("P2B_HANDOFF") or Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/paper2brain-handoff")
# local to each machine: the live database, its backups, the worker's state, the tunnel/server logs
SKIP = ["p2b.sqlite", "p2b.sqlite-wal", "p2b.sqlite-shm", "backups/", "worker/", "config.json", "*.tmp"]
JOBS = ("com.paper2brain.server", "com.paper2brain.worker")


def rsync(src, dst, *extra):
    subprocess.run(["rsync", "-a", "--delete", *[f"--exclude={x}" for x in SKIP], *extra, f"{src}/", f"{dst}/"], check=True)


def push():
    home = store.home()
    DROP.mkdir(parents=True, exist_ok=True)
    (DROP / "data").mkdir(exist_ok=True)
    rsync(home, DROP / "data")
    snap = store.backup(f"handoff-{datetime.now():%Y%m%d-%H%M%S}.sqlite")  # sqlite backup API: consistent while the server runs
    subprocess.run(["cp", str(snap), str(DROP / "p2b.sqlite")], check=True)
    snap.unlink()
    subprocess.run(["cp", str(home / "config.json"), str(DROP / "config.json")], check=True)
    (DROP / "handoff.json").write_text(json.dumps({"home": str(home), "host": socket.gethostname(), "at": datetime.now().isoformat(timespec="seconds")}))
    print(f"已放进 iCloud：{DROP}\n等 iCloud 上传完（访达里 iCloud 云盘的云朵图标不再转），再到另一台 Mac 上 pull。这台之后别再用，除非再从 iCloud pull 回来。")


def _downloaded():
    subprocess.run(["brctl", "download", str(DROP)], capture_output=True)  # ask iCloud to fetch files kept only in the cloud
    for _ in range(120):
        if not any(DROP.rglob("*.icloud")):
            return True
        time.sleep(5)
    return False


def _jobs(action):
    uid, agents = os.getuid(), Path.home() / "Library/LaunchAgents"
    for label in JOBS:
        plist = agents / f"{label}.plist"
        if plist.exists():
            subprocess.run(["launchctl", action, f"gui/{uid}", str(plist)] if action == "bootstrap" else
                           ["launchctl", "bootout", f"gui/{uid}/{label}"], capture_output=True)


def _listening():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", 8765)) == 0


def pull():
    meta = json.loads((DROP / "handoff.json").read_text()) if (DROP / "handoff.json").exists() else None
    if not meta:
        sys.exit(f"{DROP} 里没有交接快照，先在另一台 Mac 上 push")
    if not _downloaded():
        sys.exit("iCloud 还没把文件下载完，过一会儿再 pull")
    home = store.home()
    _jobs("bootout")
    time.sleep(2)
    if _listening():
        sys.exit("本机的 paper2brain 还在运行（多半是终端里的 ops/run.sh），先停掉再 pull")
    if (home / "p2b.sqlite").exists():
        print("本机原来的数据库已备份到", store.backup(f"p2b-before-pull-{datetime.now():%Y%m%d-%H%M%S}.sqlite"))
    home.mkdir(parents=True, exist_ok=True)
    rsync(DROP / "data", home)
    for f in ("p2b.sqlite-wal", "p2b.sqlite-shm"):
        (home / f).unlink(missing_ok=True)
    subprocess.run(["cp", str(DROP / "p2b.sqlite"), str(home / "p2b.sqlite")], check=True)
    config = json.loads((DROP / "config.json").read_text())
    mine = home / "config.json"
    if mine.exists() and "machine" in json.loads(mine.read_text()):
        config["machine"] = json.loads(mine.read_text())["machine"]  # each Mac is its own Cursor worker
    mine.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    mine.chmod(0o600)
    moved = store.rehome(meta["home"])
    _jobs("bootstrap")
    print(f"已接手 {meta['host']} 在 {meta['at']} 交出的论文库" + (f"，改了 {moved} 条文件路径" if moved else "") + "。")


if __name__ == "__main__":
    {"push": push, "pull": pull}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
