"""Move the library between Macs through iCloud Drive (ops/handoff.py push / pull).

The live database never sits in iCloud (iCloud syncing SQLite's -wal/-shm files separately corrupts it, and launchd
jobs may not read iCloud files): a snapshot goes there instead. handoff.json names the Mac holding the library
("owner"); while it runs, its server leaves a fresh snapshot every half hour (auto_push), so a forgotten push
loses at most that much. push stops this Mac and hands the library off; pull takes it and becomes the owner.
"""
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import store

DROP = Path(os.environ.get("P2B_HANDOFF") or Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/paper2brain-handoff")
ME = socket.gethostname()
# local to each machine: the live database, its backups, the worker's state
SKIP = ["p2b.sqlite", "p2b.sqlite-wal", "p2b.sqlite-shm", "backups/", "worker/", "config.json", "*.tmp"]
JOBS = ("com.paper2brain.server", "com.paper2brain.worker")
AUTO_EVERY = 1800


def rsync(src, dst):
    subprocess.run(["rsync", "-a", "--delete", *[f"--exclude={x}" for x in SKIP], f"{src}/", f"{dst}/"], check=True)


def meta():
    path = DROP / "handoff.json"
    return json.loads(path.read_text()) if path.exists() else None


def _snapshot(owner):
    home = store.home()
    (DROP / "data").mkdir(parents=True, exist_ok=True)
    rsync(home, DROP / "data")
    snap = store.backup(f"handoff-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite")  # sqlite backup API: consistent while running
    subprocess.run(["cp", str(snap), str(DROP / "p2b.sqlite.tmp")], check=True)
    snap.unlink()
    (DROP / "p2b.sqlite.tmp").replace(DROP / "p2b.sqlite")
    subprocess.run(["cp", str(home / "config.json"), str(DROP / "config.json")], check=True)
    (DROP / "handoff.json").write_text(json.dumps({"home": str(home), "host": ME, "owner": owner,
                                                   "at": datetime.now().isoformat(timespec="seconds")}))


_pushed = 0.0


def auto_push():
    """Background job: the owner (or the first Mac, before any handoff) refreshes the snapshot. Only once a handoff
    folder exists, so nobody gets a 1 GB iCloud upload they never asked for."""
    global _pushed
    if time.time() - _pushed < AUTO_EVERY or not DROP.exists():
        return
    _pushed = time.time()
    m = meta()
    if m and m.get("owner") not in (None, ME):
        return
    # ponytail: the other Mac can pull between this check and the write below; then run pull there once more
    _snapshot(owner=ME)


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


def _stop(verb):
    _jobs("bootout")  # nothing written here after the snapshot, and only one worker answers to a shared name
    time.sleep(2)
    if _listening():
        sys.exit(f"本机的 paper2brain 还在运行（多半是终端里的 ops/run.sh），先停掉再 {verb}")


def push():
    _stop("push")
    from . import sync
    if sync.enabled():
        sync.export()
    _snapshot(owner=None)
    print(f"已放进 iCloud：{DROP}，本机服务已停。\n等 iCloud 上传完（访达里 iCloud 云盘的云朵图标不再转），再到另一台 Mac 上 pull。"
          "要回到这台，先在那台 push，再在这里 pull。")


def _downloaded():
    subprocess.run(["brctl", "download", str(DROP)], capture_output=True)  # ask iCloud to fetch files kept only in the cloud
    for _ in range(120):
        if not any(DROP.rglob("*.icloud")):
            return True
        time.sleep(5)
    return False


def pull():
    m = meta()
    if not m:
        sys.exit(f"{DROP} 里没有交接快照，先在另一台 Mac 上 push")
    if not _downloaded():
        sys.exit("iCloud 还没把文件下载完，过一会儿再 pull")
    home = store.home()
    _stop("pull")
    if (home / "p2b.sqlite").exists():
        from . import sync
        if sync.enabled():
            sync.export()  # what changed here since the last round still reaches the other Mac
        print("本机原来的数据库已备份到", store.backup(f"p2b-before-pull-{datetime.now():%Y%m%d-%H%M%S}.sqlite"))
    home.mkdir(parents=True, exist_ok=True)
    rsync(DROP / "data", home)
    for f in ("p2b.sqlite-wal", "p2b.sqlite-shm"):
        (home / f).unlink(missing_ok=True)
    subprocess.run(["cp", str(DROP / "p2b.sqlite"), str(home / "p2b.sqlite")], check=True)
    config, mine = json.loads((DROP / "config.json").read_text()), home / "config.json"
    if mine.exists() and "machine" in json.loads(mine.read_text()):
        config["machine"] = json.loads(mine.read_text())["machine"]  # this Mac's own worker name, when it has one
    mine.write_text(json.dumps(config, indent=2, ensure_ascii=False))
    mine.chmod(0o600)
    moved = store.rehome(m["home"])
    (DROP / "handoff.json").write_text(json.dumps({**m, "owner": ME}))
    _jobs("bootstrap")
    print(f"已接手 {m['host']} 在 {m['at']} 交出的论文库" + (f"，改了 {moved} 条文件路径" if moved else "") + "。")
