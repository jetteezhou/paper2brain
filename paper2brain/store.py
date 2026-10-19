"""SQLite storage. The only module that touches the database; every write goes through a transaction here."""
import json
import re
import os
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import rules

TOPIC_TREE = [
    ("VLM", ["具身大脑", "Jev 决策模型", "通用多模态大模型", "扩散VLM"]),
    ("VLA", ["tokenizer", "推理加速", "实时推理"]),
    ("WAM", ["世界模型", "表征研究", "WAN base", "V-JEPA"]),
]
TRIAGE = ("inbox", "deep", "archived", "ignored")
JSON_COLS = {"links", "candidates", "authors", "card", "report", "tools", "meta", "body", "proposal", "results", "rows", "launch", "for_papers", "tasks", "groups", "comparison", "charts", "figures", "cells", "design"}
# WAITING: queued here until there is room to start it; the others are Cursor's own statuses of a live run.
UNFINISHED = ("WAITING", "CREATING", "RUNNING", "QUEUED")
_UNFINISHED_SQL = "('WAITING','CREATING','RUNNING','QUEUED')"
RESEARCH_ELEMENTS = ("problem", "method", "validation")
RESEARCH_FIELDS = RESEARCH_ELEMENTS + ("notes",)
LINK_KINDS = ("paper", "message", "thread", "capture", "note", "doc")
MATERIAL_ROLES = ("method", "baseline", "support", "counter", "reproduce")
# A paper shows up in lists, topics and search when a capture is confirmed to be about it or a research uses it.
LISTED = ("(EXISTS (SELECT 1 FROM capture_papers lc WHERE lc.paper_id=p.id AND lc.state='confirmed') "
          "OR EXISTS (SELECT 1 FROM research_links lr WHERE lr.kind='paper' AND lr.ref=p.id))")

SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, raw TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
  channel TEXT, url TEXT, title TEXT, author TEXT, snapshot_text TEXT, links TEXT, candidates TEXT,
  status TEXT NOT NULL DEFAULT 'saved', note TEXT, triage TEXT NOT NULL DEFAULT 'inbox', agent_id TEXT);
CREATE TABLE IF NOT EXISTS attachments (
  id TEXT PRIMARY KEY, capture_id TEXT NOT NULL REFERENCES captures(id) ON DELETE CASCADE,
  filename TEXT NOT NULL, mime TEXT NOT NULL, path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, title TEXT NOT NULL, authors TEXT, abstract TEXT,
  published TEXT, url TEXT, pdf_path TEXT, page_count INTEGER NOT NULL DEFAULT 0,
  card TEXT, card_rev INTEGER NOT NULL DEFAULT 0, report TEXT, report_rev INTEGER NOT NULL DEFAULT 0,
  topics_locked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS capture_papers (
  capture_id TEXT NOT NULL REFERENCES captures(id) ON DELETE CASCADE, paper_id TEXT NOT NULL REFERENCES papers(id),
  state TEXT NOT NULL, by TEXT NOT NULL, evidence TEXT, at TEXT NOT NULL, PRIMARY KEY (capture_id, paper_id));
CREATE TABLE IF NOT EXISTS topics (path TEXT PRIMARY KEY, name TEXT NOT NULL, parent TEXT, position INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS paper_topics (
  paper_id TEXT NOT NULL REFERENCES papers(id), topic TEXT NOT NULL REFERENCES topics(path), by TEXT NOT NULL,
  PRIMARY KEY (paper_id, topic));
CREATE TABLE IF NOT EXISTS thoughts (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, text TEXT NOT NULL, by TEXT NOT NULL,
  capture_id TEXT REFERENCES captures(id) ON DELETE CASCADE, paper_id TEXT REFERENCES papers(id),
  gist TEXT, gist_by TEXT);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, text TEXT NOT NULL, by TEXT NOT NULL,
  paper_id TEXT REFERENCES papers(id), topic TEXT, thread_id TEXT);
CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, scope TEXT NOT NULL, title TEXT, agent_id TEXT);
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, thread_id TEXT NOT NULL REFERENCES threads(id),
  role TEXT NOT NULL, text TEXT NOT NULL DEFAULT '', run_id TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, agent_id TEXT NOT NULL,
  kind TEXT NOT NULL, ref TEXT, prompt TEXT, status TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
  tools TEXT, error TEXT, duration_ms INTEGER);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  target TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS research (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, title TEXT NOT NULL, summary TEXT,
  body TEXT, stage TEXT NOT NULL DEFAULT 'forming', established_at TEXT, proposal TEXT, by TEXT NOT NULL,
  edited_by TEXT, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS research_topics (
  research_id TEXT NOT NULL REFERENCES research(id) ON DELETE CASCADE, topic TEXT NOT NULL REFERENCES topics(path),
  PRIMARY KEY (research_id, topic));
CREATE TABLE IF NOT EXISTS research_links (
  research_id TEXT NOT NULL REFERENCES research(id) ON DELETE CASCADE, kind TEXT NOT NULL, ref TEXT NOT NULL,
  by TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (research_id, kind, ref));
CREATE INDEX IF NOT EXISTS research_links_ref ON research_links(kind, ref);
CREATE TABLE IF NOT EXISTS repos (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, url TEXT NOT NULL UNIQUE, name TEXT NOT NULL, path TEXT NOT NULL,
  status TEXT NOT NULL, head TEXT, pulled_at TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS research_repos (
  research_id TEXT NOT NULL REFERENCES research(id) ON DELETE CASCADE, repo_id TEXT NOT NULL REFERENCES repos(id),
  role TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (research_id, repo_id));
CREATE TABLE IF NOT EXISTS code_maps (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, research_id TEXT NOT NULL REFERENCES research(id) ON DELETE CASCADE,
  repo_id TEXT NOT NULL REFERENCES repos(id), paper_id TEXT REFERENCES papers(id), commit_sha TEXT NOT NULL, rows TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  research_id TEXT NOT NULL REFERENCES research(id) ON DELETE CASCADE, title TEXT, target TEXT, setup TEXT,
  results TEXT, conclusion TEXT, conclusion_text TEXT, status TEXT NOT NULL DEFAULT 'done', raw TEXT NOT NULL DEFAULT '',
  by TEXT NOT NULL, edited_by TEXT, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS anchors (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, paper_id TEXT NOT NULL REFERENCES papers(id),
  surface TEXT NOT NULL, quote TEXT NOT NULL, page INTEGER, section TEXT, by TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS anchors_paper ON anchors(paper_id);
CREATE TABLE IF NOT EXISTS research_docs (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, research_id TEXT NOT NULL REFERENCES research(id) ON DELETE CASCADE,
  filename TEXT NOT NULL, mime TEXT NOT NULL, path TEXT NOT NULL, text TEXT);
CREATE TABLE IF NOT EXISTS writing (
  research_id TEXT PRIMARY KEY REFERENCES research(id) ON DELETE CASCADE, abstract TEXT, introduction TEXT,
  proposal TEXT, revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT, edited_by TEXT);
CREATE INDEX IF NOT EXISTS captures_triage ON captures(triage, created_at);
CREATE INDEX IF NOT EXISTS cp_paper ON capture_papers(paper_id);
CREATE INDEX IF NOT EXISTS runs_ref ON runs(ref, created_at);
"""


def _statements(script):
    return [s for s in script.split(";") if s.strip()]


def _v1_baseline(c):
    """The schema as it was before versioning. Databases created by earlier code already have most of it."""
    for stmt in _statements(SCHEMA):
        c.execute(stmt)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(thoughts)")}
    for col in ("gist", "gist_by"):
        if col not in cols:
            c.execute(f"ALTER TABLE thoughts ADD COLUMN {col} TEXT")
    for t in ("idea_links", "idea_topics", "ideas"):  # replaced by research; were empty when dropped
        c.execute(f"DROP TABLE IF EXISTS {t}")
    if not c.execute("SELECT 1 FROM topics LIMIT 1").fetchone():
        for i, (parent, children) in enumerate(TOPIC_TREE):
            c.execute("INSERT INTO topics VALUES (?,?,?,?)", (parent, parent, None, i))
            for j, child in enumerate(children):
                c.execute("INSERT INTO topics VALUES (?,?,?,?)", (f"{parent}/{child}", child, parent, j))


def _v2_indexes(c):
    for stmt in _statements("""
        CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id, created_at);
        CREATE INDEX IF NOT EXISTS threads_scope ON threads(scope, created_at);
        CREATE INDEX IF NOT EXISTS thoughts_capture ON thoughts(capture_id);
        CREATE INDEX IF NOT EXISTS thoughts_paper ON thoughts(paper_id);
        CREATE INDEX IF NOT EXISTS notes_paper ON notes(paper_id);
        CREATE INDEX IF NOT EXISTS paper_topics_topic ON paper_topics(topic);
        CREATE INDEX IF NOT EXISTS events_target ON events(target, action)"""):
        c.execute(stmt)


def _v3_run_queue(c):
    """Runs wait their turn (status WAITING) with what is needed to start them later; started_at times them out."""
    c.execute("ALTER TABLE runs ADD COLUMN started_at TEXT")
    c.execute("ALTER TABLE runs ADD COLUMN launch TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS runs_status ON runs(status)")


def _v4_free_bare_thoughts(c):
    """A thought typed into the capture box without any content used to become an empty capture that showed up as
    "no paper found". Such captures become standalone thoughts (the thought keeps its id, text and time)."""
    bare = _ids(c, "SELECT id FROM captures c WHERE raw='' AND NOT EXISTS (SELECT 1 FROM attachments a WHERE a.capture_id=c.id) "
                   "AND NOT EXISTS (SELECT 1 FROM capture_papers cp WHERE cp.capture_id=c.id) "
                   "AND NOT EXISTS (SELECT 1 FROM research_links l WHERE l.kind='capture' AND l.ref=c.id)", ())
    if bare:
        c.execute(f"UPDATE thoughts SET capture_id=NULL WHERE capture_id IN ({_qs(bare)})", bare)
        c.execute(f"DELETE FROM runs WHERE ref IN ({_qs(bare)})", bare)
        c.execute(f"DELETE FROM captures WHERE id IN ({_qs(bare)})", bare)


def _v5_searchable_fulltext(c):
    """Full text in the database so search can reach it; filled from each paper's pages.json."""
    c.execute("CREATE TABLE IF NOT EXISTS paper_text (paper_id TEXT PRIMARY KEY REFERENCES papers(id), text TEXT NOT NULL)")
    for pid in _ids(c, "SELECT id FROM papers WHERE page_count > 0", ()):
        path = paper_dir(pid, create=False) / "pages.json"
        if path.exists():
            c.execute("INSERT OR REPLACE INTO paper_text VALUES (?,?)", (pid, "\n".join(json.loads(path.read_text()))))


def _v6_topic_proposals(c):
    """Topics the agent proposes wait (state 'proposed') until the user accepts them."""
    c.execute("ALTER TABLE topics ADD COLUMN state TEXT NOT NULL DEFAULT 'active'")
    c.execute("ALTER TABLE topics ADD COLUMN reason TEXT")
    c.execute("ALTER TABLE topics ADD COLUMN for_papers TEXT")


def _v7_discussion_tree(c):
    """Discussions form a tree: parent_id is the discussion one grew out of; title_by / parent_by = 'user' keep the
    agent off what the user set. Each thought becomes a discussion of its own (same id, words, time, gist as title),
    absorbing the discussion it already had; research materials follow. The thoughts table goes (backed up first)."""
    for col in ("parent_id", "title_by", "parent_by"):
        c.execute(f"ALTER TABLE threads ADD COLUMN {col} TEXT")
    c.execute("CREATE INDEX IF NOT EXISTS threads_parent ON threads(parent_id)")
    c.execute("UPDATE threads SET scope='library' WHERE scope='thoughts'")
    for t in c.execute("SELECT * FROM thoughts ORDER BY created_at").fetchall():
        scope = f"paper:{t['paper_id']}" if t["paper_id"] else f"capture:{t['capture_id']}" if t["capture_id"] else "library"
        old = _ids(c, "SELECT id FROM threads WHERE scope=?", (f"thought:{t['id']}",))
        q = _qs(old)
        agent = old and (c.execute(f"SELECT agent_id FROM threads WHERE id IN ({q}) AND agent_id IS NOT NULL "
                                   f"ORDER BY created_at DESC", old).fetchone() or [None])[0]
        c.execute("INSERT INTO threads (id, created_at, scope, title, agent_id, title_by) VALUES (?,?,?,?,?,?)",
                  (t["id"], t["created_at"], scope, t["gist"], agent or None, t["gist"] and t["gist_by"]))
        c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                  (new_id(), t["created_at"], t["id"], "user" if t["by"] == "user" else "assistant", t["text"], None))
        if old:
            for table, col in (("messages", "thread_id"), ("notes", "thread_id"), ("runs", "ref")):
                c.execute(f"UPDATE {table} SET {col}=? WHERE {col} IN ({q})", (t["id"], *old))
            c.execute(f"UPDATE OR IGNORE research_links SET ref=? WHERE kind='thread' AND ref IN ({q})", (t["id"], *old))
            c.execute(f"DELETE FROM research_links WHERE kind='thread' AND ref IN ({q})", old)
            c.execute(f"DELETE FROM threads WHERE id IN ({q})", old)
    c.execute("UPDATE OR IGNORE research_links SET kind='thread' WHERE kind='thought'")
    c.execute("DELETE FROM research_links WHERE kind='thought'")
    c.execute("DROP TABLE thoughts")


def _v8_fork_point(c):
    """fork_message_id: the message of the parent discussion a branch grew from (NULL: from the whole of it)."""
    c.execute("ALTER TABLE threads ADD COLUMN fork_message_id TEXT")


def _v9_material_roles(c):
    """role: what a paper does in the research (MATERIAL_ROLES), set by the user; NULL until they say."""
    c.execute("ALTER TABLE research_links ADD COLUMN role TEXT")


def _v10_overleaf(c):
    """overleaf: id of the Overleaf project the research's paper is written in (checked out under overleaf/<id>)."""
    c.execute("ALTER TABLE writing ADD COLUMN overleaf TEXT")


def _v11_plans(c):
    """A research's plan: an ordered list of tasks (rules.validate_plan); the agent's version waits as a proposal."""
    c.execute("CREATE TABLE IF NOT EXISTS plans (research_id TEXT PRIMARY KEY REFERENCES research(id) ON DELETE CASCADE, "
              "tasks TEXT NOT NULL DEFAULT '[]', proposal TEXT, revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT, edited_by TEXT)")


def _v12_related_work(c):
    """A research's related work: lines of work (rules.validate_related); the agent's version waits as a proposal."""
    c.execute("CREATE TABLE IF NOT EXISTS related_work (research_id TEXT PRIMARY KEY REFERENCES research(id) ON DELETE CASCADE, "
              "groups TEXT NOT NULL DEFAULT '[]', proposal TEXT, revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT, edited_by TEXT)")


def _v13_related_comparison(c):
    """The related work's side-by-side table (rules.validate_comparison)."""
    c.execute("ALTER TABLE related_work ADD COLUMN comparison TEXT")


def _v14_experiment_charts(c):
    """An experiment's charts: Chart.js configs whose numbers all come from the raw result (rules.validate_experiment)."""
    c.execute("ALTER TABLE experiments ADD COLUMN charts TEXT")


def _v15_expected_figures(c):
    """A research's expected figures (rules.validate_figures; the agent's version waits as a proposal) and the
    experiment results filling their cells (rules.validate_cells)."""
    c.execute("CREATE TABLE IF NOT EXISTS exp_figures (research_id TEXT PRIMARY KEY REFERENCES research(id) ON DELETE CASCADE, "
              "figures TEXT NOT NULL DEFAULT '[]', cells TEXT NOT NULL DEFAULT '[]', proposal TEXT, "
              "revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT, edited_by TEXT)")


def _v16_experiment_design(c):
    """design: the Experimental Setup the expected figures share (rules.validate_setup)."""
    c.execute("ALTER TABLE exp_figures ADD COLUMN design TEXT")


# Append only: PRAGMA user_version is the number of steps already applied. Each step runs once, in its own
# transaction, after a backup of the database as it was.
MIGRATIONS = [_v1_baseline, _v2_indexes, _v3_run_queue, _v4_free_bare_thoughts, _v5_searchable_fulltext, _v6_topic_proposals,
              _v7_discussion_tree, _v8_fork_point, _v9_material_roles, _v10_overleaf, _v11_plans, _v12_related_work,
              _v13_related_comparison, _v14_experiment_charts, _v15_expected_figures,
              _v16_experiment_design]


def home():
    path = Path(os.environ.get("P2B_HOME") or Path.home() / "paper2brain-data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id():
    return str(uuid.uuid4())


def paper_dir(paper_id, create=True):
    path = home() / "papers" / paper_id.replace(":", "_").replace("/", "_")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _row(r):
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        if d[k] is not None:
            d[k] = json.loads(d[k])
    return d


def _rows(rs):
    return [_row(r) for r in rs]


def _connect():
    conn = sqlite3.connect(home() / "p2b.sqlite", timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_initialized = set()
BACKUPS_KEPT = 7


def backup(name=None):
    """A consistent copy of the database under backups/. Without a name: today's daily copy (once a day, the newest
    BACKUPS_KEPT kept). Returns the new file, or None when today's copy already exists."""
    folder = home() / "backups"
    folder.mkdir(exist_ok=True)
    path = folder / (name or f"p2b-{datetime.now():%Y%m%d}.sqlite")
    if path.exists():
        return None
    tmp = path.with_suffix(".tmp")
    src, dst = _connect(), sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    tmp.replace(path)
    if not name:
        for old in sorted(folder.glob("p2b-[0-9]*.sqlite"))[:-BACKUPS_KEPT]:
            old.unlink()
    return path


def _migrate(conn):
    if conn.execute("PRAGMA user_version").fetchone()[0] >= len(MIGRATIONS):
        return
    if conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
        backup(f"p2b-before-v{len(MIGRATIONS)}-{datetime.now():%Y%m%d-%H%M%S}.sqlite")
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")  # the server and an agent's ./p2b may start at the same moment
    try:
        done = conn.execute("PRAGMA user_version").fetchone()[0]
        for n, step in enumerate(MIGRATIONS[done:], done + 1):
            step(conn)
            conn.execute(f"PRAGMA user_version={n}")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.isolation_level = ""


@contextmanager
def tx():
    conn = _connect()
    try:
        key = str(home())
        if key not in _initialized:
            _migrate(conn)
            _initialized.add(key)
        with conn:
            yield conn
    finally:
        conn.close()


def _event(conn, actor, action, target, **detail):
    conn.execute(
        "INSERT INTO events (at, actor, action, target, detail) VALUES (?,?,?,?,?)",
        (now(), actor, action, target, json.dumps(detail, ensure_ascii=False) if detail else None),
    )


# ---------- captures ----------

def add_capture(capture_id, raw, source, thought=None, images=()):
    """Idempotent on capture_id: the iPhone may deliver the same capture via the API and the iCloud inbox.
    Returns (capture, created)."""
    raw = (raw or "").strip()
    if not raw and not images and not (thought or "").strip():
        raise rules.Rejected("内容为空：至少要有文字、链接、图片或一句想法")
    with tx() as c:
        existing = c.execute("SELECT id FROM captures WHERE id=?", (capture_id,)).fetchone()
        if not existing:
            c.execute(
                "INSERT INTO captures (id, created_at, raw, source) VALUES (?,?,?,?)",
                (capture_id, now(), raw, source),
            )
            if (thought or "").strip():
                _start_thread(c, f"capture:{capture_id}", thought)
            _event(c, source, "capture.add", capture_id)
        have = {r["filename"] for r in c.execute("SELECT filename FROM attachments WHERE capture_id=?", (capture_id,))}
        folder = home() / "assets" / capture_id
        for filename, data, mime in images:
            if filename in have:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / filename
            path.write_bytes(data)
            c.execute(
                "INSERT INTO attachments VALUES (?,?,?,?,?)", (new_id(), capture_id, filename, mime, str(path))
            )
    return get_capture(capture_id), not existing


def same_capture(raw):
    """An earlier capture of exactly the same text, if any: re-sharing a link should not create a second entry."""
    if not (raw or "").strip():
        return None
    with tx() as c:
        r = c.execute("SELECT id FROM captures WHERE raw=? ORDER BY created_at LIMIT 1", (raw.strip(),)).fetchone()
    return r and r["id"]


def update_capture(capture_id, **fields):
    for k in ("links", "candidates"):
        if k in fields and fields[k] is not None:
            fields[k] = json.dumps(fields[k], ensure_ascii=False)
    cols = ", ".join(f"{k}=?" for k in fields)
    with tx() as c:
        c.execute(f"UPDATE captures SET {cols} WHERE id=?", (*fields.values(), capture_id))


def set_triage(capture_id, triage):
    if triage not in TRIAGE:
        raise rules.Rejected(f"未知状态：{triage}")
    with tx() as c:
        c.execute("UPDATE captures SET triage=? WHERE id=?", (triage, capture_id))
        _event(c, "user", "capture.triage", capture_id, triage=triage)


def _ids(c, sql, args):
    return [r[0] for r in c.execute(sql, args)]


def _qs(xs):
    return ",".join("?" * len(xs))


def _paper_dependents(c, pid):
    """Everything that exists only because the paper is in the library: pinned passages, its discussions and the
    notes attached to it. Card and report live on the paper row itself."""
    anchors = _ids(c, "SELECT id FROM anchors WHERE paper_id=?", (pid,))
    scopes = [f"paper:{pid}"] + [f"anchor:{a}" for a in anchors]
    threads = _ids(c, f"SELECT id FROM threads WHERE scope IN ({_qs(scopes)})", scopes)
    return {
        "anchor": anchors, "thread": threads,
        "message": _ids(c, f"SELECT id FROM messages WHERE thread_id IN ({_qs(threads)})", threads) if threads else [],
        "note": _ids(c, f"SELECT id FROM notes WHERE paper_id=? OR thread_id IN ({_qs(threads) or 'NULL'})", (pid, *threads)),
    }


def _research_using(c, refs):
    """Titles of researches that use any of these (kind, [ids]) as material."""
    out = set()
    for kind, ids in refs.items():
        if ids:
            out |= {r[0] for r in c.execute(
                f"SELECT x.title FROM research_links l JOIN research x ON x.id=l.research_id WHERE l.kind=? AND l.ref IN ({_qs(ids)})",
                (kind, *ids))}
    return sorted(out)


def _removal(c, capture_id):
    threads = _ids(c, "SELECT id FROM threads WHERE scope IN (?,?)", (f"find:{capture_id}", f"capture:{capture_id}"))
    msgs = _ids(c, f"SELECT id FROM messages WHERE thread_id IN ({_qs(threads)})", threads) if threads else []
    plan = {"threads": threads,
            "used_in": _research_using(c, {"thread": threads, "message": msgs, "capture": [capture_id]}),
            "papers": [], "kept": []}
    for r in c.execute("SELECT p.id, p.title, p.card IS NOT NULL AS card, p.report IS NOT NULL AS report FROM capture_papers cp "
                       "JOIN papers p ON p.id=cp.paper_id WHERE cp.capture_id=?", (capture_id,)).fetchall():
        others = c.execute("SELECT count(*) FROM capture_papers WHERE paper_id=? AND capture_id!=? AND state!='rejected'",
                           (r["id"], capture_id)).fetchone()[0]
        deps = _paper_dependents(c, r["id"])
        research = _research_using(c, {"paper": [r["id"]], **deps})
        if c.execute("SELECT 1 FROM code_maps WHERE paper_id=?", (r["id"],)).fetchone() and not research:
            research = ["（方法—代码对照）"]
        if others or research:
            why = (["还有另外 %d 条收下的内容讲的是它" % others] if others else []) + [f"研究《{t}》用到了它" for t in research]
            plan["kept"].append({"id": r["id"], "title": r["title"], "why": why})
        else:
            plan["papers"].append({"id": r["id"], "title": r["title"], "card": bool(r["card"]), "report": bool(r["report"]),
                                   **{k: deps[k] for k in ("anchor", "thread", "note")}, "_deps": deps})
    return plan


def capture_removal(capture_id):
    """What undoing this capture deletes, for the confirmation dialog."""
    with tx() as c:
        plan = _removal(c, capture_id)
    for p in plan["papers"]:
        p.pop("_deps")
    return plan


def delete_capture(capture_id):
    """Undoing a capture means the user disowns it: the capture, its discussions (what the user said when collecting
    it, the find discussion), and every paper it alone brought in, with that paper's card, report, pinned passages,
    discussions and notes. A paper that another capture or a research also uses stays, untouched."""
    with tx() as c:
        plan = _removal(c, capture_id)
        threads = plan["threads"] + [t for p in plan["papers"] for t in p["_deps"]["thread"]]
        gone = {"capture": [capture_id], "thread": threads, "note": [n for p in plan["papers"] for n in p["_deps"]["note"]]}
        if threads:
            gone["message"] = _ids(c, f"SELECT id FROM messages WHERE thread_id IN ({_qs(threads)})", threads)
        for kind, ids in gone.items():
            if ids:
                c.execute(f"DELETE FROM research_links WHERE kind=? AND ref IN ({_qs(ids)})", (kind, *ids))
        pids = [p["id"] for p in plan["papers"]]
        refs = [capture_id, *threads, *pids]
        c.execute(f"DELETE FROM runs WHERE ref IN ({_qs(refs)})", refs)
        _drop_threads(c, threads)
        for table, col, ids in (("notes", "id", gone["note"]), ("anchors", "paper_id", pids), ("paper_topics", "paper_id", pids),
                                ("capture_papers", "paper_id", pids), ("paper_text", "paper_id", pids)):
            if ids:
                c.execute(f"DELETE FROM {table} WHERE {col} IN ({_qs(ids)})", ids)
        c.execute("DELETE FROM captures WHERE id=?", (capture_id,))
        if pids:
            c.execute(f"DELETE FROM papers WHERE id IN ({_qs(pids)})", pids)
        _event(c, "user", "capture.delete", capture_id, papers=pids, threads=len(threads))
    shutil.rmtree(home() / "assets" / capture_id, ignore_errors=True)
    for pid in pids:
        shutil.rmtree(paper_dir(pid, create=False), ignore_errors=True)


def get_capture(capture_id):
    with tx() as c:
        cap = _row(c.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone())
        if not cap:
            return None
        cap["attachments"] = _rows(c.execute("SELECT id, filename, mime FROM attachments WHERE capture_id=?", (capture_id,)))
        cap["threads"] = _threads_of(c, [f"capture:{capture_id}"])
        cap["papers"] = _rows(c.execute(
            "SELECT p.id, p.title, p.card IS NOT NULL AS has_card, cp.state, cp.by, cp.evidence "
            "FROM capture_papers cp JOIN papers p ON p.id=cp.paper_id WHERE cp.capture_id=? ORDER BY cp.at",
            (capture_id,),
        ))
        cap["runs"] = _rows(c.execute("SELECT * FROM runs WHERE ref=? ORDER BY created_at DESC", (capture_id,)))
        cap["research"] = _research_for(c, "capture", [capture_id]).get(capture_id, [])
    return cap


def attachment(attachment_id):
    with tx() as c:
        return _row(c.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone())


def _topics_of(c, paper_ids):
    out = {pid: [] for pid in paper_ids}
    if paper_ids:
        q = ",".join("?" * len(paper_ids))
        for r in c.execute(f"SELECT paper_id, topic FROM paper_topics WHERE paper_id IN ({q}) ORDER BY topic", paper_ids):
            out[r["paper_id"]].append(r["topic"])
    return out


PENDING = "c.status IN ('needs_confirm','unmapped','failed') AND c.triage!='ignored'"


def list_captures(view="inbox", limit=200):
    """The inbox also holds, first, every capture that needs the user's word (PENDING), wherever it was filed."""
    where = {"pending": PENDING, "inbox": f"(c.triage='inbox' OR ({PENDING}))"}.get(view, "c.triage=?" if view in TRIAGE else "1=1")
    args = (view,) if view in TRIAGE and view != "inbox" else ()
    order = f"({PENDING}) DESC, " if view == "inbox" else ""
    with tx() as c:
        caps = _rows(c.execute(
            f"SELECT c.id, c.created_at, substr(c.raw,1,300) AS raw, c.source, c.channel, c.title, c.author, c.status, "
            f"c.note, c.triage, c.agent_id, (SELECT coalesce(t.title, (SELECT m.text FROM messages m WHERE m.thread_id=t.id "
            f"AND m.role='user' ORDER BY m.created_at, m.rowid LIMIT 1)) FROM threads t WHERE t.scope='capture:' || c.id "
            f"ORDER BY t.created_at DESC LIMIT 1) AS thought, "
            f"(SELECT status FROM runs r WHERE r.ref=c.id ORDER BY created_at DESC LIMIT 1) AS run_status, "
            f"(SELECT count(*) FROM attachments a WHERE a.capture_id=c.id) AS image_count "
            f"FROM captures c WHERE {where} ORDER BY {order}c.created_at DESC LIMIT ?",
            (*args, limit),
        ))
        ids = [x["id"] for x in caps]
        links = {}
        if ids:
            q = ",".join("?" * len(ids))
            for r in c.execute(
                f"SELECT cp.capture_id, cp.state, p.id, p.title, p.card FROM capture_papers cp JOIN papers p ON p.id=cp.paper_id "
                f"WHERE cp.capture_id IN ({q}) AND cp.state!='rejected' ORDER BY cp.at", ids,
            ):
                card = json.loads(r["card"]) if r["card"] else None
                links.setdefault(r["capture_id"], []).append(
                    {"id": r["id"], "title": r["title"], "state": r["state"], "tldr": card and card.get("tldr")}
                )
        pids = sorted({p["id"] for ps in links.values() for p in ps})
        topics = _topics_of(c, pids)
        reports = _report_status(c, pids)
        jumps = _jumps(c, pids)
    for x in caps:
        x["papers"] = [{**p, "topics": topics.get(p["id"], []), "report_status": reports.get(p["id"]), "jumps": jumps.get(p["id"])}
                       for p in links.get(x["id"], [])]
    return caps


def _report_status(c, pids):
    """'done' when the paper has a deep-read report, 'running' while one is being written."""
    if not pids:
        return {}
    q = ",".join("?" * len(pids))
    out = {r[0]: "running" for r in c.execute(
        f"SELECT DISTINCT ref FROM runs WHERE kind='deepread' AND status IN {_UNFINISHED_SQL} AND ref IN ({q})", pids)}
    out.update({r[0]: "done" for r in c.execute(f"SELECT id FROM papers WHERE report IS NOT NULL AND id IN ({q})", pids)})
    return out


def _jumps(c, pids):
    """Where a title card can take the reader directly: report sections, the PDF's method / experiments / conclusion,
    figures, pinned passages and the paper's discussion."""
    if not pids:
        return {}
    q = ",".join("?" * len(pids))
    heads = {r["id"]: [s["heading"] for s in json.loads(r["report"])["sections"]]
             for r in c.execute(f"SELECT id, report FROM papers WHERE report IS NOT NULL AND id IN ({q})", pids)}
    anchors = {r[0]: r[1] for r in c.execute(f"SELECT paper_id, count(*) FROM anchors WHERE paper_id IN ({q}) GROUP BY paper_id", pids)}
    chats = {r[0][6:]: r[1] for r in c.execute(
        f"SELECT t.scope, count(*) FROM threads t JOIN messages m ON m.thread_id=t.id WHERE m.role='user' AND t.scope IN ({q}) "
        f"GROUP BY t.scope", [f"paper:{p}" for p in pids])}
    out = {}
    for pid in pids:
        path = paper_dir(pid, create=False) / "structure.json"
        s = json.loads(path.read_text()) if path.exists() else {}
        out[pid] = {"report": heads.get(pid, []), "sections": s.get("key", {}), "figures": len(s.get("figures", [])),
                    "anchors": anchors.get(pid, 0), "chat": chats.get(pid, 0)}
    return out


def deep_queue():
    """Papers the user marked for deep reading that have full text but no report and no report being written,
    most recently marked first."""
    with tx() as c:
        return [r[0] for r in c.execute(
            "SELECT p.id FROM papers p JOIN capture_papers cp ON cp.paper_id=p.id AND cp.state='confirmed' "
            "JOIN captures c ON c.id=cp.capture_id AND c.triage='deep' "
            "WHERE p.report IS NULL AND p.page_count > 0 "
            f"AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.ref=p.id AND r.kind='deepread' AND r.status IN {_UNFINISHED_SQL}) "
            "GROUP BY p.id ORDER BY max(COALESCE((SELECT max(e.at) FROM events e WHERE e.action='capture.triage' AND e.target=c.id), c.created_at)) DESC"
        )]


def card_queue():
    """Listed papers with full text but no quick card, none being written for them, and no capture of theirs still
    being resolved or handled by its agent (that agent writes the card), most recently added first."""
    with tx() as c:
        return [r[0] for r in c.execute(
            f"SELECT p.id FROM papers p WHERE {LISTED} AND p.card IS NULL AND p.page_count > 0 "
            f"AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.ref=p.id AND r.kind='card' AND r.status IN {_UNFINISHED_SQL}) "
            "AND NOT EXISTS (SELECT 1 FROM capture_papers cp JOIN captures c ON c.id=cp.capture_id WHERE cp.paper_id=p.id "
            f"AND (c.status='resolving' OR EXISTS (SELECT 1 FROM runs r WHERE r.ref=c.id AND r.kind='capture' AND r.status IN {_UNFINISHED_SQL}))) "
            "ORDER BY p.created_at DESC"
        )]


def counts():
    with tx() as c:
        out = {t: 0 for t in TRIAGE}
        for r in c.execute("SELECT triage, count(*) n FROM captures GROUP BY triage"):
            out[r["triage"]] = r["n"]
        out["pending"] = c.execute(
            f"SELECT count(*) FROM captures c WHERE {PENDING}").fetchone()[0]
        out["inbox"] = c.execute(f"SELECT count(*) FROM captures c WHERE c.triage='inbox' OR ({PENDING})").fetchone()[0]
        out["papers"] = c.execute(f"SELECT count(*) FROM papers p WHERE {LISTED}").fetchone()[0]
        out["research"] = c.execute("SELECT count(*) FROM research").fetchone()[0]
        out["unsorted"] = c.execute(
            f"SELECT count(*) FROM papers p WHERE {LISTED} AND NOT EXISTS (SELECT 1 FROM paper_topics pt WHERE pt.paper_id=p.id)"
        ).fetchone()[0]
        out["running"] = c.execute(
            f"SELECT count(*) FROM runs WHERE status IN {_UNFINISHED_SQL}").fetchone()[0]
    return out


# Your own actions: (at, kind, ref, text, extra). A paper read counts once per paper per day.
_ACTIVITY = (
    "SELECT created_at at, 'capture' kind, id ref, COALESCE(title, raw) text, NULL extra FROM captures UNION ALL "
    "SELECT created_at, 'message', thread_id, text, (SELECT scope FROM threads t WHERE t.id=thread_id) FROM messages WHERE role='user' UNION ALL "
    "SELECT created_at, 'anchor', paper_id, quote, (SELECT title FROM papers p WHERE p.id=paper_id) FROM anchors WHERE by='user' UNION ALL "
    "SELECT created_at, 'note', paper_id, text, (SELECT title FROM papers p WHERE p.id=paper_id) FROM notes WHERE by='user' UNION ALL "
    "SELECT at, 'triage', target, json_extract(detail, '$.triage'), (SELECT COALESCE(title, raw) FROM captures c WHERE c.id=target) "
    "FROM events WHERE actor='user' AND action='capture.triage' UNION ALL "
    "SELECT min(at), 'read', target, NULL, (SELECT title FROM papers p WHERE p.id=target) "
    "FROM events WHERE actor='user' AND action='paper.read' GROUP BY target, date(at, 'localtime')"
)


def activity(days=371):
    """Actions per local day over the last year, the current streak (today may still be empty) and the longest one."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with tx() as c:
        counts = dict(c.execute(
            f"SELECT date(at, 'localtime') d, count(*) FROM ({_ACTIVITY}) WHERE at >= ? GROUP BY d", (since,)).fetchall())
    day = date.today()
    if day.isoformat() not in counts:
        day -= timedelta(days=1)
    streak = 0
    while day.isoformat() in counts:
        streak, day = streak + 1, day - timedelta(days=1)
    best = run = 0
    prev = None
    for k in sorted(counts):
        d = date.fromisoformat(k)
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best, prev = max(best, run), d
    return {"days": counts, "streak": streak, "best": best}


def activity_day(day):
    """What you did on one local day (YYYY-MM-DD), oldest first."""
    with tx() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM ({_ACTIVITY}) WHERE date(at, 'localtime')=? ORDER BY at", (day,))]


def mark_read(pid):
    with tx() as c:
        _event(c, "user", "paper.read", pid)


# ---------- papers ----------

def upsert_paper(pid, title, authors=None, abstract=None, published=None, url=None):
    with tx() as c:
        if c.execute("SELECT 1 FROM papers WHERE id=?", (pid,)).fetchone():
            c.execute(
                "UPDATE papers SET title=?, authors=?, abstract=?, published=?, url=? WHERE id=?",
                (title, json.dumps(authors or [], ensure_ascii=False), abstract, published, url, pid),
            )
        else:
            c.execute(
                "INSERT INTO papers (id, created_at, title, authors, abstract, published, url) VALUES (?,?,?,?,?,?,?)",
                (pid, now(), title, json.dumps(authors or [], ensure_ascii=False), abstract, published, url),
            )


def set_fulltext(pid, pdf_path, pages):
    (paper_dir(pid) / "pages.json").write_text(json.dumps(pages, ensure_ascii=False))
    (paper_dir(pid) / "structure.json").unlink(missing_ok=True)  # figures and outline come from the PDF
    with tx() as c:
        c.execute("UPDATE papers SET pdf_path=?, page_count=? WHERE id=?", (str(pdf_path), len(pages), pid))
        c.execute("INSERT OR REPLACE INTO paper_text VALUES (?,?)", (pid, "\n".join(pages)))


def pages(pid):
    path = paper_dir(pid) / "pages.json"
    return json.loads(path.read_text()) if path.exists() else []


def paper_ids():
    with tx() as c:
        return {r["id"] for r in c.execute("SELECT id FROM papers")}


def get_paper(pid, with_anchors=True):
    with tx() as c:
        p = _row(c.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone())
        if not p:
            return None
        p["topics"] = _topics_of(c, [pid])[pid]
        p["topic_by"] = {r["topic"]: r["by"] for r in c.execute("SELECT topic, by FROM paper_topics WHERE paper_id=?", (pid,))}
        p["captures"] = _rows(c.execute(
            "SELECT c.id, c.created_at, c.channel, c.title, c.author, c.url, substr(c.raw,1,300) AS raw, c.triage, c.agent_id, cp.state, cp.by "
            "FROM capture_papers cp JOIN captures c ON c.id=cp.capture_id WHERE cp.paper_id=? AND cp.state!='rejected' "
            "ORDER BY c.created_at", (pid,),
        ))
        p["threads"] = _threads_of(c, [f"paper:{pid}"] + [f"capture:{x['id']}" for x in p["captures"]])
        p["notes"] = _rows(c.execute("SELECT * FROM notes WHERE paper_id=? ORDER BY created_at", (pid,)))
        p["research"] = _research_for(c, "paper", [pid]).get(pid, [])
        p["anchors"] = []
        p["runs"] = _rows(c.execute("SELECT * FROM runs WHERE ref=? ORDER BY created_at DESC", (pid,)))
    if with_anchors:
        p["anchors"] = anchors_for(pid)
        p["cited_in"] = cited_in(pid)
    return p


def list_papers(topic=None):
    """topic=None: every listed paper (LISTED); '' : papers without a topic; a path includes its children."""
    sql = (
        "SELECT p.id, p.title, p.authors, p.published, p.card, p.report IS NOT NULL AS has_report, "
        "coalesce((SELECT max(c.created_at) FROM capture_papers cp JOIN captures c ON c.id=cp.capture_id "
        "WHERE cp.paper_id=p.id AND cp.state='confirmed'), p.created_at) AS last_captured, "
        "EXISTS (SELECT 1 FROM capture_papers cp2 JOIN captures c2 ON c2.id=cp2.capture_id WHERE cp2.paper_id=p.id "
        "AND cp2.state='confirmed' AND c2.triage='deep') AS deep "
        f"FROM papers p WHERE {LISTED} "
    )
    args = ()
    if topic == "":
        sql += "AND NOT EXISTS (SELECT 1 FROM paper_topics pt WHERE pt.paper_id=p.id) "
    elif topic:
        sql += "AND EXISTS (SELECT 1 FROM paper_topics pt WHERE pt.paper_id=p.id AND (pt.topic=? OR pt.topic LIKE ?)) "
        args = (topic, topic + "/%")
    sql += "ORDER BY last_captured DESC"
    with tx() as c:
        out = _rows(c.execute(sql, args))
        topics = _topics_of(c, [p["id"] for p in out])
        reports = _report_status(c, [p["id"] for p in out])
        jumps = _jumps(c, [p["id"] for p in out])
    for p in out:
        card = p.pop("card")
        p["tldr"] = card and card.get("tldr")
        p["topics"] = topics[p["id"]]
        p["report_status"] = reports.get(p["id"])
        p["jumps"] = jumps[p["id"]]
    return out


def link(capture_id, pid, by, state="confirmed", evidence=None):
    with tx() as c:
        if not c.execute("SELECT 1 FROM papers WHERE id=?", (pid,)).fetchone():
            raise rules.Rejected(f"论文 {pid} 不在库里，先运行 ./p2b fetch {pid}")
        existing = c.execute(
            "SELECT state, by FROM capture_papers WHERE capture_id=? AND paper_id=?", (capture_id, pid)
        ).fetchone()
        rules.check_link(existing, by)
        if existing and existing["state"] == "confirmed" and state == "pending":
            return
        c.execute(
            "INSERT OR REPLACE INTO capture_papers VALUES (?,?,?,?,?,?)", (capture_id, pid, state, by, evidence, now())
        )
        _event(c, by, "link", capture_id, paper=pid, state=state)


def unlink(capture_id, pid, by):
    """A user unlink is remembered as a rejection so later processing cannot bring the link back."""
    with tx() as c:
        existing = c.execute(
            "SELECT state, by FROM capture_papers WHERE capture_id=? AND paper_id=?", (capture_id, pid)
        ).fetchone()
        rules.check_link(existing, by)
        if by == "user":
            c.execute(
                "INSERT OR REPLACE INTO capture_papers VALUES (?,?,?,?,?,?)", (capture_id, pid, "rejected", "user", None, now())
            )
        else:
            c.execute("DELETE FROM capture_papers WHERE capture_id=? AND paper_id=?", (capture_id, pid))
        _event(c, by, "unlink", capture_id, paper=pid)


def put_card(pid, card, by, base_rev=None):
    with tx() as c:
        cur = c.execute("SELECT card_rev FROM papers WHERE id=?", (pid,)).fetchone()
        if not cur:
            raise rules.Rejected(f"论文 {pid} 不在库里")
        rules.check_revision(cur["card_rev"], base_rev)
        c.execute(
            "UPDATE papers SET card=?, card_rev=card_rev+1 WHERE id=?", (json.dumps(card, ensure_ascii=False), pid)
        )
        _event(c, by, "card.put", pid, rev=cur["card_rev"] + 1)
        return cur["card_rev"] + 1


def put_report(pid, report, by, base_rev=None):
    with tx() as c:
        cur = c.execute("SELECT report_rev FROM papers WHERE id=?", (pid,)).fetchone()
        if not cur:
            raise rules.Rejected(f"论文 {pid} 不在库里")
        rules.check_revision(cur["report_rev"], base_rev)
        c.execute(
            "UPDATE papers SET report=?, report_rev=report_rev+1 WHERE id=?", (json.dumps(report, ensure_ascii=False), pid)
        )
        _event(c, by, "report.put", pid, rev=cur["report_rev"] + 1)
        return cur["report_rev"] + 1


# ---------- topics ----------

def topic_tree():
    with tx() as c:
        rows = _rows(c.execute("SELECT * FROM topics WHERE state='active' ORDER BY parent IS NOT NULL, position"))
        n = {r["topic"]: r["n"] for r in c.execute(
            f"SELECT pt.topic, count(DISTINCT pt.paper_id) n FROM paper_topics pt JOIN papers p ON p.id=pt.paper_id "
            f"WHERE {LISTED} GROUP BY pt.topic"
        )}
        under = {r["parent"]: r["n"] for r in c.execute(
            "SELECT t.parent, count(DISTINCT pt.paper_id) n FROM paper_topics pt JOIN topics t ON t.path=pt.topic "
            f"JOIN papers p ON p.id=pt.paper_id WHERE t.parent IS NOT NULL AND {LISTED} GROUP BY t.parent"
        )}
    tree = []
    for r in rows:
        if r["parent"] is None:
            tree.append({"path": r["path"], "name": r["name"], "count": max(n.get(r["path"], 0), under.get(r["path"], 0)), "children": []})
        else:
            next(t for t in tree if t["path"] == r["parent"])["children"].append(
                {"path": r["path"], "name": r["name"], "count": n.get(r["path"], 0)}
            )
    return tree


def topic_proposals():
    with tx() as c:
        return _rows(c.execute("SELECT path, name, parent, reason, for_papers FROM topics WHERE state='proposed' ORDER BY rowid"))


def _topic_name(name):
    name = re.sub(r"\s+", " ", name or "").strip()
    if not name or "/" in name or len(name) > 30:
        raise rules.Rejected("主题名不能为空、不能含 /，最多 30 字")
    return name


def _topic_parent(c, parent, path=None):
    """'' or None: top level. Otherwise an active top-level topic: the tree is two levels deep."""
    if not parent:
        return None
    if parent == path or not c.execute("SELECT 1 FROM topics WHERE path=? AND parent IS NULL AND state='active'", (parent,)).fetchone():
        raise rules.Rejected(f"上级主题只能是一个已有的一级主题：{parent}")
    return parent


def add_topic(name, parent, by, reason=None, papers=()):
    """The user adds a topic directly; the agent only proposes one (with a reason) for the user to accept."""
    name = _topic_name(name)
    if by != "user" and not (reason or "").strip():
        raise rules.Rejected("提议新主题要写 --why：为什么现有主题放不下")
    with tx() as c:
        parent = _topic_parent(c, parent)
        path = f"{parent}/{name}" if parent else name
        if c.execute("SELECT state FROM topics WHERE path=?", (path,)).fetchone():
            raise rules.Rejected(f"主题 {path} 已经有了（或已被提议）")
        known = _papers_in(c, papers)
        position = c.execute("SELECT coalesce(max(position), -1) + 1 FROM topics WHERE parent IS ?", (parent,)).fetchone()[0]
        c.execute("INSERT INTO topics (path, name, parent, position, state, reason, for_papers) VALUES (?,?,?,?,?,?,?)",
                  (path, name, parent, position, "active" if by == "user" else "proposed", (reason or "").strip() or None,
                   json.dumps(known) if known else None))
        _event(c, by, "topic.add" if by == "user" else "topic.propose", path)
    return path


def _papers_in(c, papers):
    missing = [p for p in papers if not c.execute("SELECT 1 FROM papers WHERE id=?", (p,)).fetchone()]
    if missing:
        raise rules.Rejected(f"论文不在库里：{missing}")
    return list(dict.fromkeys(papers))


def _repath(c, old, new, name, parent):
    """Give a topic (and its children) a new path, carrying every paper, research, note and discussion along.
    The new row goes in before the old one goes out, so references never point at nothing."""
    r = c.execute("SELECT * FROM topics WHERE path=?", (old,)).fetchone()
    c.execute("INSERT INTO topics (path, name, parent, position, state, reason, for_papers) VALUES (?,?,?,?,?,?,?)",
              (new, name, parent, r["position"], r["state"], r["reason"], r["for_papers"]))
    for child in c.execute("SELECT path, name FROM topics WHERE parent=?", (old,)).fetchall():
        _repath(c, child["path"], f"{new}/{child['name']}", child["name"], new)
    for table in ("paper_topics", "research_topics"):
        c.execute(f"UPDATE {table} SET topic=? WHERE topic=?", (new, old))
    c.execute("UPDATE notes SET topic=? WHERE topic=?", (new, old))
    c.execute("UPDATE threads SET scope=? WHERE scope=?", (f"topic:{new}", f"topic:{old}"))
    c.execute("DELETE FROM topics WHERE path=?", (old,))


def edit_topic(path, name, parent):
    """Rename and / or move a topic (the user only). parent '' moves it to the top level."""
    name = _topic_name(name)
    with tx() as c:
        cur = c.execute("SELECT * FROM topics WHERE path=? AND state='active'", (path,)).fetchone()
        if not cur:
            raise rules.Rejected(f"主题不存在：{path}")
        parent = _topic_parent(c, parent, path)
        if parent and c.execute("SELECT 1 FROM topics WHERE parent=?", (path,)).fetchone():
            raise rules.Rejected("有子主题的一级主题不能移到别的主题下面")
        new = f"{parent}/{name}" if parent else name
        if new == path:
            return path
        if c.execute("SELECT 1 FROM topics WHERE path=?", (new,)).fetchone():
            raise rules.Rejected(f"主题 {new} 已经有了")
        _repath(c, path, new, name, parent)
        if parent != cur["parent"]:
            c.execute("UPDATE topics SET position=(SELECT coalesce(max(position), -1) + 1 FROM topics WHERE parent IS ?) WHERE path=?",
                      (parent, new))
        _event(c, "user", "topic.edit", new, was=path)
    return new


def delete_topic(path):
    """The user removes a topic; what was filed under a subtopic moves up to its parent, a top-level one leaves it unsorted."""
    with tx() as c:
        cur = c.execute("SELECT parent FROM topics WHERE path=? AND state='active'", (path,)).fetchone()
        if not cur:
            raise rules.Rejected(f"主题不存在：{path}")
        if c.execute("SELECT 1 FROM topics WHERE parent=? AND state='active'", (path,)).fetchone():
            raise rules.Rejected("先删除或移走它下面的子主题")
        up = cur["parent"]
        if up:
            c.execute("INSERT OR IGNORE INTO paper_topics SELECT paper_id, ?, by FROM paper_topics WHERE topic=?", (up, path))
            c.execute("INSERT OR IGNORE INTO research_topics SELECT research_id, ? FROM research_topics WHERE topic=?", (up, path))
        c.execute("DELETE FROM paper_topics WHERE topic=?", (path,))
        c.execute("DELETE FROM research_topics WHERE topic=?", (path,))
        c.execute("UPDATE notes SET topic=? WHERE topic=?", (up, path))
        c.execute("UPDATE threads SET scope=? WHERE scope=?", (f"topic:{up}" if up else "library", f"topic:{path}"))
        c.execute("DELETE FROM topics WHERE path=?", (path,))
        _event(c, "user", "topic.delete", path)


def decide_topic(path, accept):
    """Accepting a proposed topic also files the papers the agent proposed it for, unless the user fixed their topics."""
    with tx() as c:
        r = _row(c.execute("SELECT * FROM topics WHERE path=? AND state='proposed'", (path,)).fetchone())
        if not r:
            raise rules.Rejected("没有这个待确认的主题")
        if not accept:
            c.execute("DELETE FROM topics WHERE path=?", (path,))
        else:
            if r["parent"] and not c.execute("SELECT 1 FROM topics WHERE path=? AND state='active'", (r["parent"],)).fetchone():
                raise rules.Rejected(f"上级主题 {r['parent']} 已经不在了")
            c.execute("UPDATE topics SET state='active' WHERE path=?", (path,))
            for pid in r["for_papers"] or []:
                if c.execute("SELECT 1 FROM papers WHERE id=? AND topics_locked=0", (pid,)).fetchone():
                    c.execute("INSERT OR IGNORE INTO paper_topics VALUES (?,?,?)", (pid, path, "agent"))
        _event(c, "user", "topic.accept" if accept else "topic.reject", path)


def set_topics(pid, paths, by):
    """The agent may classify until the user touches a paper's topics; from then on the user's choice is kept."""
    with tx() as c:
        p = c.execute("SELECT topics_locked FROM papers WHERE id=?", (pid,)).fetchone()
        if not p:
            raise rules.Rejected(f"论文 {pid} 不在库里")
        if by != "user" and p["topics_locked"]:
            raise rules.Rejected("用户已手动调整过这篇论文的主题，agent 不能修改")
        _check_topics(c, paths)
        c.execute("DELETE FROM paper_topics WHERE paper_id=?", (pid,))
        for t in dict.fromkeys(paths):
            c.execute("INSERT INTO paper_topics VALUES (?,?,?)", (pid, t, by))
        if by == "user":
            c.execute("UPDATE papers SET topics_locked=1 WHERE id=?", (pid,))
        _event(c, by, "topics.set", pid, topics=list(paths))


# ---------- notes ----------

def kv_get(key):
    with tx() as c:
        r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return r and r["value"]


def kv_set(key, value):
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, value))


def add_note(text, by, paper_id=None, topic=None, thread_id=None):
    if not (text or "").strip():
        raise rules.Rejected("笔记不能为空")
    nid = new_id()
    with tx() as c:
        c.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,?)", (nid, now(), text.strip(), by, paper_id, topic, thread_id))
    return nid


# ---------- discussion ----------

def thread_for(scope=None, thread_id=None):
    """The latest thread of a scope (created on first use), or a specific one when jumping back to an old discussion."""
    with tx() as c:
        if thread_id:
            t = _row(c.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone())
            if not t:
                raise rules.Rejected("这段讨论不存在了")
        else:
            t = _row(c.execute("SELECT * FROM threads WHERE scope=? ORDER BY created_at DESC, rowid DESC LIMIT 1", (scope,)).fetchone())
        if not t:
            t = _row(c.execute("SELECT * FROM threads WHERE id=?", (_start_thread(c, scope),)).fetchone())
        t["messages"] = _rows(c.execute(
            "SELECT m.*, r.status AS run_status, r.text AS run_text, r.tools AS run_tools, r.error AS run_error "
            "FROM messages m LEFT JOIN runs r ON r.id=m.run_id WHERE m.thread_id=? ORDER BY m.created_at, m.rowid", (t["id"],),
        ))
        used = _research_for(c, "message", [m["id"] for m in t["messages"]])
        t["parent"] = _row(c.execute(_HEAD + "WHERE t.id=?", (t["parent_id"],)).fetchone()) if t["parent_id"] else None
        t["children"] = _rows(c.execute(_HEAD + "WHERE t.parent_id=? ORDER BY t.created_at", (t["id"],)))
        t["siblings"] = _rows(c.execute(_HEAD + "WHERE t.parent_id IS ? AND t.fork_message_id IS ? AND (t.parent_id IS NOT NULL OR t.scope=?) "
                                                "ORDER BY t.created_at, t.rowid", (t["parent_id"], t["fork_message_id"], t["scope"])))
        t["path"] = _path(c, t)
        t["research"] = _research_for(c, "thread", [t["id"]]).get(t["id"], [])
        t["papers"] = _papers_cited(c, [m["run_text"] or m["text"] for m in t["messages"]])
        t["scope_title"] = _scope_title(c, t["scope"])
        kind, _, ref = t["scope"].partition(":")
        t["scope_papers"] = _rows(c.execute(f"SELECT id, title FROM papers WHERE id IN ({_qs(ref.split('|'))})", ref.split("|"))) if kind == "papers" \
            else _rows(c.execute("SELECT p.id, p.title FROM capture_papers cp JOIN papers p ON p.id=cp.paper_id "
                                 "WHERE cp.capture_id=? AND cp.state='confirmed' ORDER BY cp.at", (ref,))) if kind in ("find", "capture") else []
        table = {"research": "research WHERE id=?", "writing": "writing WHERE research_id=?",
                 "related": "related_work WHERE research_id=?", "plan": "plans WHERE research_id=?",
                 "experiment": "exp_figures WHERE research_id=?"}.get(kind)
        t["proposal"] = bool(table and (c.execute(f"SELECT proposal FROM {table}", (ref,)).fetchone() or [None])[0])
    forks = {}
    for ch in t["children"]:
        if ch["fork_message_id"]:
            forks.setdefault(ch["fork_message_id"], []).append(ch)
    for m in t["messages"]:
        if m["run_tools"]:
            m["run_tools"] = json.loads(m["run_tools"])
        m["research"] = used.get(m["id"], [])
        m["forks"] = forks.get(m["id"], [])
    return t


# What a discussion is called in lists: its title, else the user's opening words.
_HEAD = ("SELECT t.id, t.scope, t.created_at, t.title, t.parent_id, t.fork_message_id, (SELECT text FROM messages u "
         "WHERE u.thread_id=t.id AND u.role='user' ORDER BY u.created_at, u.rowid LIMIT 1) AS first FROM threads t ")
_CITE = re.compile(r"\[((?:arxiv|doi|openreview|pdf):[^\s\],;]+)", re.I)


def _said(c, tid):
    return _rows(c.execute("SELECT m.id, m.role, m.created_at, coalesce(nullif(m.text, ''), r.text, '') AS text FROM messages m "
                           "LEFT JOIN runs r ON r.id=m.run_id WHERE m.thread_id=? ORDER BY m.created_at, m.rowid", (tid,)))


def _path(c, t):
    """What a branch inherits, root first: each ancestor with its messages up to where the branch below it forked."""
    out, cur = [], t
    while cur["parent_id"]:
        up = _row(c.execute(_HEAD + "WHERE t.id=?", (cur["parent_id"],)).fetchone())
        if not up:
            break
        msgs = _said(c, up["id"])
        cut = next((i for i, m in enumerate(msgs) if m["id"] == cur["fork_message_id"]), None)
        kids = _rows(c.execute(_HEAD + "WHERE t.parent_id=? ORDER BY t.created_at, t.rowid", (up["id"],)))
        out.insert(0, {**up, "messages": msgs if cut is None else msgs[:cut + 1], "children": kids})
        cur = up
    return out


def _papers_cited(c, texts):
    """Library papers named in [arxiv:… p.N]-style citations, in order of first mention."""
    ids = [i for i in dict.fromkeys(rules.parse_pid(m.group(1)) for s in texts if s for m in _CITE.finditer(s)) if i]
    if not ids:
        return []
    found = {r["id"]: r["title"] for r in c.execute(f"SELECT id, title FROM papers WHERE id IN ({_qs(ids)})", ids)}
    return [{"id": i, "title": found[i]} for i in ids if i in found]


def cited_in(pid):
    """Backlinks: the discussions whose messages cite this paper, latest activity first."""
    pattern = "%[" + pid.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with tx() as c:
        ids = _ids(c, "SELECT m.thread_id FROM messages m LEFT JOIN runs r ON r.id=m.run_id "
                      "WHERE coalesce(nullif(m.text, ''), r.text, '') LIKE ? ESCAPE '\\' GROUP BY m.thread_id "
                      "ORDER BY max(m.created_at) DESC", (pattern,))
        out = [_row(c.execute(_HEAD + "WHERE t.id=?", (i,)).fetchone()) for i in ids]
        for t in out:
            t["scope_title"] = _scope_title(c, t["scope"])
    return out


def _threads_of(c, scopes):
    """The started discussions of these scopes, oldest first, with the researches that use them."""
    out = _rows(c.execute(_HEAD + f"WHERE t.scope IN ({_qs(scopes)}) AND EXISTS (SELECT 1 FROM messages m WHERE m.thread_id=t.id) "
                                  f"ORDER BY t.created_at, t.rowid", scopes))
    used = _research_for(c, "thread", [t["id"] for t in out])
    for t in out:
        t["research"] = used.get(t["id"], [])
    return out


def _start_thread(c, scope, text=None, parent_id=None, tid=None, fork=None):
    """A user who opens a discussion from another one placed it there (parent_by='user')."""
    tid = tid or new_id()
    added = c.execute("INSERT OR IGNORE INTO threads (id, created_at, scope, parent_id, parent_by, fork_message_id) VALUES (?,?,?,?,?,?)",
                      (tid, now(), scope, parent_id, parent_id and "user", fork)).rowcount
    if added and (text or "").strip():
        c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (new_id(), now(), tid, "user", text.strip(), None))
    return tid


def _drop_threads(c, ids):
    """Delete discussions and their messages; what grew out of them moves up to their parent."""
    if not ids:
        return
    q = _qs(ids)
    while c.execute(f"UPDATE threads SET parent_id=(SELECT p.parent_id FROM threads p WHERE p.id=threads.parent_id), "
                    f"fork_message_id=NULL WHERE parent_id IN ({q}) AND id NOT IN ({q})", (*ids, *ids)).rowcount:
        pass
    c.execute(f"DELETE FROM messages WHERE thread_id IN ({q})", ids)
    c.execute(f"DELETE FROM threads WHERE id IN ({q})", ids)


def _scope_title(c, scope):
    kind, _, ref = scope.partition(":")
    one = lambda sql: (c.execute(sql, (ref,)).fetchone() or [None])[0]  # noqa: E731
    if kind == "paper":
        return one("SELECT title FROM papers WHERE id=?")
    if kind == "anchor":
        r = c.execute("SELECT a.quote, p.title FROM anchors a JOIN papers p ON p.id=a.paper_id WHERE a.id=?", (ref,)).fetchone()
        return r and f"{r['title']} ·「{r['quote'][:40]}」"
    if kind in ("research", "writing", "related", "plan", "code", "experiment"):
        return one("SELECT title FROM research WHERE id=?")
    if kind == "find":
        return one("SELECT raw FROM captures WHERE id=?")
    if kind == "capture":
        return one("SELECT coalesce(title, substr(raw,1,80)) FROM captures WHERE id=?")
    if kind == "papers":
        return f"{len(ref.split('|'))} 篇论文"
    return {"library": "整个论文库"}.get(kind, ref)


def recent_threads(limit=300):
    """Every discussion that has started, the latest activity first, whatever it is attached to."""
    with tx() as c:
        out = _rows(c.execute(
            "SELECT t.id, t.scope, t.created_at, t.agent_id, t.title, t.title_by, t.parent_id, t.parent_by, t.fork_message_id, count(m.id) AS n, "
            "max(m.created_at) AS last_at, "
            "(SELECT text FROM messages u WHERE u.thread_id=t.id AND u.role='user' ORDER BY u.created_at, u.rowid LIMIT 1) AS first "
            "FROM threads t JOIN messages m ON m.thread_id=t.id GROUP BY t.id ORDER BY last_at DESC, max(m.rowid) DESC LIMIT ?", (limit,)))
        for t in out:
            t["scope_title"] = _scope_title(c, t["scope"])
    return out


def paper_discussions(pid):
    """What the user said and asked about a paper (when collecting it, on the web, in pinned passages, from the
    phone), with the answers."""
    with tx() as c:
        scopes = [f"paper:{pid}"] + [f"anchor:{a}" for a in _ids(c, "SELECT id FROM anchors WHERE paper_id=?", (pid,))] + \
                 [f"capture:{x}" for x in _ids(c, "SELECT capture_id FROM capture_papers WHERE paper_id=? AND state='confirmed'", (pid,))]
        threads = _rows(c.execute(f"SELECT id, scope, created_at FROM threads WHERE scope IN ({_qs(scopes)}) "
                                  f"ORDER BY created_at, rowid", scopes))
        for t in threads:
            if t["scope"].startswith("anchor:"):
                t["passage"] = c.execute("SELECT quote FROM anchors WHERE id=?", (t["scope"][7:],)).fetchone()[0]
            t["messages"] = [{"role": m["role"], "at": m["created_at"], "text": (m["text"] or "")[:1500]} for m in c.execute(
                "SELECT m.role, m.created_at, coalesce(nullif(m.text, ''), r.text) AS text FROM messages m "
                "LEFT JOIN runs r ON r.id=m.run_id WHERE m.thread_id=? ORDER BY m.created_at, m.rowid", (t["id"],))]
    return [t for t in threads if t["messages"]]


def message(mid):
    with tx() as c:
        m = _row(c.execute(
            "SELECT m.*, t.scope, r.text AS run_text FROM messages m JOIN threads t ON t.id=m.thread_id "
            "LEFT JOIN runs r ON r.id=m.run_id WHERE m.id=?", (mid,),
        ).fetchone())
    if not m:
        raise rules.Rejected("这条讨论消息不存在了")
    return m


def new_thread(scope, parent_id=None, text=None, tid=None, fork=None):
    """A new discussion of a scope, maybe grown out of another (fork: from one of its messages, which names the
    parent), maybe opened by a line the user only wrote down (no answer asked for). tid: the id of what delivered it
    (an iPhone capture id), so a second delivery adds nothing."""
    with tx() as c:
        if fork:
            r = c.execute("SELECT thread_id FROM messages WHERE id=?", (fork,)).fetchone()
            if not r:
                raise rules.Rejected("要分叉的那条消息不存在了")
            parent_id = r["thread_id"]
        if parent_id and not c.execute("SELECT 1 FROM threads WHERE id=?", (parent_id,)).fetchone():
            raise rules.Rejected("要接着的那段讨论不存在了")
        return _start_thread(c, scope, text, parent_id, tid, fork)


def set_thread_title(tid, title, by):
    """One line that says what the discussion is about. The agent names it until the user does."""
    title = re.sub(r"\s+", " ", title or "").strip()
    if not 1 <= len(title) <= 80:
        raise rules.Rejected("标题不能为空，最多 80 字")
    with tx() as c:
        cur = c.execute("SELECT title_by FROM threads WHERE id=?", (tid,)).fetchone()
        if not cur:
            raise rules.Rejected("这段讨论不存在了")
        if by != "user" and cur["title_by"] == "user":
            raise rules.Rejected("用户已手动改过这段讨论的标题，agent 不能修改")
        c.execute("UPDATE threads SET title=?, title_by=? WHERE id=?", (title, by, tid))


def move_thread(tid, parent_id, by):
    """Hang a discussion under the one it grew out of (parent_id None: top level). The agent may place it until
    the user has."""
    with tx() as c:
        cur = c.execute("SELECT parent_by, parent_id FROM threads WHERE id=?", (tid,)).fetchone()
        if not cur:
            raise rules.Rejected("这段讨论不存在了")
        if by != "user" and cur["parent_by"] == "user":
            raise rules.Rejected("用户已手动调整过这段讨论的位置，agent 不能修改")
        if cur["parent_id"] == (parent_id or None):
            return
        up = parent_id
        while up:
            if up == tid:
                raise rules.Rejected("不能挂到它自己或它下面的讨论上")
            r = c.execute("SELECT parent_id FROM threads WHERE id=?", (up,)).fetchone()
            if not r:
                raise rules.Rejected(f"要挂到的讨论 {up} 不存在")
            up = r["parent_id"]
        c.execute("UPDATE threads SET parent_id=?, parent_by=?, fork_message_id=NULL WHERE id=?", (parent_id or None, by, tid))
        _event(c, by, "thread.move", tid, parent=parent_id)


def pending_titles(limit=12):
    """Started discussions nobody has named yet."""
    with tx() as c:
        return _ids(c, "SELECT t.id FROM threads t WHERE t.title IS NULL AND EXISTS "
                       "(SELECT 1 FROM messages m WHERE m.thread_id=t.id AND m.role='user') ORDER BY t.created_at LIMIT ?", (limit,))


def set_thread_agent(thread_id, agent_id):
    with tx() as c:
        c.execute("UPDATE threads SET agent_id=? WHERE id=?", (agent_id, thread_id))


def add_message(thread_id, role, text, run_id=None):
    mid = new_id()
    with tx() as c:
        c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (mid, now(), thread_id, role, text, run_id))
    return mid


# ---------- research ----------
# A research gathers materials (discussions, papers, ...) into three elements: problem, method, validation.
# Only the user establishes a research; the agent only ever proposes drafts the user accepts or discards.

def _research_for(c, kind, refs):
    """Reverse links: which researches use each of these objects as material."""
    out = {}
    if refs:
        q = ",".join("?" * len(refs))
        for r in c.execute(
            f"SELECT l.ref, l.role, x.id, x.title, x.stage FROM research_links l JOIN research x ON x.id=l.research_id "
            f"WHERE l.kind=? AND l.ref IN ({q}) ORDER BY x.updated_at DESC", (kind, *refs),
        ):
            out.setdefault(r["ref"], []).append({"id": r["id"], "title": r["title"], "stage": r["stage"], "role": r["role"]})
    return out


def _check_topics(c, paths):
    known = {r["path"] for r in c.execute("SELECT path FROM topics WHERE state='active'")}
    unknown = [t for t in paths if t not in known]
    if unknown:
        raise rules.Rejected(f"未知主题：{unknown}。可用主题见 ./p2b topics；放不下时用 ./p2b topic propose 提议新主题")


def _check_link(c, kind, ref):
    if kind not in LINK_KINDS:
        raise rules.Rejected(f"不能关联 {kind}，可选：{', '.join(LINK_KINDS)}")
    table = {"paper": "papers", "message": "messages", "thread": "threads", "doc": "research_docs",
             "capture": "captures", "note": "notes"}[kind]
    if not c.execute(f"SELECT 1 FROM {table} WHERE id=?", (ref,)).fetchone():
        raise rules.Rejected(f"要关联的 {kind} {ref} 不存在")


def add_research(title, by, summary=None, topics=(), links=()):
    """links: [(kind, ref)]. Without explicit topics the research inherits the topics of its papers."""
    if not (title or "").strip():
        raise rules.Rejected("研究需要一个标题")
    rid = new_id()
    with tx() as c:
        for kind, ref in links:
            _check_link(c, kind, ref)
        topics = list(topics)
        papers = [ref for kind, ref in links if kind == "paper"]
        if not topics and papers:
            q = ",".join("?" * len(papers))
            topics = [r["topic"] for r in c.execute(f"SELECT DISTINCT topic FROM paper_topics WHERE paper_id IN ({q})", papers)]
        _check_topics(c, topics)
        c.execute(
            "INSERT INTO research (id, created_at, updated_at, title, summary, by) VALUES (?,?,?,?,?,?)",
            (rid, now(), now(), title.strip(), (summary or "").strip() or None, by),
        )
        for t in dict.fromkeys(topics):
            c.execute("INSERT INTO research_topics VALUES (?,?)", (rid, t))
        for kind, ref in dict.fromkeys(links):
            c.execute("INSERT INTO research_links (research_id, kind, ref, by, at) VALUES (?,?,?,?,?)", (rid, kind, ref, by, now()))
        _event(c, by, "research.add", rid)
    return rid


def _current(c, rid, base_rev):
    cur = c.execute("SELECT * FROM research WHERE id=?", (rid,)).fetchone()
    if not cur:
        raise rules.Rejected("研究不存在")
    rules.check_revision(cur["revision"], base_rev)
    return cur


def update_research(rid, base_rev=None, title=None, summary=None, body=None):
    """The user's own edits. Revision-guarded so an edit made in another tab is not silently lost."""
    with tx() as c:
        cur = _current(c, rid, base_rev)
        if title is not None and not title.strip():
            raise rules.Rejected("研究需要一个标题")
        old = json.loads(cur["body"]) if cur["body"] else {}
        new_body = {k: (body or old).get(k) for k in RESEARCH_FIELDS} if body is not None else old
        c.execute(
            "UPDATE research SET title=?, summary=?, body=?, updated_at=?, edited_by='user', revision=revision+1 WHERE id=?",
            ((title or cur["title"]).strip(), cur["summary"] if summary is None else (summary.strip() or None),
             json.dumps(new_body, ensure_ascii=False), now(), rid),
        )
        _event(c, "user", "research.update", rid)
        return cur["revision"] + 1


def establish(rid, by, established=True):
    """Only the user establishes a research, and only once all three elements are written down."""
    if by != "user":
        raise rules.Rejected("研究只能由用户确立。请把修改稿交给用户确认")
    with tx() as c:
        cur = _current(c, rid, None)
        body = json.loads(cur["body"]) if cur["body"] else {}
        missing = [rules.RESEARCH_LABELS[k] for k in RESEARCH_ELEMENTS if not (body.get(k) or "").strip()]
        if established and missing:
            raise rules.Rejected(f"三要素还缺：{'、'.join(missing)}")
        c.execute(
            "UPDATE research SET stage=?, established_at=?, updated_at=?, revision=revision+1 WHERE id=?",
            ("established" if established else "forming", now() if established else None, now(), rid),
        )
        _event(c, by, "research.establish" if established else "research.reopen", rid)


def propose(rid, draft, by, base_rev=None):
    """The agent's draft of title / summary / elements, held aside until the user accepts or discards it."""
    with tx() as c:
        _current(c, rid, base_rev)
        c.execute("UPDATE research SET proposal=?, updated_at=? WHERE id=?",
                  (json.dumps({**draft, "by": by, "at": now()}, ensure_ascii=False), now(), rid))
        _event(c, by, "research.propose", rid)


def accept_proposal(rid, base_rev=None, fields=None):
    """fields: the parts of the draft the user takes (None: all of it); the rest of the draft is dropped and
    those parts keep what they had."""
    with tx() as c:
        cur = _current(c, rid, base_rev)
        if not cur["proposal"]:
            raise rules.Rejected("没有待确认的修改稿")
        p = json.loads(cur["proposal"])
        take = lambda k: p.get(k) and (fields is None or k in fields)  # noqa: E731
        old = json.loads(cur["body"]) if cur["body"] else {}
        body = {k: p[k] if take(k) else old.get(k) for k in RESEARCH_FIELDS}
        c.execute(
            "UPDATE research SET title=?, summary=?, body=?, proposal=NULL, updated_at=?, edited_by='agent', revision=revision+1 WHERE id=?",
            (p["title"] if take("title") else cur["title"], p["summary"] if take("summary") else cur["summary"],
             json.dumps(body, ensure_ascii=False), now(), rid),
        )
        _event(c, "user", "research.accept", rid)
        return cur["revision"] + 1


def discard_proposal(rid):
    with tx() as c:
        c.execute("UPDATE research SET proposal=NULL, updated_at=? WHERE id=?", (now(), rid))
        _event(c, "user", "research.discard", rid)


def set_research_topics(rid, topics):
    with tx() as c:
        _check_topics(c, topics)
        c.execute("DELETE FROM research_topics WHERE research_id=?", (rid,))
        for t in dict.fromkeys(topics):
            c.execute("INSERT INTO research_topics VALUES (?,?)", (rid, t))
        c.execute("UPDATE research SET updated_at=? WHERE id=?", (now(), rid))


def link_research(rid, kind, ref, by, role=None):
    """role: None leaves it as it is; '' clears it. Only the user sets roles."""
    if role is not None and (by != "user" or role not in MATERIAL_ROLES + ("",)):
        raise rules.Rejected(f"素材角色只能由用户设置，可选：{', '.join(MATERIAL_ROLES)}")
    with tx() as c:
        if not c.execute("SELECT 1 FROM research WHERE id=?", (rid,)).fetchone():
            raise rules.Rejected("研究不存在")
        _check_link(c, kind, ref)
        c.execute("INSERT OR IGNORE INTO research_links (research_id, kind, ref, by, at) VALUES (?,?,?,?,?)", (rid, kind, ref, by, now()))
        if role is not None:
            c.execute("UPDATE research_links SET role=? WHERE research_id=? AND kind=? AND ref=?", (role or None, rid, kind, ref))
        c.execute("UPDATE research SET updated_at=? WHERE id=?", (now(), rid))


def rejected_in_research(rid):
    """Papers the user turned down ("不对路") in the paper searches started from this research."""
    with tx() as c:
        return _ids(c, "SELECT DISTINCT cp.paper_id FROM research_links l JOIN capture_papers cp ON cp.capture_id=l.ref "
                       "WHERE l.research_id=? AND l.kind='capture' AND cp.state='rejected'", (rid,))


def unlink_research(rid, kind, ref):
    with tx() as c:
        c.execute("DELETE FROM research_links WHERE research_id=? AND kind=? AND ref=?", (rid, kind, ref))


def delete_research(rid):
    with tx() as c:
        c.execute("DELETE FROM research WHERE id=?", (rid,))
        _event(c, "user", "research.delete", rid)


def _resolve_link(c, kind, ref):
    """What a material looks like in the research's list; None when it has been deleted since."""
    if kind == "paper":
        r = c.execute("SELECT title, card FROM papers WHERE id=?", (ref,)).fetchone()
        code = ((json.loads(r["card"]) if r["card"] else {}).get("resources") or {}).get("code") if r else None
        return r and {"label": r["title"], "code": code}
    if kind == "message":
        r = c.execute(
            "SELECT m.role, coalesce(nullif(m.text, ''), r.text) AS text, m.created_at, t.scope, t.id AS thread_id "
            "FROM messages m JOIN threads t ON t.id=m.thread_id LEFT JOIN runs r ON r.id=m.run_id WHERE m.id=?", (ref,),
        ).fetchone()
        return r and {"label": (r["text"] or "")[:200], "at": r["created_at"], "role": r["role"], "scope": r["scope"], "thread_id": r["thread_id"]}
    if kind == "thread":
        r = c.execute(_HEAD + "WHERE t.id=?", (ref,)).fetchone()
        return r and {"label": r["title"] or r["first"] or "一段讨论", "at": r["created_at"], "scope": r["scope"]}
    if kind == "capture":
        r = c.execute("SELECT coalesce(title, substr(raw,1,120)) AS label, created_at FROM captures WHERE id=?", (ref,)).fetchone()
        return r and {"label": r["label"], "at": r["created_at"]}
    if kind == "doc":
        r = c.execute("SELECT filename, mime, created_at, substr(text,1,200) AS excerpt FROM research_docs WHERE id=?", (ref,)).fetchone()
        return r and {"label": r["filename"], "excerpt": r["excerpt"], "mime": r["mime"], "at": r["created_at"]}
    r = c.execute("SELECT text, created_at, paper_id FROM notes WHERE id=?", (ref,)).fetchone()
    return r and {"label": r["text"][:200], "at": r["created_at"], "paper_id": r["paper_id"]}


def _elements_done(body):
    return sum(bool((body or {}).get(k)) for k in RESEARCH_ELEMENTS)


def get_research(rid):
    with tx() as c:
        x = _row(c.execute("SELECT * FROM research WHERE id=?", (rid,)).fetchone())
        if not x:
            return None
        x["topics"] = [r["topic"] for r in c.execute("SELECT topic FROM research_topics WHERE research_id=? ORDER BY topic", (rid,))]
        x["links"] = []
        for r in c.execute("SELECT * FROM research_links WHERE research_id=? ORDER BY at", (rid,)).fetchall():
            info = _resolve_link(c, r["kind"], r["ref"])
            x["links"].append({"kind": r["kind"], "ref": r["ref"], "by": r["by"], "role": r["role"], "missing": info is None, **(info or {})})
        x["runs"] = _rows(c.execute("SELECT * FROM runs WHERE ref=? ORDER BY created_at DESC", (rid,)))
    x["elements_done"] = _elements_done(x["body"])
    x.update(research_extras(rid))
    return x


def list_research(topic=None):
    """topic=None: all; '': without a topic; a path includes its children. Established first."""
    sql = "SELECT x.id, x.title, x.summary, x.stage, x.by, x.updated_at, x.created_at, x.body, x.proposal IS NOT NULL AS has_proposal, " \
          "(SELECT count(*) FROM research_links l WHERE l.research_id=x.id) AS link_count, " \
          "(SELECT count(*) FROM experiments e WHERE e.research_id=x.id) AS exp_count, " \
          "(SELECT count(*) FROM research_repos rr WHERE rr.research_id=x.id) AS repo_count FROM research x "
    args = ()
    if topic == "":
        sql += "WHERE NOT EXISTS (SELECT 1 FROM research_topics t WHERE t.research_id=x.id) "
    elif topic:
        sql += "WHERE EXISTS (SELECT 1 FROM research_topics t WHERE t.research_id=x.id AND (t.topic=? OR t.topic LIKE ?)) "
        args = (topic, topic + "/%")
    sql += "ORDER BY x.stage='established' DESC, x.updated_at DESC"
    with tx() as c:
        out = _rows(c.execute(sql, args))
        for x in out:
            x["topics"] = [r["topic"] for r in c.execute("SELECT topic FROM research_topics WHERE research_id=? ORDER BY topic", (x["id"],))]
            x["elements_done"] = _elements_done(x.pop("body"))
    return out


# ---------- research: code, experiments, writing ----------

EXPERIMENT_STATUS = ("planned", "running", "done", "abandoned")
CONCLUSIONS = ("supports", "refutes", "inconclusive")


def add_repo(url, path):
    """One checkout per repository, shared by every research that links it."""
    with tx() as c:
        r = c.execute("SELECT id FROM repos WHERE url=?", (url,)).fetchone()
        if r:
            return r["id"], False
        rid = new_id()
        name = "/".join(url.rstrip("/").split("/")[-2:])
        c.execute("INSERT INTO repos (id, created_at, url, name, path, status) VALUES (?,?,?,?,?,?)",
                  (rid, now(), url, name, str(path), "cloning"))
        return rid, True


def update_repo(repo_id, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    with tx() as c:
        c.execute(f"UPDATE repos SET {cols} WHERE id=?", (*fields.values(), repo_id))


def get_repo(repo_id):
    with tx() as c:
        return _row(c.execute("SELECT * FROM repos WHERE id=?", (repo_id,)).fetchone())


def link_repo(rid, repo_id, role):
    if role not in ("own", "baseline"):
        raise rules.Rejected("仓库角色只能是 own（自己的）或 baseline")
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO research_repos VALUES (?,?,?,?)", (rid, repo_id, role, now()))
        c.execute("UPDATE research SET updated_at=? WHERE id=?", (now(), rid))


def unlink_repo(rid, repo_id):
    with tx() as c:
        c.execute("DELETE FROM research_repos WHERE research_id=? AND repo_id=?", (rid, repo_id))


def put_code_map(rid, repo_id, paper_id, commit, rows, by):
    """One method–code map per (research, repo, paper); a new one replaces the old."""
    with tx() as c:
        c.execute("DELETE FROM code_maps WHERE research_id=? AND repo_id=? AND paper_id=?", (rid, repo_id, paper_id))
        c.execute("INSERT INTO code_maps VALUES (?,?,?,?,?,?,?)",
                  (new_id(), now(), rid, repo_id, paper_id, commit, json.dumps(rows, ensure_ascii=False)))
        _event(c, by, "codemap.put", rid, repo=repo_id, paper=paper_id)


def diagram_dir(rid, name):
    """Where a method diagram lives (paper2brain/diagram.py renders into it); name is the agent's slug."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", name or "") or not re.fullmatch(r"[\w-]+", rid or ""):
        raise rules.Rejected("图的名字用小写英文、数字和连字符，例如 framework")
    return home() / "diagrams" / rid / name


def diagrams(rid):
    """The research's method diagrams, newest first."""
    root = home() / "diagrams" / rid
    metas = [json.loads(p.read_text()) for p in root.glob("*/meta.json")] if root.is_dir() else []
    return sorted(metas, key=lambda m: m["at"], reverse=True)


def delete_diagram(rid, name):
    shutil.rmtree(diagram_dir(rid, name), ignore_errors=True)


def add_experiment(rid, raw, by, title=None):
    if not (raw or "").strip() and not (title or "").strip():
        raise rules.Rejected("实验记录至少要有原始结果或标题")
    eid = new_id()
    with tx() as c:
        if not c.execute("SELECT 1 FROM research WHERE id=?", (rid,)).fetchone():
            raise rules.Rejected("研究不存在")
        c.execute("INSERT INTO experiments (id, created_at, updated_at, research_id, title, raw, by) VALUES (?,?,?,?,?,?,?)",
                  (eid, now(), now(), rid, (title or "").strip() or None, (raw or "").strip(), by))
        c.execute("UPDATE research SET updated_at=? WHERE id=?", (now(), rid))
        _event(c, by, "experiment.add", eid)
    return eid


def update_experiment(eid, by, base_rev=None, **fields):
    """title / target / setup / results / charts / conclusion / conclusion_text / status. The agent structures a record
    until the user edits it; from then on the user's version stands (a fresh revision does not get the agent past that)."""
    if "status" in fields and fields["status"] not in EXPERIMENT_STATUS:
        raise rules.Rejected(f"未知状态：{fields['status']}")
    if fields.get("conclusion") not in (None, *CONCLUSIONS):
        raise rules.Rejected(f"结论只能是 {CONCLUSIONS}")
    for k in ("results", "charts"):
        if k in fields:
            fields[k] = json.dumps(fields[k] or [], ensure_ascii=False)
    with tx() as c:
        cur = c.execute("SELECT revision, edited_by FROM experiments WHERE id=?", (eid,)).fetchone()
        if not cur:
            raise rules.Rejected("实验不存在")
        if by != "user" and cur["edited_by"] == "user":
            raise rules.Rejected("用户已手动改过这条实验记录，agent 不能修改。需要补充时在回复里告诉用户")
        rules.check_revision(cur["revision"], base_rev)
        cols = ", ".join(f"{k}=?" for k in fields)
        c.execute(f"UPDATE experiments SET {cols}, updated_at=?, edited_by=?, revision=revision+1 WHERE id=?",
                  (*fields.values(), now(), by, eid))
        return cur["revision"] + 1


def delete_experiment(eid):
    with tx() as c:
        c.execute("DELETE FROM experiments WHERE id=?", (eid,))


def get_experiment(eid):
    with tx() as c:
        e = _row(c.execute("SELECT * FROM experiments WHERE id=?", (eid,)).fetchone())
        if e:
            e["runs"] = _rows(c.execute("SELECT * FROM runs WHERE ref=? ORDER BY created_at DESC", (eid,)))
    return e


def get_writing(rid):
    with tx() as c:
        w = _row(c.execute("SELECT * FROM writing WHERE research_id=?", (rid,)).fetchone())
    return w or {"research_id": rid, "abstract": None, "introduction": None, "proposal": None, "revision": 0,
                 "updated_at": None, "edited_by": None, "overleaf": None}


def set_overleaf(rid, project):
    with tx() as c:
        c.execute("INSERT OR IGNORE INTO writing (research_id, revision) VALUES (?, 0)", (rid,))
        c.execute("UPDATE writing SET overleaf=? WHERE research_id=?", (project, rid))


def update_writing(rid, base_rev=None, by="user", **sections):
    with tx() as c:
        cur = c.execute("SELECT revision FROM writing WHERE research_id=?", (rid,)).fetchone()
        rules.check_revision(cur["revision"] if cur else 0, base_rev)
        if not cur:
            c.execute("INSERT INTO writing (research_id, revision) VALUES (?, 0)", (rid,))
        cols = ", ".join(f"{k}=?" for k in sections)
        c.execute(f"UPDATE writing SET {cols}, updated_at=?, edited_by=?, revision=revision+1 WHERE research_id=?",
                  (*sections.values(), now(), by, rid))
        return (cur["revision"] if cur else 0) + 1


def propose_writing(rid, draft, by, base_rev=None):
    with tx() as c:
        cur = c.execute("SELECT revision FROM writing WHERE research_id=?", (rid,)).fetchone()
        rules.check_revision(cur["revision"] if cur else 0, base_rev)
        if not cur:
            c.execute("INSERT INTO writing (research_id, revision) VALUES (?, 0)", (rid,))
        c.execute("UPDATE writing SET proposal=? WHERE research_id=?",
                  (json.dumps({**draft, "by": by, "at": now()}, ensure_ascii=False), rid))


def accept_writing(rid, base_rev=None, fields=None):
    w = get_writing(rid)
    if not w["proposal"]:
        raise rules.Rejected("没有待确认的写作修改稿")
    p = w["proposal"]
    update_writing(rid, base_rev, "agent", **{k: p[k] for k in ("abstract", "introduction") if p.get(k) and (fields is None or k in fields)})
    with tx() as c:
        c.execute("UPDATE writing SET proposal=NULL WHERE research_id=?", (rid,))


def discard_writing(rid):
    with tx() as c:
        c.execute("UPDATE writing SET proposal=NULL WHERE research_id=?", (rid,))


def get_plan(rid):
    with tx() as c:
        p = _row(c.execute("SELECT * FROM plans WHERE research_id=?", (rid,)).fetchone())
    return p or {"research_id": rid, "tasks": [], "proposal": None, "revision": 0, "updated_at": None, "edited_by": None}


def update_plan(rid, tasks, by, base_rev=None):
    """tasks: already checked by rules.validate_plan."""
    with tx() as c:
        cur = c.execute("SELECT revision FROM plans WHERE research_id=?", (rid,)).fetchone()
        rules.check_revision(cur["revision"] if cur else 0, base_rev)
        c.execute("INSERT OR IGNORE INTO plans (research_id) VALUES (?)", (rid,))
        c.execute("UPDATE plans SET tasks=?, updated_at=?, edited_by=?, revision=revision+1 WHERE research_id=?",
                  (json.dumps(tasks, ensure_ascii=False), now(), by, rid))
        return (cur["revision"] if cur else 0) + 1


def propose_plan(rid, tasks, note, by, base_rev=None):
    with tx() as c:
        cur = c.execute("SELECT revision FROM plans WHERE research_id=?", (rid,)).fetchone()
        rules.check_revision(cur["revision"] if cur else 0, base_rev)
        c.execute("INSERT OR IGNORE INTO plans (research_id) VALUES (?)", (rid,))
        c.execute("UPDATE plans SET proposal=? WHERE research_id=?",
                  (json.dumps({"tasks": tasks, "note": note, "by": by, "at": now()}, ensure_ascii=False), rid))


def accept_plan(rid, base_rev=None):
    p = get_plan(rid)["proposal"]
    if not p:
        raise rules.Rejected("没有待确认的计划修改稿")
    update_plan(rid, p["tasks"], "agent", base_rev)
    discard_plan(rid)


def discard_plan(rid):
    with tx() as c:
        c.execute("UPDATE plans SET proposal=NULL WHERE research_id=?", (rid,))


def get_figures(rid):
    with tx() as c:
        f = _row(c.execute("SELECT * FROM exp_figures WHERE research_id=?", (rid,)).fetchone())
    return f or {"research_id": rid, "design": None, "figures": [], "cells": [], "proposal": None, "revision": 0, "updated_at": None, "edited_by": None}


def update_figures(rid, figures, by, base_rev=None, design=None):
    """figures: already checked by rules.validate_figures; design (rules.validate_setup) None keeps the old setup.
    Cells of rows, series or figures that are gone are dropped."""
    keep = {(f["id"], r, s) for f in figures for r in f["rows"] for s in f["series"]}
    with tx() as c:
        cur = _row(c.execute("SELECT revision, cells FROM exp_figures WHERE research_id=?", (rid,)).fetchone())
        rules.check_revision(cur["revision"] if cur else 0, base_rev)
        cells = [x for x in (cur["cells"] if cur else []) if (x["figure"], x["row"], x["series"]) in keep]
        c.execute("INSERT OR IGNORE INTO exp_figures (research_id) VALUES (?)", (rid,))
        c.execute("UPDATE exp_figures SET figures=?, cells=?, design=COALESCE(?, design), updated_at=?, edited_by=?, revision=revision+1 "
                  "WHERE research_id=?", (json.dumps(figures, ensure_ascii=False), json.dumps(cells, ensure_ascii=False),
                                          json.dumps(design, ensure_ascii=False) if design else None, now(), by, rid))
        return (cur["revision"] if cur else 0) + 1


def propose_figures(rid, figures, note, by, base_rev=None, design=None):
    with tx() as c:
        cur = c.execute("SELECT revision FROM exp_figures WHERE research_id=?", (rid,)).fetchone()
        rules.check_revision(cur["revision"] if cur else 0, base_rev)
        c.execute("INSERT OR IGNORE INTO exp_figures (research_id) VALUES (?)", (rid,))
        c.execute("UPDATE exp_figures SET proposal=? WHERE research_id=?",
                  (json.dumps({"design": design, "figures": figures, "note": note, "by": by, "at": now()}, ensure_ascii=False), rid))


def accept_figures(rid, base_rev=None):
    p = get_figures(rid)["proposal"]
    if not p:
        raise rules.Rejected("没有待确认的实验设计修改稿")
    update_figures(rid, p["figures"], "agent", base_rev, p.get("design"))
    discard_figures(rid)


def discard_figures(rid):
    with tx() as c:
        c.execute("UPDATE exp_figures SET proposal=NULL WHERE research_id=?", (rid,))


def fill_cells(rid, cells):
    """cells: already checked by rules.validate_cells; each replaces whatever filled that cell (experiment None clears it)."""
    with tx() as c:
        cur = _row(c.execute("SELECT cells FROM exp_figures WHERE research_id=?", (rid,)).fetchone())
        if not cur:
            raise rules.Rejected("这个研究还没有实验设计")
        key = lambda x: (x["figure"], x["row"], x["series"])  # noqa: E731
        new = {key(x): x for x in cur["cells"]} | {key(x): x for x in cells}
        c.execute("UPDATE exp_figures SET cells=? WHERE research_id=?",
                  (json.dumps([x for x in new.values() if x["experiment"]], ensure_ascii=False), rid))


def get_related(rid):
    with tx() as c:
        r = _row(c.execute("SELECT * FROM related_work WHERE research_id=?", (rid,)).fetchone())
    return r or {"research_id": rid, "groups": [], "comparison": None, "proposal": None, "revision": 0, "updated_at": None, "edited_by": None}


def update_related(rid, groups, by, base_rev=None, comparison=None):
    """groups: already checked by rules.validate_related; comparison (rules.validate_comparison) None keeps the old table."""
    with tx() as c:
        cur = c.execute("SELECT revision FROM related_work WHERE research_id=?", (rid,)).fetchone()
        rules.check_revision(cur["revision"] if cur else 0, base_rev)
        c.execute("INSERT OR IGNORE INTO related_work (research_id) VALUES (?)", (rid,))
        c.execute("UPDATE related_work SET groups=?, comparison=COALESCE(?, comparison), updated_at=?, edited_by=?, revision=revision+1 "
                  "WHERE research_id=?", (json.dumps(groups, ensure_ascii=False),
                                          json.dumps(comparison, ensure_ascii=False) if comparison else None, now(), by, rid))
        return (cur["revision"] if cur else 0) + 1


def propose_related(rid, groups, note, by, base_rev=None, comparison=None):
    with tx() as c:
        cur = c.execute("SELECT revision FROM related_work WHERE research_id=?", (rid,)).fetchone()
        rules.check_revision(cur["revision"] if cur else 0, base_rev)
        c.execute("INSERT OR IGNORE INTO related_work (research_id) VALUES (?)", (rid,))
        c.execute("UPDATE related_work SET proposal=? WHERE research_id=?",
                  (json.dumps({"groups": groups, "comparison": comparison, "note": note, "by": by, "at": now()}, ensure_ascii=False), rid))


def accept_related(rid, base_rev=None):
    """The papers of the accepted lines of work become materials of the research."""
    p = get_related(rid)["proposal"]
    if not p:
        raise rules.Rejected("没有待确认的相关工作修改稿")
    update_related(rid, p["groups"], "agent", base_rev, p.get("comparison"))
    discard_related(rid)
    for pid in dict.fromkeys(pid for g in p["groups"] for pid in g["papers"]):
        link_research(rid, "paper", pid, "agent")


def discard_related(rid):
    with tx() as c:
        c.execute("UPDATE related_work SET proposal=NULL WHERE research_id=?", (rid,))


def add_doc(rid, filename, mime, data, text, by):
    """A file the user brought when starting a research: their own draft, notes, a whiteboard photo."""
    did = new_id()
    folder = home() / "assets" / "research" / rid
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{did[:8]}-{Path(filename).name}"
    path.write_bytes(data)
    with tx() as c:
        c.execute("INSERT INTO research_docs VALUES (?,?,?,?,?,?,?)", (did, now(), rid, filename, mime, str(path), text))
        c.execute("INSERT INTO research_links (research_id, kind, ref, by, at) VALUES (?,?,?,?,?)", (rid, "doc", did, by, now()))
    return did


def get_doc(did):
    with tx() as c:
        return _row(c.execute("SELECT * FROM research_docs WHERE id=?", (did,)).fetchone())


def research_extras(rid):
    """Repos, method–code maps, experiments and writing of a research, for its page and for the agent."""
    with tx() as c:
        repos = _rows(c.execute(
            "SELECT r.*, rr.role FROM research_repos rr JOIN repos r ON r.id=rr.repo_id WHERE rr.research_id=? ORDER BY rr.at", (rid,)))
        maps = _rows(c.execute("SELECT * FROM code_maps WHERE research_id=? ORDER BY created_at DESC", (rid,)))
        exps = _rows(c.execute("SELECT * FROM experiments WHERE research_id=? ORDER BY created_at DESC", (rid,)))
        ids = [e["id"] for e in exps]
        runs = {}
        if ids:
            q = ",".join("?" * len(ids))
            for r in _rows(c.execute(f"SELECT * FROM runs WHERE ref IN ({q}) ORDER BY created_at DESC", ids)):
                runs.setdefault(r["ref"], []).append(r)
    for e in exps:
        e["runs"] = runs.get(e["id"], [])
    return {"repos": repos, "code_maps": maps, "experiments": exps, "writing": get_writing(rid), "plan": get_plan(rid),
            "related": get_related(rid), "figures": get_figures(rid), "diagrams": diagrams(rid)}


# ---------- anchored discussions (selected text in a card, report or PDF) ----------

ANCHOR_SURFACES = ("card", "report", "pdf")


def add_anchor(pid, surface, quote, by, page=None, section=None):
    """A discussion pinned to a passage. Its thread (scope anchor:<id>) stays attached to the passage."""
    quote = re.sub(r"\s+", " ", quote or "").strip()
    if surface not in ANCHOR_SURFACES:
        raise rules.Rejected(f"只能在 {ANCHOR_SURFACES} 上划词")
    if not 2 <= len(quote) <= 2000:
        raise rules.Rejected("选中的文字太短或太长（2-2000 字）")
    aid = new_id()
    with tx() as c:
        if not c.execute("SELECT 1 FROM papers WHERE id=?", (pid,)).fetchone():
            raise rules.Rejected("论文不在库里")
        c.execute("INSERT INTO anchors VALUES (?,?,?,?,?,?,?,?)", (aid, now(), pid, surface, quote, page, section, by))
    return aid


def get_anchor(aid):
    with tx() as c:
        return _row(c.execute("SELECT * FROM anchors WHERE id=?", (aid,)).fetchone())


def anchors_for(pid):
    with tx() as c:
        return _rows(c.execute(
            "SELECT a.*, t.id AS thread_id, (SELECT count(*) FROM messages m WHERE m.thread_id=t.id) AS message_count, "
            "(SELECT coalesce(nullif(m.text, ''), r.text) FROM messages m LEFT JOIN runs r ON r.id=m.run_id "
            " WHERE m.thread_id=t.id ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1) AS last_text "
            "FROM anchors a LEFT JOIN threads t ON t.scope='anchor:' || a.id WHERE a.paper_id=? "
            "GROUP BY a.id ORDER BY a.created_at", (pid,),
        ))


def delete_anchor(aid):
    with tx() as c:
        _drop_threads(c, _ids(c, "SELECT id FROM threads WHERE scope=?", (f"anchor:{aid}",)))
        c.execute("DELETE FROM anchors WHERE id=?", (aid,))


# ---------- agent runs ----------

def add_run(run_id, agent_id, kind, ref, prompt, status, launch=None):
    with tx() as c:
        c.execute(
            "INSERT OR IGNORE INTO runs (id, created_at, updated_at, agent_id, kind, ref, prompt, status, launch) VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, now(), now(), agent_id, kind, ref, prompt, status, launch and json.dumps(launch, ensure_ascii=False)),
        )


def replace_run(placeholder, run_id, agent_id, status):
    """Swap the placeholder shown while Cursor was still creating the agent for the real run."""
    with tx() as c:
        c.execute("UPDATE runs SET id=?, agent_id=?, status=?, updated_at=? WHERE id=?", (run_id, agent_id, status, now(), placeholder))
        c.execute("UPDATE messages SET run_id=? WHERE run_id=?", (run_id, placeholder))


def update_run(run_id, **fields):
    if "tools" in fields:
        fields["tools"] = json.dumps(fields["tools"], ensure_ascii=False)
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=?" for k in fields)
    with tx() as c:
        c.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))


def get_run(run_id):
    with tx() as c:
        return _row(c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone())


def last_run(kind):
    with tx() as c:
        return _row(c.execute("SELECT * FROM runs WHERE kind=? ORDER BY created_at DESC LIMIT 1", (kind,)).fetchone())


def unfinished_runs():
    """Oldest first, so waiting runs start in the order they were asked for."""
    with tx() as c:
        return _rows(c.execute(f"SELECT * FROM runs WHERE status IN {_UNFINISHED_SQL} ORDER BY created_at, rowid"))


def stale_runs(cutoff):
    """Live runs started before cutoff (ISO time)."""
    with tx() as c:
        return _rows(c.execute("SELECT * FROM runs WHERE status IN ('CREATING','RUNNING','QUEUED') "
                               "AND coalesce(started_at, created_at) < ?", (cutoff,)))


def forget_agent(agent_id):
    """An agent that was never created: threads and captures must not keep following up on it."""
    with tx() as c:
        c.execute("UPDATE threads SET agent_id=NULL WHERE agent_id=?", (agent_id,))
        c.execute("UPDATE captures SET agent_id=NULL WHERE agent_id=?", (agent_id,))
        c.execute("UPDATE kv SET value='' WHERE value=?", (agent_id,))


# ---------- search ----------

PAPER_FIELDS = ("p.title", "p.id", "p.authors", "p.abstract", "p.card", "p.report", "pt.text")


def _every_word(terms, fields):
    """WHERE clause: each word appears in at least one of the fields (case-insensitive for Latin letters)."""
    esc = [t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") for t in terms]
    clause = " AND ".join("(" + " OR ".join(f"{f} LIKE ? ESCAPE '\\'" for f in fields) + ")" for _ in terms)
    return clause, [f"%{t}%" for t in esc for _ in fields]


def _snippet(text, terms, n=90):
    low = (text or "").lower()
    at = min((i for i in (low.find(t.lower()) for t in terms) if i >= 0), default=0)
    s = re.sub(r"\s+", " ", (text or "")[max(0, at - 30): at + n]).strip()
    return ("…" if at > 30 else "") + s


def search(q, limit=30):
    """"我记得有篇……": the words may come from anywhere, in any order. Every word must match; papers match on title,
    authors, abstract, card, report or full text, and say where (hits)."""
    terms = q.split()[:8]
    empty = {"papers": [], "captures": [], "threads": [], "notes": [], "messages": [], "research": []}
    if not terms:
        return empty
    with tx() as c:
        where, args = _every_word(terms, PAPER_FIELDS)
        papers = _rows(c.execute(
            f"SELECT p.id, p.title, p.authors, p.abstract, p.card, p.report, pt.text AS fulltext FROM papers p "
            f"LEFT JOIN paper_text pt ON pt.paper_id=p.id WHERE {LISTED} AND {where} LIMIT ?", (*args, limit)))
        where, args = _every_word(terms, ["raw", "title", "snapshot_text", "author"])
        captures = _rows(c.execute(
            f"SELECT id, created_at, title, author, channel, source, substr(raw,1,200) AS raw FROM captures WHERE {where} "
            f"ORDER BY created_at DESC LIMIT ?", (*args, limit)))
        where, args = _every_word(terms, ["t.title"])
        threads = _rows(c.execute(_HEAD + f"WHERE {where} ORDER BY t.created_at DESC LIMIT ?", (*args, limit)))
        where, args = _every_word(terms, ["text"])
        notes = _rows(c.execute(f"SELECT * FROM notes WHERE {where} ORDER BY created_at DESC LIMIT ?", (*args, limit)))
        where, args = _every_word(terms, ["coalesce(nullif(m.text, ''), r.text, '')"])
        messages = _rows(c.execute(
            f"SELECT m.id, m.role, m.created_at, m.thread_id, t.scope, coalesce(nullif(m.text, ''), r.text) AS text "
            f"FROM messages m JOIN threads t ON t.id=m.thread_id LEFT JOIN runs r ON r.id=m.run_id WHERE {where} "
            f"ORDER BY m.created_at DESC LIMIT ?", (*args, limit)))
        where, args = _every_word(terms, ["title", "summary", "body"])
        research = _rows(c.execute(f"SELECT id, title, summary, stage, updated_at FROM research WHERE {where} "
                                   f"ORDER BY updated_at DESC LIMIT ?", (*args, limit)))
    for p in papers:
        card, report = p.pop("card"), p.pop("report")
        fields = {"标题": p["title"], "编号": p["id"], "作者": " ".join(p["authors"] or []), "摘要": p.pop("abstract"),
                  "快读卡": card and json.dumps(card, ensure_ascii=False), "深读报告": report and json.dumps(report, ensure_ascii=False),
                  "原文": p.pop("fulltext")}
        p["hits"] = [k for k, v in fields.items() if v and any(t.lower() in v.lower() for t in terms)]
        if "原文" in p["hits"]:
            texts = pages(p["id"])
            n = next((i for i, text in enumerate(texts, 1) if any(t.lower() in text.lower() for t in terms)), None)
            p["hit_page"], p["hit_text"] = n, n and _snippet(texts[n - 1], terms)
        p["tldr"] = card and card.get("tldr")
    for m in messages:
        m["text"] = _snippet(m["text"], terms, 140)
    return {**empty, "papers": papers, "captures": captures, "threads": threads, "notes": notes, "messages": messages,
            "research": research}
