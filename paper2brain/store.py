"""SQLite storage. The only module that touches the database; every write goes through a transaction here."""
import json
import re
import os
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import rules

TOPIC_TREE = [
    ("VLM", ["具身大脑", "Jev 决策模型", "通用多模态大模型", "扩散VLM"]),
    ("VLA", ["tokenizer", "推理加速", "实时推理"]),
    ("WAM", ["世界模型", "表征研究", "WAN base", "V-JEPA"]),
]
TRIAGE = ("inbox", "deep", "archived", "ignored")
JSON_COLS = {"links", "candidates", "authors", "card", "report", "tools", "meta", "body", "proposal", "results", "rows"}
RESEARCH_ELEMENTS = ("problem", "method", "validation")
RESEARCH_FIELDS = RESEARCH_ELEMENTS + ("notes",)
LINK_KINDS = ("paper", "thought", "message", "thread", "capture", "note", "doc")

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


# Append only: PRAGMA user_version is the number of steps already applied. Each step runs once, in its own
# transaction, after a backup of the database as it was.
MIGRATIONS = [_v1_baseline, _v2_indexes]


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
                c.execute(
                    "INSERT INTO thoughts (id, created_at, text, by, capture_id) VALUES (?,?,?,?,?)",
                    (new_id(), now(), thought.strip(), "user", capture_id),
                )
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


def delete_capture(capture_id):
    """Removes the capture and what was derived only from it. Papers stay: they are facts and may have other sources."""
    with tx() as c:
        c.execute("DELETE FROM captures WHERE id=?", (capture_id,))
        _event(c, "user", "capture.delete", capture_id)
    shutil.rmtree(home() / "assets" / capture_id, ignore_errors=True)


def get_capture(capture_id):
    with tx() as c:
        cap = _row(c.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone())
        if not cap:
            return None
        cap["attachments"] = _rows(c.execute("SELECT id, filename, mime FROM attachments WHERE capture_id=?", (capture_id,)))
        cap["thoughts"] = _rows(c.execute("SELECT * FROM thoughts WHERE capture_id=? ORDER BY created_at", (capture_id,)))
        thought_research = _research_for(c, "thought", [t["id"] for t in cap["thoughts"]])
        for t in cap["thoughts"]:
            t["research"] = thought_research.get(t["id"], [])
        cap["papers"] = _rows(c.execute(
            "SELECT p.id, p.title, p.card IS NOT NULL AS has_card, cp.state, cp.by, cp.evidence "
            "FROM capture_papers cp JOIN papers p ON p.id=cp.paper_id WHERE cp.capture_id=? ORDER BY cp.at",
            (capture_id,),
        ))
        cap["runs"] = _rows(c.execute("SELECT * FROM runs WHERE ref=? ORDER BY created_at DESC", (capture_id,)))
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


def list_captures(view="inbox", limit=200):
    where = {"pending": "c.status IN ('needs_confirm','unmapped','failed') AND c.triage!='ignored'"}.get(
        view, "c.triage=?" if view in TRIAGE else "1=1"
    )
    args = (view,) if view in TRIAGE else ()
    with tx() as c:
        caps = _rows(c.execute(
            f"SELECT c.id, c.created_at, substr(c.raw,1,300) AS raw, c.source, c.channel, c.title, c.author, c.status, "
            f"c.note, c.triage, c.agent_id, (SELECT text FROM thoughts t WHERE t.capture_id=c.id ORDER BY created_at DESC LIMIT 1) AS thought, "
            f"(SELECT status FROM runs r WHERE r.ref=c.id ORDER BY created_at DESC LIMIT 1) AS run_status, "
            f"(SELECT count(*) FROM attachments a WHERE a.capture_id=c.id) AS image_count "
            f"FROM captures c WHERE {where} ORDER BY c.created_at DESC LIMIT ?",
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
        f"SELECT DISTINCT ref FROM runs WHERE kind='deepread' AND status IN ('CREATING','RUNNING','QUEUED') AND ref IN ({q})", pids)}
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
            "AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.ref=p.id AND r.kind='deepread' AND r.status IN ('CREATING','RUNNING','QUEUED')) "
            "GROUP BY p.id ORDER BY max(COALESCE((SELECT max(e.at) FROM events e WHERE e.action='capture.triage' AND e.target=c.id), c.created_at)) DESC"
        )]


def counts():
    with tx() as c:
        out = {t: 0 for t in TRIAGE}
        for r in c.execute("SELECT triage, count(*) n FROM captures GROUP BY triage"):
            out[r["triage"]] = r["n"]
        out["pending"] = c.execute(
            "SELECT count(*) FROM captures WHERE status IN ('needs_confirm','unmapped','failed') AND triage!='ignored'"
        ).fetchone()[0]
        out["papers"] = c.execute("SELECT count(DISTINCT paper_id) FROM capture_papers WHERE state='confirmed'").fetchone()[0]
        out["thoughts"] = c.execute("SELECT count(*) FROM thoughts").fetchone()[0]
        out["research"] = c.execute("SELECT count(*) FROM research").fetchone()[0]
        out["unsorted"] = c.execute(
            "SELECT count(DISTINCT cp.paper_id) FROM capture_papers cp WHERE cp.state='confirmed' "
            "AND NOT EXISTS (SELECT 1 FROM paper_topics pt WHERE pt.paper_id=cp.paper_id)"
        ).fetchone()[0]
    return out


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
            "SELECT c.id, c.created_at, c.channel, c.title, c.author, c.url, substr(c.raw,1,300) AS raw, c.triage, cp.state, cp.by "
            "FROM capture_papers cp JOIN captures c ON c.id=cp.capture_id WHERE cp.paper_id=? AND cp.state!='rejected' "
            "ORDER BY c.created_at", (pid,),
        ))
        cap_ids = [x["id"] for x in p["captures"]]
        q = ",".join("?" * len(cap_ids)) or "''"
        p["thoughts"] = _rows(c.execute(
            f"SELECT * FROM thoughts WHERE paper_id=? OR capture_id IN ({q}) ORDER BY created_at", (pid, *cap_ids)
        ))
        p["notes"] = _rows(c.execute("SELECT * FROM notes WHERE paper_id=? ORDER BY created_at", (pid,)))
        p["research"] = _research_for(c, "paper", [pid]).get(pid, [])
        p["anchors"] = []
        thought_research = _research_for(c, "thought", [t["id"] for t in p["thoughts"]])
        for t in p["thoughts"]:
            t["research"] = thought_research.get(t["id"], [])
        p["runs"] = _rows(c.execute("SELECT * FROM runs WHERE ref=? ORDER BY created_at DESC", (pid,)))
    if with_anchors:
        p["anchors"] = anchors_for(pid)
    return p


def list_papers(topic=None):
    """topic=None: every paper with a confirmed source; '' : papers without a topic; a path includes its children."""
    sql = (
        "SELECT p.id, p.title, p.authors, p.published, p.card, p.report IS NOT NULL AS has_report, "
        "max(c.created_at) AS last_captured, (SELECT min(CASE c2.triage WHEN 'deep' THEN 0 ELSE 1 END) FROM capture_papers cp2 "
        "JOIN captures c2 ON c2.id=cp2.capture_id WHERE cp2.paper_id=p.id AND cp2.state='confirmed') = 0 AS deep "
        "FROM papers p JOIN capture_papers cp ON cp.paper_id=p.id AND cp.state='confirmed' JOIN captures c ON c.id=cp.capture_id "
    )
    args = ()
    if topic == "":
        sql += "WHERE NOT EXISTS (SELECT 1 FROM paper_topics pt WHERE pt.paper_id=p.id) "
    elif topic:
        sql += "WHERE EXISTS (SELECT 1 FROM paper_topics pt WHERE pt.paper_id=p.id AND (pt.topic=? OR pt.topic LIKE ?)) "
        args = (topic, topic + "/%")
    sql += "GROUP BY p.id ORDER BY last_captured DESC"
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
        rows = _rows(c.execute("SELECT * FROM topics ORDER BY parent IS NOT NULL, position"))
        n = {r["topic"]: r["n"] for r in c.execute(
            "SELECT pt.topic, count(DISTINCT pt.paper_id) n FROM paper_topics pt "
            "JOIN capture_papers cp ON cp.paper_id=pt.paper_id AND cp.state='confirmed' GROUP BY pt.topic"
        )}
        under = {r["parent"]: r["n"] for r in c.execute(
            "SELECT t.parent, count(DISTINCT pt.paper_id) n FROM paper_topics pt JOIN topics t ON t.path=pt.topic "
            "JOIN capture_papers cp ON cp.paper_id=pt.paper_id AND cp.state='confirmed' WHERE t.parent IS NOT NULL GROUP BY t.parent"
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


def set_topics(pid, paths, by):
    """The agent may classify until the user touches a paper's topics; from then on the user's choice is kept."""
    with tx() as c:
        p = c.execute("SELECT topics_locked FROM papers WHERE id=?", (pid,)).fetchone()
        if not p:
            raise rules.Rejected(f"论文 {pid} 不在库里")
        if by != "user" and p["topics_locked"]:
            raise rules.Rejected("用户已手动调整过这篇论文的主题，agent 不能修改")
        known = {r["path"] for r in c.execute("SELECT path FROM topics")}
        unknown = [t for t in paths if t not in known]
        if unknown:
            raise rules.Rejected(f"未知主题：{unknown}。可用主题见 ./p2b topics")
        c.execute("DELETE FROM paper_topics WHERE paper_id=?", (pid,))
        for t in dict.fromkeys(paths):
            c.execute("INSERT INTO paper_topics VALUES (?,?,?)", (pid, t, by))
        if by == "user":
            c.execute("UPDATE papers SET topics_locked=1 WHERE id=?", (pid,))
        _event(c, by, "topics.set", pid, topics=list(paths))


# ---------- thoughts & notes ----------

def add_thought(text, by, capture_id=None, paper_id=None, gist=None):
    if not (text or "").strip():
        raise rules.Rejected("想法不能为空")
    tid = new_id()
    with tx() as c:
        c.execute(
            "INSERT INTO thoughts (id, created_at, text, by, capture_id, paper_id, gist, gist_by) VALUES (?,?,?,?,?,?,?,?)",
            (tid, now(), text.strip(), by, capture_id, paper_id, (gist or "").strip() or None, gist and by),
        )
    return tid


def set_gist(tid, gist, by):
    """The one sentence that matters, in the user's terms. The agent only fills empty ones; the user's edit is final."""
    gist = (gist or "").strip()
    if not gist:
        raise rules.Rejected("提炼不能为空")
    with tx() as c:
        cur = c.execute("SELECT gist_by FROM thoughts WHERE id=?", (tid,)).fetchone()
        if not cur:
            raise rules.Rejected("想法不存在")
        if by != "user" and cur["gist_by"] == "user":
            raise rules.Rejected("用户已手动改过这条提炼，agent 不能修改")
        c.execute("UPDATE thoughts SET gist=?, gist_by=? WHERE id=?", (gist, by, tid))


def pending_gists(limit=12):
    with tx() as c:
        return [r["id"] for r in c.execute(
            "SELECT id FROM thoughts WHERE gist IS NULL ORDER BY created_at LIMIT ?", (limit,)
        )]


def get_thought(tid):
    """A thought with everything needed to keep thinking about it: its paper, its researches, its neighbours."""
    t = next((x for x in list_thoughts() if x["id"] == tid), None)
    if not t:
        return None
    with tx() as c:
        pid = t["about_paper"]
        if pid:
            p = _row(c.execute("SELECT id, title, card, page_count FROM papers WHERE id=?", (pid,)).fetchone())
            card = p.pop("card") or {}
            t["paper"] = {**p, "tldr": card.get("tldr"), "results": (card.get("results") or [])[:3]}
        rids = [r["id"] for r in t["research"]]
        t["siblings"] = [x for x in list_thoughts() if x["id"] != tid and (
            (pid and x["about_paper"] == pid) or any(r["id"] in rids for r in x["research"]))][:8]
        if t["capture_id"]:
            t["capture"] = _row(c.execute("SELECT id, title, raw, channel, author FROM captures WHERE id=?", (t["capture_id"],)).fetchone())
    return t


def kv_get(key):
    with tx() as c:
        r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return r and r["value"]


def kv_set(key, value):
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, value))


def list_thoughts():
    with tx() as c:
        out = _rows(c.execute(
            "SELECT t.*, coalesce(p.title, (SELECT p2.title FROM capture_papers cp JOIN papers p2 ON p2.id=cp.paper_id "
            "WHERE cp.capture_id=t.capture_id AND cp.state='confirmed' LIMIT 1)) AS paper_title, "
            "coalesce(t.paper_id, (SELECT cp.paper_id FROM capture_papers cp WHERE cp.capture_id=t.capture_id AND cp.state='confirmed' LIMIT 1)) AS about_paper, "
            "c.title AS capture_title FROM thoughts t LEFT JOIN papers p ON p.id=t.paper_id "
            "LEFT JOIN captures c ON c.id=t.capture_id ORDER BY t.created_at DESC"
        ))
        used = _research_for(c, "thought", [t["id"] for t in out])
    for t in out:
        t["research"] = used.get(t["id"], [])
    return out


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
            t = _row(c.execute("SELECT * FROM threads WHERE scope=? ORDER BY created_at DESC LIMIT 1", (scope,)).fetchone())
        if not t:
            t = {"id": new_id(), "created_at": now(), "scope": scope, "title": None, "agent_id": None}
            c.execute("INSERT INTO threads VALUES (?,?,?,?,?)", tuple(t.values()))
        t["messages"] = _rows(c.execute(
            "SELECT m.*, r.status AS run_status, r.text AS run_text, r.tools AS run_tools, r.error AS run_error "
            "FROM messages m LEFT JOIN runs r ON r.id=m.run_id WHERE m.thread_id=? ORDER BY m.created_at, m.rowid", (t["id"],),
        ))
        used = _research_for(c, "message", [m["id"] for m in t["messages"]])
    for m in t["messages"]:
        if m["run_tools"]:
            m["run_tools"] = json.loads(m["run_tools"])
        m["research"] = used.get(m["id"], [])
    return t


def message(mid):
    with tx() as c:
        m = _row(c.execute(
            "SELECT m.*, t.scope, r.text AS run_text FROM messages m JOIN threads t ON t.id=m.thread_id "
            "LEFT JOIN runs r ON r.id=m.run_id WHERE m.id=?", (mid,),
        ).fetchone())
    if not m:
        raise rules.Rejected("这条讨论消息不存在了")
    return m


def new_thread(scope):
    with tx() as c:
        c.execute("INSERT INTO threads VALUES (?,?,?,?,?)", (new_id(), now(), scope, None, None))


def set_thread_agent(thread_id, agent_id):
    with tx() as c:
        c.execute("UPDATE threads SET agent_id=? WHERE id=?", (agent_id, thread_id))


def add_message(thread_id, role, text, run_id=None):
    mid = new_id()
    with tx() as c:
        c.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)", (mid, now(), thread_id, role, text, run_id))
    return mid


# ---------- research ----------
# A research gathers materials (discussions, thoughts, papers, ...) into three elements: problem, method, validation.
# Only the user establishes a research; the agent only ever proposes drafts the user accepts or discards.

def _research_for(c, kind, refs):
    """Reverse links: which researches use each of these objects as material."""
    out = {}
    if refs:
        q = ",".join("?" * len(refs))
        for r in c.execute(
            f"SELECT l.ref, x.id, x.title, x.stage FROM research_links l JOIN research x ON x.id=l.research_id "
            f"WHERE l.kind=? AND l.ref IN ({q}) ORDER BY x.updated_at DESC", (kind, *refs),
        ):
            out.setdefault(r["ref"], []).append({"id": r["id"], "title": r["title"], "stage": r["stage"]})
    return out


def _check_topics(c, paths):
    known = {r["path"] for r in c.execute("SELECT path FROM topics")}
    unknown = [t for t in paths if t not in known]
    if unknown:
        raise rules.Rejected(f"未知主题：{unknown}。可用主题见 ./p2b topics")


def _check_link(c, kind, ref):
    if kind not in LINK_KINDS:
        raise rules.Rejected(f"不能关联 {kind}，可选：{', '.join(LINK_KINDS)}")
    table = {"paper": "papers", "thought": "thoughts", "message": "messages", "thread": "threads", "doc": "research_docs",
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
            c.execute("INSERT INTO research_links VALUES (?,?,?,?,?)", (rid, kind, ref, by, now()))
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


def accept_proposal(rid, base_rev=None):
    with tx() as c:
        cur = _current(c, rid, base_rev)
        if not cur["proposal"]:
            raise rules.Rejected("没有待确认的修改稿")
        p = json.loads(cur["proposal"])
        body = {k: p.get(k) for k in RESEARCH_FIELDS}
        c.execute(
            "UPDATE research SET title=?, summary=?, body=?, proposal=NULL, updated_at=?, edited_by='agent', revision=revision+1 WHERE id=?",
            (p.get("title") or cur["title"], p.get("summary") or cur["summary"], json.dumps(body, ensure_ascii=False), now(), rid),
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


def link_research(rid, kind, ref, by):
    with tx() as c:
        if not c.execute("SELECT 1 FROM research WHERE id=?", (rid,)).fetchone():
            raise rules.Rejected("研究不存在")
        _check_link(c, kind, ref)
        c.execute("INSERT OR IGNORE INTO research_links VALUES (?,?,?,?,?)", (rid, kind, ref, by, now()))
        c.execute("UPDATE research SET updated_at=? WHERE id=?", (now(), rid))


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
    if kind == "thought":
        r = c.execute(
            "SELECT t.text, t.created_at, t.capture_id, coalesce(t.paper_id, (SELECT cp.paper_id FROM capture_papers cp "
            "WHERE cp.capture_id=t.capture_id AND cp.state='confirmed' LIMIT 1)) AS paper_id FROM thoughts t WHERE t.id=?", (ref,),
        ).fetchone()
        return r and {"label": r["text"], "at": r["created_at"], "paper_id": r["paper_id"], "capture_id": r["capture_id"]}
    if kind == "message":
        r = c.execute(
            "SELECT m.role, coalesce(nullif(m.text, ''), r.text) AS text, m.created_at, t.scope, t.id AS thread_id "
            "FROM messages m JOIN threads t ON t.id=m.thread_id LEFT JOIN runs r ON r.id=m.run_id WHERE m.id=?", (ref,),
        ).fetchone()
        return r and {"label": (r["text"] or "")[:200], "at": r["created_at"], "role": r["role"], "scope": r["scope"], "thread_id": r["thread_id"]}
    if kind == "thread":
        r = c.execute(
            "SELECT t.scope, t.created_at, (SELECT text FROM messages WHERE thread_id=t.id AND role='user' ORDER BY created_at LIMIT 1) AS first "
            "FROM threads t WHERE t.id=?", (ref,),
        ).fetchone()
        return r and {"label": r["first"] or "一段讨论", "at": r["created_at"], "scope": r["scope"]}
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
            x["links"].append({"kind": r["kind"], "ref": r["ref"], "by": r["by"], "missing": info is None, **(info or {})})
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
    """title / target / setup / results / conclusion / conclusion_text / status. The agent structures a record until
    the user edits it; from then on the user's version stands (a fresh revision does not get the agent past that)."""
    if "status" in fields and fields["status"] not in EXPERIMENT_STATUS:
        raise rules.Rejected(f"未知状态：{fields['status']}")
    if fields.get("conclusion") not in (None, *CONCLUSIONS):
        raise rules.Rejected(f"结论只能是 {CONCLUSIONS}")
    if "results" in fields:
        fields["results"] = json.dumps(fields["results"] or [], ensure_ascii=False)
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
                 "updated_at": None, "edited_by": None}


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


def accept_writing(rid, base_rev=None):
    w = get_writing(rid)
    if not w["proposal"]:
        raise rules.Rejected("没有待确认的写作修改稿")
    p = w["proposal"]
    update_writing(rid, base_rev, "agent", **{k: p[k] for k in ("abstract", "introduction") if p.get(k)})
    with tx() as c:
        c.execute("UPDATE writing SET proposal=NULL WHERE research_id=?", (rid,))


def discard_writing(rid):
    with tx() as c:
        c.execute("UPDATE writing SET proposal=NULL WHERE research_id=?", (rid,))


def add_doc(rid, filename, mime, data, text, by):
    """A file the user brought when starting a research: their own draft, notes, a whiteboard photo."""
    did = new_id()
    folder = home() / "assets" / "research" / rid
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{did[:8]}-{Path(filename).name}"
    path.write_bytes(data)
    with tx() as c:
        c.execute("INSERT INTO research_docs VALUES (?,?,?,?,?,?,?)", (did, now(), rid, filename, mime, str(path), text))
        c.execute("INSERT INTO research_links VALUES (?,?,?,?,?)", (rid, "doc", did, by, now()))
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
    return {"repos": repos, "code_maps": maps, "experiments": exps, "writing": get_writing(rid)}


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
        for t in c.execute("SELECT id FROM threads WHERE scope=?", (f"anchor:{aid}",)).fetchall():
            c.execute("DELETE FROM messages WHERE thread_id=?", (t["id"],))
            c.execute("DELETE FROM threads WHERE id=?", (t["id"],))
        c.execute("DELETE FROM anchors WHERE id=?", (aid,))


# ---------- agent runs ----------

def add_run(run_id, agent_id, kind, ref, prompt, status):
    with tx() as c:
        c.execute(
            "INSERT OR IGNORE INTO runs (id, created_at, updated_at, agent_id, kind, ref, prompt, status) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, now(), now(), agent_id, kind, ref, prompt, status),
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
    with tx() as c:
        return _rows(c.execute("SELECT * FROM runs WHERE status IN ('CREATING','RUNNING','QUEUED')"))


# ---------- search ----------

def search(q, limit=30):
    like = f"%{q.strip()}%"
    with tx() as c:
        papers = _rows(c.execute(
            "SELECT DISTINCT p.id, p.title, p.card FROM papers p JOIN capture_papers cp ON cp.paper_id=p.id AND cp.state='confirmed' "
            "WHERE p.id LIKE ? OR p.title LIKE ? OR p.abstract LIKE ? OR p.card LIKE ? OR p.report LIKE ? LIMIT ?",
            (like, like, like, like, like, limit),
        ))
        captures = _rows(c.execute(
            "SELECT id, created_at, title, author, channel, substr(raw,1,200) AS raw FROM captures "
            "WHERE raw LIKE ? OR title LIKE ? OR snapshot_text LIKE ? OR author LIKE ? ORDER BY created_at DESC LIMIT ?",
            (like, like, like, like, limit),
        ))
        thoughts = _rows(c.execute(
            "SELECT * FROM thoughts WHERE text LIKE ? ORDER BY created_at DESC LIMIT ?", (like, limit)
        ))
        notes = _rows(c.execute("SELECT * FROM notes WHERE text LIKE ? ORDER BY created_at DESC LIMIT ?", (like, limit)))
    for p in papers:
        card = p.pop("card")
        p["tldr"] = card and card.get("tldr")
    return {"papers": papers, "captures": captures, "thoughts": thoughts, "notes": notes}
