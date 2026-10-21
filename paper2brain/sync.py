"""Experimental: two Macs using the library at the same time (config.json "sync": true on both).

Each Mac writes the rows it changed (store.SYNCED: discussions with their finished runs, notes, pinned passages) to
its own folder under <iCloud handoff folder>/changes/<host>/, one file per round, and applies the other Mac's files;
the newest change of each row wins. A row whose parent has not arrived yet (a note on a paper only the other Mac
has) waits in sync_pending and is tried again every round.
ponytail: papers, cards, topics and research pages do not sync yet; they still move with handoff push / pull.
"""
import json
import re
import sqlite3
import subprocess

from . import handoff, store

KEEP_LOCAL = {"threads": {"agent_id"}}  # an agent runs on the Mac that started it; the other Mac starts its own
NAME = re.compile(r"^\.?(\d{12})\.jsonl(\.icloud)?$")


def enabled():
    return bool(store.config().get("sync"))


def folder():
    return handoff.DROP / "changes"


def _kv(c, key, default="0"):
    r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def export():
    """This Mac's changes since the last round, as one file. Returns how many rows went out."""
    with store.tx() as c:
        rows = c.execute("SELECT * FROM sync_rows WHERE mine=1 AND seq>? ORDER BY seq", (int(_kv(c, "sync:exported")),)).fetchall()
        if not rows:
            return 0
        lines = []
        for r in rows:
            row = c.execute(f"SELECT * FROM {r['tbl']} WHERE id=?", (r["pk"],)).fetchone()
            lines.append(json.dumps({"tbl": r["tbl"], "pk": r["pk"], "at": r["at"], "row": dict(row) if row else None}, ensure_ascii=False))
        out = folder() / handoff.ME
        out.mkdir(parents=True, exist_ok=True)
        name = f"{rows[-1]['seq']:012d}.jsonl"
        (out / f"{name}.tmp").write_text("\n".join(lines) + "\n")
        (out / f"{name}.tmp").replace(out / name)
        c.execute("INSERT OR REPLACE INTO kv VALUES ('sync:exported', ?)", (str(rows[-1]["seq"]),))
    return len(rows)


def _apply(c, e):
    t = e.get("tbl")
    if t not in store.SYNCED:
        return
    have = c.execute("SELECT at FROM sync_rows WHERE tbl=? AND pk=?", (t, e["pk"])).fetchone()
    if have and have["at"] >= e["at"]:
        return
    if e["row"] is None:
        c.execute(f"DELETE FROM {t} WHERE id=?", (e["pk"],))
    else:
        known = {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
        keep = KEEP_LOCAL.get(t, set())
        cols = [k for k in e["row"] if k in known]  # names come from a file: only this table's own columns
        sets = ", ".join(f"{k}=excluded.{k}" for k in cols if k != "id" and k not in keep) or "id=id"
        c.execute(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) ON CONFLICT(id) DO UPDATE SET {sets}",
                  [None if k in keep else e["row"][k] for k in cols])
    c.execute("INSERT OR REPLACE INTO sync_rows (tbl, pk, at, mine) VALUES (?,?,?,0)", (t, e["pk"], e["at"]))


def _entry(line):
    try:
        e = json.loads(line)
    except ValueError:
        return None
    ok = isinstance(e, dict) and isinstance(e.get("pk"), str) and isinstance(e.get("at"), str) \
        and (e.get("row") is None or isinstance(e["row"], dict))
    return e if ok else None


def _try(c, e):
    if e is None:
        return True  # a damaged line: drop it rather than retry it forever
    c.execute("SAVEPOINT row")
    try:
        _apply(c, e)
        c.execute("RELEASE row")
        return True
    except sqlite3.IntegrityError:  # its thread or paper is not here yet
        c.execute("ROLLBACK TO row")
        c.execute("RELEASE row")
        return False


def apply_others():
    """Apply what the other Macs wrote since last time, in order; a file iCloud has not downloaded yet stops that
    Mac's queue until it arrives. Returns how many rows were applied or queued."""
    root = folder()
    if not root.exists():
        return 0
    subprocess.run(["brctl", "download", str(root)], capture_output=True)
    n = 0
    with store.tx() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES ('sync:applying', '1')")
        try:
            for (line,) in c.execute("SELECT line FROM sync_pending").fetchall():
                if _try(c, _entry(line)):
                    c.execute("DELETE FROM sync_pending WHERE line=?", (line,))
            for d in sorted(p for p in root.iterdir() if p.is_dir() and p.name != handoff.ME):
                seen = int(_kv(c, f"sync:seen:{d.name}"))
                files = sorted((int(m[1]), bool(m[2]), f) for f in d.iterdir() if (m := NAME.match(f.name)))
                for seq, placeholder, f in files:
                    if seq <= seen:
                        continue
                    if placeholder:
                        break
                    for line in f.read_text().splitlines():
                        if line.strip() and not _try(c, _entry(line)):
                            c.execute("INSERT OR IGNORE INTO sync_pending VALUES (?)", (line,))
                        n += 1
                    seen = seq
                c.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (f"sync:seen:{d.name}", str(seen)))
        finally:
            c.execute("DELETE FROM kv WHERE key='sync:applying'")
    return n


def run():
    """Background job, every round of the server loop."""
    if enabled():
        apply_others()
        export()
