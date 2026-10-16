"""SQLite storage. The only module that touches the database; every write goes through a transaction here."""
import json
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
JSON_COLS = {"links", "candidates", "authors", "card", "report", "tools", "meta", "body"}
IDEA_FIELDS = ("problem", "insight", "approach", "novelty", "experiment", "risks")
IDEA_STAGES = ("seed", "refined")
LINK_KINDS = ("paper", "thought", "message", "thread", "capture", "note")

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
  capture_id TEXT REFERENCES captures(id) ON DELETE CASCADE, paper_id TEXT REFERENCES papers(id));
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
CREATE TABLE IF NOT EXISTS ideas (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, title TEXT NOT NULL, summary TEXT,
  body TEXT, stage TEXT NOT NULL DEFAULT 'seed', by TEXT NOT NULL, edited_by TEXT, revision INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS idea_topics (
  idea_id TEXT NOT NULL REFERENCES ideas(id) ON DELETE CASCADE, topic TEXT NOT NULL REFERENCES topics(path),
  PRIMARY KEY (idea_id, topic));
CREATE TABLE IF NOT EXISTS idea_links (
  idea_id TEXT NOT NULL REFERENCES ideas(id) ON DELETE CASCADE, kind TEXT NOT NULL, ref TEXT NOT NULL,
  by TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (idea_id, kind, ref));
CREATE INDEX IF NOT EXISTS idea_links_ref ON idea_links(kind, ref);
CREATE INDEX IF NOT EXISTS captures_triage ON captures(triage, created_at);
CREATE INDEX IF NOT EXISTS cp_paper ON capture_papers(paper_id);
CREATE INDEX IF NOT EXISTS runs_ref ON runs(ref, created_at);
"""


def home():
    path = Path(os.environ.get("P2B_HOME") or Path.home() / "paper2brain-data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id():
    return str(uuid.uuid4())


def paper_dir(paper_id):
    path = home() / "papers" / paper_id.replace(":", "_").replace("/", "_")
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


@contextmanager
def tx():
    conn = _connect()
    try:
        key = str(home())
        if key not in _initialized:
            conn.executescript(SCHEMA)
            if not conn.execute("SELECT 1 FROM topics LIMIT 1").fetchone():
                for i, (parent, children) in enumerate(TOPIC_TREE):
                    conn.execute("INSERT INTO topics VALUES (?,?,?,?)", (parent, parent, None, i))
                    for j, child in enumerate(children):
                        conn.execute("INSERT INTO topics VALUES (?,?,?,?)", (f"{parent}/{child}", child, parent, j))
            conn.commit()
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
        thought_ideas = _ideas_for(c, "thought", [t["id"] for t in cap["thoughts"]])
        for t in cap["thoughts"]:
            t["ideas"] = thought_ideas.get(t["id"], [])
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
        topics = _topics_of(c, sorted({p["id"] for ps in links.values() for p in ps}))
    for x in caps:
        x["papers"] = [{**p, "topics": topics.get(p["id"], [])} for p in links.get(x["id"], [])]
    return caps


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
        out["ideas"] = c.execute("SELECT count(*) FROM ideas").fetchone()[0]
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
    with tx() as c:
        c.execute("UPDATE papers SET pdf_path=?, page_count=? WHERE id=?", (str(pdf_path), len(pages), pid))


def pages(pid):
    path = paper_dir(pid) / "pages.json"
    return json.loads(path.read_text()) if path.exists() else []


def paper_ids():
    with tx() as c:
        return {r["id"] for r in c.execute("SELECT id FROM papers")}


def get_paper(pid):
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
        p["ideas"] = _ideas_for(c, "paper", [pid]).get(pid, [])
        thought_ideas = _ideas_for(c, "thought", [t["id"] for t in p["thoughts"]])
        for t in p["thoughts"]:
            t["ideas"] = thought_ideas.get(t["id"], [])
        p["runs"] = _rows(c.execute("SELECT * FROM runs WHERE ref=? ORDER BY created_at DESC", (pid,)))
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
    for p in out:
        card = p.pop("card")
        p["tldr"] = card and card.get("tldr")
        p["topics"] = topics[p["id"]]
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

def add_thought(text, by, capture_id=None, paper_id=None):
    if not (text or "").strip():
        raise rules.Rejected("想法不能为空")
    tid = new_id()
    with tx() as c:
        c.execute(
            "INSERT INTO thoughts VALUES (?,?,?,?,?,?)", (tid, now(), text.strip(), by, capture_id, paper_id)
        )
    return tid


def list_thoughts():
    with tx() as c:
        out = _rows(c.execute(
            "SELECT t.*, coalesce(p.title, (SELECT p2.title FROM capture_papers cp JOIN papers p2 ON p2.id=cp.paper_id "
            "WHERE cp.capture_id=t.capture_id AND cp.state='confirmed' LIMIT 1)) AS paper_title, "
            "coalesce(t.paper_id, (SELECT cp.paper_id FROM capture_papers cp WHERE cp.capture_id=t.capture_id AND cp.state='confirmed' LIMIT 1)) AS about_paper, "
            "c.title AS capture_title FROM thoughts t LEFT JOIN papers p ON p.id=t.paper_id "
            "LEFT JOIN captures c ON c.id=t.capture_id ORDER BY t.created_at DESC"
        ))
        ideas = _ideas_for(c, "thought", [t["id"] for t in out])
    for t in out:
        t["ideas"] = ideas.get(t["id"], [])
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
            "FROM messages m LEFT JOIN runs r ON r.id=m.run_id WHERE m.thread_id=? ORDER BY m.created_at", (t["id"],),
        ))
        ideas = _ideas_for(c, "message", [m["id"] for m in t["messages"]])
    for m in t["messages"]:
        if m["run_tools"]:
            m["run_tools"] = json.loads(m["run_tools"])
        m["ideas"] = ideas.get(m["id"], [])
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


# ---------- ideas ----------

def _ideas_for(c, kind, refs):
    """Reverse links: which ideas cite each of these objects."""
    out = {}
    if refs:
        q = ",".join("?" * len(refs))
        for r in c.execute(
            f"SELECT l.ref, i.id, i.title, i.stage FROM idea_links l JOIN ideas i ON i.id=l.idea_id "
            f"WHERE l.kind=? AND l.ref IN ({q}) ORDER BY i.updated_at DESC", (kind, *refs),
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
    table = {"paper": "papers", "thought": "thoughts", "message": "messages", "thread": "threads",
             "capture": "captures", "note": "notes"}[kind]
    if not c.execute(f"SELECT 1 FROM {table} WHERE id=?", (ref,)).fetchone():
        raise rules.Rejected(f"要关联的 {kind} {ref} 不存在")


def add_idea(title, by, summary=None, topics=(), links=(), stage="seed"):
    """links: [(kind, ref)]. Without explicit topics the idea inherits the topics of the papers it cites."""
    if not (title or "").strip():
        raise rules.Rejected("Idea 需要一个标题")
    if stage not in IDEA_STAGES:
        raise rules.Rejected(f"未知阶段：{stage}")
    iid = new_id()
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
            "INSERT INTO ideas (id, created_at, updated_at, title, summary, stage, by) VALUES (?,?,?,?,?,?,?)",
            (iid, now(), now(), title.strip(), (summary or "").strip() or None, stage, by),
        )
        for t in dict.fromkeys(topics):
            c.execute("INSERT INTO idea_topics VALUES (?,?)", (iid, t))
        for kind, ref in dict.fromkeys(links):
            c.execute("INSERT INTO idea_links VALUES (?,?,?,?,?)", (iid, kind, ref, by, now()))
        _event(c, by, "idea.add", iid)
    return iid


def update_idea(iid, by, base_rev=None, **fields):
    """title / summary / stage / body ({problem, insight, ...}). Guarded by revision so a late agent draft cannot
    overwrite what the user edited meanwhile."""
    with tx() as c:
        cur = c.execute("SELECT revision FROM ideas WHERE id=?", (iid,)).fetchone()
        if not cur:
            raise rules.Rejected("Idea 不存在")
        rules.check_revision(cur["revision"], base_rev)
        if "stage" in fields and fields["stage"] not in IDEA_STAGES:
            raise rules.Rejected(f"未知阶段：{fields['stage']}")
        if "title" in fields and not (fields["title"] or "").strip():
            raise rules.Rejected("Idea 需要一个标题")
        if "body" in fields:
            fields["body"] = json.dumps({k: (fields["body"] or {}).get(k) for k in IDEA_FIELDS}, ensure_ascii=False)
        sets = ", ".join(f"{k}=?" for k in fields)
        c.execute(
            f"UPDATE ideas SET {sets}, updated_at=?, edited_by=?, revision=revision+1 WHERE id=?",
            (*fields.values(), now(), by, iid),
        )
        _event(c, by, "idea.update", iid, fields=list(fields))
        return cur["revision"] + 1


def set_idea_topics(iid, topics, by):
    with tx() as c:
        _check_topics(c, topics)
        c.execute("DELETE FROM idea_topics WHERE idea_id=?", (iid,))
        for t in dict.fromkeys(topics):
            c.execute("INSERT INTO idea_topics VALUES (?,?)", (iid, t))
        c.execute("UPDATE ideas SET updated_at=? WHERE id=?", (now(), iid))


def link_idea(iid, kind, ref, by):
    with tx() as c:
        if not c.execute("SELECT 1 FROM ideas WHERE id=?", (iid,)).fetchone():
            raise rules.Rejected("Idea 不存在")
        _check_link(c, kind, ref)
        c.execute("INSERT OR IGNORE INTO idea_links VALUES (?,?,?,?,?)", (iid, kind, ref, by, now()))
        c.execute("UPDATE ideas SET updated_at=? WHERE id=?", (now(), iid))


def unlink_idea(iid, kind, ref):
    with tx() as c:
        c.execute("DELETE FROM idea_links WHERE idea_id=? AND kind=? AND ref=?", (iid, kind, ref))


def delete_idea(iid):
    with tx() as c:
        c.execute("DELETE FROM ideas WHERE id=?", (iid,))
        _event(c, "user", "idea.delete", iid)


def _resolve_link(c, kind, ref):
    """What a source looks like in the idea's provenance list; None when it has been deleted since."""
    if kind == "paper":
        r = c.execute("SELECT title FROM papers WHERE id=?", (ref,)).fetchone()
        return r and {"label": r["title"]}
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
    r = c.execute("SELECT text, created_at, paper_id FROM notes WHERE id=?", (ref,)).fetchone()
    return r and {"label": r["text"][:200], "at": r["created_at"], "paper_id": r["paper_id"]}


def get_idea(iid):
    with tx() as c:
        i = _row(c.execute("SELECT * FROM ideas WHERE id=?", (iid,)).fetchone())
        if not i:
            return None
        i["topics"] = [r["topic"] for r in c.execute("SELECT topic FROM idea_topics WHERE idea_id=? ORDER BY topic", (iid,))]
        i["links"] = []
        for r in c.execute("SELECT * FROM idea_links WHERE idea_id=? ORDER BY at", (iid,)).fetchall():
            info = _resolve_link(c, r["kind"], r["ref"])
            i["links"].append({"kind": r["kind"], "ref": r["ref"], "by": r["by"], "missing": info is None, **(info or {})})
        i["runs"] = _rows(c.execute("SELECT * FROM runs WHERE ref=? ORDER BY created_at DESC", (iid,)))
    return i


def list_ideas(topic=None):
    """topic=None: all; '': ideas without a topic; a path includes its children."""
    sql = "SELECT i.id, i.title, i.summary, i.stage, i.by, i.updated_at, i.created_at, " \
          "(SELECT count(*) FROM idea_links l WHERE l.idea_id=i.id) AS link_count FROM ideas i "
    args = ()
    if topic == "":
        sql += "WHERE NOT EXISTS (SELECT 1 FROM idea_topics t WHERE t.idea_id=i.id) "
    elif topic:
        sql += "WHERE EXISTS (SELECT 1 FROM idea_topics t WHERE t.idea_id=i.id AND (t.topic=? OR t.topic LIKE ?)) "
        args = (topic, topic + "/%")
    sql += "ORDER BY i.stage='refined' DESC, i.updated_at DESC"
    with tx() as c:
        out = _rows(c.execute(sql, args))
        for i in out:
            i["topics"] = [r["topic"] for r in c.execute("SELECT topic FROM idea_topics WHERE idea_id=? ORDER BY topic", (i["id"],))]
            i["papers"] = [r["title"] for r in c.execute(
                "SELECT p.title FROM idea_links l JOIN papers p ON p.id=l.ref WHERE l.idea_id=? AND l.kind='paper'", (i["id"],)
            )]
    return out


# ---------- agent runs ----------

def add_run(run_id, agent_id, kind, ref, prompt, status):
    with tx() as c:
        c.execute(
            "INSERT OR IGNORE INTO runs (id, created_at, updated_at, agent_id, kind, ref, prompt, status) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, now(), now(), agent_id, kind, ref, prompt, status),
        )


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
