"""Experimental: two Macs using the library at the same time (config.json "sync": true on both).

Rows: each Mac writes the rows it changed (store.SYNCED) to its own folder under <iCloud handoff folder>/changes/<host>/,
one file per round, and applies the other Mac's files; the newest change of each row wins. A row whose parent has not
arrived yet waits in sync_pending and is tried again every round. Files (PDFs, figures, diagrams, code checkouts) go
both ways through <handoff folder>/data, the newer copy winning.
ponytail: deletions of files do not propagate (a deleted paper's folder stays on the other Mac, unused); Overleaf
checkouts are git working copies both Macs may commit in, so they stay local and sync through Overleaf itself.
"""
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time

from . import handoff, store

KEEP_LOCAL = {"threads": {"agent_id"}, "captures": {"agent_id"}}  # an agent runs on the Mac that started it
NAME = re.compile(r"^\.?(\d{12})\.jsonl(\.icloud)?$")
FILES = ("papers", "diagrams", "repos")


def enabled():
    return bool(store.config().get("sync"))


def folder():
    return handoff.DROP / "changes"


def _kv(c, key, default="0"):
    r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def _where(c, t, pk):
    cols = store.primary_key(c, t)
    if not isinstance(pk, list) or len(pk) != len(cols):
        raise ValueError(f"bad key for {t}")
    return " AND ".join(f"{k}=?" for k in cols), pk


def export():
    """This Mac's changes since the last round, as one file. Returns how many rows went out."""
    with store.tx() as c:
        rows = c.execute("SELECT * FROM sync_rows WHERE mine=1 AND seq>? ORDER BY seq", (int(_kv(c, "sync:exported")),)).fetchall()
        if not rows:
            return 0
        lines = [json.dumps({"home": str(store.home())})]
        for r in rows:
            if r["tbl"] not in store.SYNCED:
                continue
            pk = json.loads(r["pk"])
            where, args = _where(c, r["tbl"], pk)
            row = c.execute(f"SELECT * FROM {r['tbl']} WHERE {where}", args).fetchone()
            lines.append(json.dumps({"tbl": r["tbl"], "pk": pk, "at": r["at"], "row": dict(row) if row else None}, ensure_ascii=False))
        out = folder() / handoff.ME
        out.mkdir(parents=True, exist_ok=True)
        name = f"{rows[-1]['seq']:012d}.jsonl"
        (out / f"{name}.tmp").write_text("\n".join(lines) + "\n")
        (out / f"{name}.tmp").replace(out / name)
        c.execute("INSERT OR REPLACE INTO kv VALUES ('sync:exported', ?)", (str(rows[-1]["seq"]),))
    return len(rows)


def _rehome(value, there, here):
    return here + value[len(there):] if isinstance(value, str) and there and value.startswith(there) else value


def _apply(c, e, there=None):
    t = e.get("tbl")
    if t not in store.SYNCED:
        return
    key = json.dumps(e["pk"])
    have = c.execute("SELECT at FROM sync_rows WHERE tbl=? AND pk=?", (t, key)).fetchone()
    if have and have["at"] >= e["at"]:
        return
    where, args = _where(c, t, e["pk"])
    if e["row"] is None:
        c.execute(f"DELETE FROM {t} WHERE {where}", args)
    else:
        known, pks = {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}, store.primary_key(c, t)
        keep, here = KEEP_LOCAL.get(t, set()), str(store.home()).rstrip("/") + "/"
        cols = [k for k in e["row"] if k in known]  # names come from a file: only this table's own columns
        sets = ", ".join(f"{k}=excluded.{k}" for k in cols if k not in pks and k not in keep)
        c.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
                  f"ON CONFLICT({', '.join(pks)}) DO " + (f"UPDATE SET {sets}" if sets else "NOTHING"),
                  [None if k in keep else _rehome(e["row"][k], there, here) for k in cols])
    c.execute("INSERT OR REPLACE INTO sync_rows (tbl, pk, at, mine) VALUES (?,?,?,0)", (t, key, e["at"]))


def _entry(line):
    try:
        e = json.loads(line)
    except ValueError:
        return None
    if isinstance(e, dict) and isinstance(e.get("pk"), str):
        e["pk"] = [e["pk"]]  # files written before keys became arrays
    ok = isinstance(e, dict) and isinstance(e.get("pk"), list) and isinstance(e.get("at"), str) \
        and (e.get("row") is None or isinstance(e["row"], dict))
    return e if ok else None


def _try(c, e, there=None):
    if e is None:
        return True  # a damaged line: drop it rather than retry it forever
    c.execute("SAVEPOINT row")
    try:
        _apply(c, e, there)
        c.execute("RELEASE row")
        return True
    except (sqlite3.IntegrityError, ValueError):  # its parent is not here yet, or its key does not fit
        c.execute("ROLLBACK TO row")
        c.execute("RELEASE row")
        return False


def apply_others(include_self=False):
    """Apply what the other Macs wrote since last time, in order; a file iCloud has not downloaded yet stops that
    Mac's queue until it arrives. include_self (after a pull): this Mac's own files too, from the start, since the
    snapshot it just took may predate them. Returns how many rows were applied or queued."""
    root = folder()
    if not root.exists():
        return 0
    n = 0

    def retry():
        for (line,) in c.execute("SELECT line FROM sync_pending").fetchall():
            e = _entry(line)
            if _try(c, e, e and e.get("_home")):
                c.execute("DELETE FROM sync_pending WHERE line=?", (line,))
    with store.tx() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES ('sync:applying', '1')")
        try:
            retry()
            for d in sorted(p for p in root.iterdir() if p.is_dir() and (include_self or p.name != handoff.ME)):
                seen = 0 if d.name == handoff.ME else int(_kv(c, f"sync:seen:{d.name}"))
                files = sorted((int(m[1]), bool(m[2]), f) for f in d.iterdir() if (m := NAME.match(f.name)))
                for seq, placeholder, f in files:
                    if seq <= seen:
                        continue
                    if placeholder:
                        break
                    there = None
                    for line in f.read_text().splitlines():
                        e = _entry(line)
                        if e is None and line.startswith('{"home"'):  # the file's first line: the sender's data folder
                            try:
                                there = str(json.loads(line)["home"]).rstrip("/") + "/"
                            except (ValueError, KeyError, TypeError):
                                pass
                        elif line.strip() and not _try(c, e, there):
                            c.execute("INSERT OR IGNORE INTO sync_pending VALUES (?)", (json.dumps({**e, "_home": there}, ensure_ascii=False),))
                            n += 1
                        else:
                            n += bool(line.strip())
                    seen = seq
                if d.name != handoff.ME:
                    c.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (f"sync:seen:{d.name}", str(seen)))
            retry()  # a child listed before its parent in the same round
        finally:
            c.execute("DELETE FROM kv WHERE key='sync:applying'")
    return n


def push_files():
    """This Mac's new or newer files up to iCloud. A file iCloud keeps only in the cloud (.name.icloud) is left
    alone: copying over it would leave iCloud two versions to reconcile."""
    data = handoff.DROP / "data"
    data.mkdir(parents=True, exist_ok=True)
    gone = [str(p.relative_to(data).with_name(p.name[1:-7])) for p in data.rglob(".*.icloud")]
    with tempfile.NamedTemporaryFile("w", suffix=".txt") as skip:
        skip.write("".join(f"/{g}\n" for g in gone))
        skip.flush()
        for d in FILES:
            if (store.home() / d).exists():
                subprocess.run(["rsync", "-a", "--update", f"--exclude-from={skip.name}", "--exclude=*.tmp",
                                f"{store.home() / d}/", f"{data / d}/"], check=True)


def pull_files():
    data = handoff.DROP / "data"
    for d in FILES:
        if (data / d).exists():
            subprocess.run(["rsync", "-a", "--update", "--exclude=*.icloud", "--exclude=*.tmp", f"{data / d}/", f"{store.home() / d}/"], check=True)


LOCAL_SETTINGS = ("machine",)  # each Mac's own Cursor worker name


def sync_config():
    """Settings and keys (config.json) follow the Mac that changed them last. settings.json in iCloud carries when
    they changed by that Mac's clock, so iCloud's own file times don't matter. Everything reads config.json on every
    use; only the Cursor worker reads its key once, so a new key restarts it."""
    mine, shared = store.home() / "config.json", handoff.DROP / "settings.json"
    if not mine.exists():
        return None
    with store.tx() as c:
        done, seen = float(_kv(c, "sync:config_done")), float(_kv(c, "sync:config_seen"))
    now = mine.stat().st_mtime
    try:
        theirs = json.loads(shared.read_text()) if shared.exists() else None
    except ValueError:
        theirs = None  # mid-download: next round
    local = json.loads(mine.read_text())
    other = bool(theirs) and theirs.get("host") != handoff.ME and isinstance(theirs.get("config"), dict)
    first = done == 0 and other  # never synced here: take the shared keys before sending this Mac's, maybe empty, ones
    if now > done and not first:
        at = time.time()
        shared.write_text(json.dumps({"at": at, "host": handoff.ME, "config": {k: v for k, v in local.items() if k not in LOCAL_SETTINGS}},
                                     ensure_ascii=False))
        marks, result = {"sync:config_done": now, "sync:config_seen": at}, "sent"
    elif other and (first or float(theirs.get("at") or 0) > seen):
        # merged, never dropped: a setting only this Mac has stays
        new = {**local, **theirs["config"], **{k: local[k] for k in LOCAL_SETTINGS if k in local}}
        mine.write_text(json.dumps(new, indent=2, ensure_ascii=False))
        mine.chmod(0o600)
        if new.get("cursor_api_key") != local.get("cursor_api_key"):
            subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.paper2brain.worker"], capture_output=True)
        marks, result = {"sync:config_done": mine.stat().st_mtime, "sync:config_seen": float(theirs["at"])}, "taken"
    else:
        return None
    with store.tx() as c:
        c.executemany("INSERT OR REPLACE INTO kv VALUES (?, ?)", [(k, str(v)) for k, v in marks.items()])
    return result


def run():
    """Background job, every round of the server loop."""
    if not enabled():
        return
    subprocess.run(["brctl", "download", str(handoff.DROP)], capture_output=True)  # fetch what the other Mac uploaded
    sync_config()
    pull_files()
    apply_others()
    export()
    push_files()
