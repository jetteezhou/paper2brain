"""The shared server's accounts (./p2b serve with P2B_ROOT): users, their sign-in and agent tokens, libraries and who may
use which. Each library is its own data directory (store.library_home): everyone has a personal one, and a research
shared with others is copied into a space. Tokens and invite codes are stored only as their SHA-256."""
import contextvars
import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import overleaf, rules, store

ROLES = ("viewer", "editor", "owner")
INVITE_DAYS = 7
# who is asking and in which library: {"user": {...}, "library": id, "role": role}, set by the server per request
ME = contextvars.ContextVar("p2b_me", default=None)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT NOT NULL, library TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tokens (hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), kind TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS libraries (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members (library TEXT NOT NULL REFERENCES libraries(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id), role TEXT NOT NULL, PRIMARY KEY (library, user_id));
CREATE TABLE IF NOT EXISTS invites (hash TEXT PRIMARY KEY, kind TEXT NOT NULL, library TEXT, role TEXT, user_id TEXT,
  by TEXT, expires_at TEXT NOT NULL, used_at TEXT);
CREATE TABLE IF NOT EXISTS logins (name TEXT PRIMARY KEY COLLATE NOCASE, user_id TEXT NOT NULL UNIQUE REFERENCES users(id),
  salt TEXT NOT NULL, hash TEXT NOT NULL);
"""
LOCK_AFTER, LOCK_MINUTES = 5, 15
# ponytail: wrong-password counts live in memory, so a restart clears them; move them into hub.sqlite if that matters
_FAILS = {}


def root():
    return Path(os.environ["P2B_ROOT"])


@contextmanager
def _db():
    root().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root() / "hub.sqlite", timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(SCHEMA)
        with conn:
            yield conn
    finally:
        conn.close()


def _hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def _now(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat(timespec="seconds")


def _new_library(c, name, kind, owner):
    lib = uuid.uuid4().hex
    c.execute("INSERT INTO libraries VALUES (?,?,?,?)", (lib, name, kind, _now()))
    c.execute("INSERT INTO members VALUES (?,?,'owner')", (lib, owner))
    store.library_home(lib).mkdir(parents=True, exist_ok=True)
    return lib


def write_config(lib, **fields):
    path = store.library_home(lib) / "config.json"
    cfg = json.loads(path.read_text()) if path.exists() else {}
    cfg.update(fields)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    path.chmod(0o600)  # holds the member's Cursor key


# ---------- tokens and invites ----------

def issue(user_id, kind):
    """A new sign-in (web) or agent token; an agent token replaces the user's previous one."""
    token = secrets.token_urlsafe(32)
    with _db() as c:
        if kind == "agent":
            c.execute("DELETE FROM tokens WHERE user_id=? AND kind='agent'", (user_id,))
        c.execute("INSERT INTO tokens VALUES (?,?,?,?)", (_hash(token), user_id, kind, _now()))
    return token


def auth(token):
    """(user, kind) for a token, else None."""
    if not token:
        return None
    with _db() as c:
        r = c.execute("SELECT u.*, t.kind FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.hash=?", (_hash(token),)).fetchone()
    return (dict(id=r["id"], name=r["name"], library=r["library"]), r["kind"]) if r else None


def invite(kind="join", library=None, role=None, user_id=None, by=None):
    """A one-time code. join: a new account (an existing one just joins), into library as role when given;
    login: signs user_id in on another device."""
    if kind not in ("join", "login") or (library and role not in ROLES[:2]):
        raise rules.Rejected("邀请的角色是 viewer 或 editor")
    code = secrets.token_urlsafe(24)
    with _db() as c:
        c.execute("INSERT INTO invites VALUES (?,?,?,?,?,?,?,NULL)",
                  (_hash(code), kind, library, role, user_id, by, _now(days=INVITE_DAYS)))
    return code


def redeem(code, name=None, user=None, password=None):
    """Use an invite. Returns (user, web token or None when the visitor was already signed in, library to open).
    A new account signs in with its name and password from then on."""
    with _db() as c:
        inv = c.execute("SELECT * FROM invites WHERE hash=?", (_hash(code or ""),)).fetchone()
        if not inv or inv["used_at"] or inv["expires_at"] < _now():
            raise rules.Rejected("链接无效、已用过或已过期，请向邀请你的人要一个新的")
        c.execute("UPDATE invites SET used_at=? WHERE hash=?", (_now(), inv["hash"]))
        if inv["kind"] == "login":
            r = c.execute("SELECT * FROM users WHERE id=?", (inv["user_id"],)).fetchone()
            user, fresh = dict(id=r["id"], name=r["name"], library=r["library"]), True
        elif user:
            fresh = False
        else:
            name = (name or "").strip()
            if not name or len(name) > 40:
                raise rules.Rejected("写一个 40 字以内的名字，别人在共享空间里看到的就是它")
            uid = uuid.uuid4().hex
            c.execute("INSERT INTO users VALUES (?,?,?,?)", (uid, name, "", _now()))
            _set_login(c, uid, name, password)
            lib = _new_library(c, f"{name} 的论文库", "personal", uid)
            c.execute("UPDATE users SET library=? WHERE id=?", (lib, uid))
            user, fresh = dict(id=uid, name=name, library=lib), True
        if inv["kind"] == "join" and inv["library"] and c.execute("SELECT 1 FROM libraries WHERE id=?", (inv["library"],)).fetchone():
            c.execute("INSERT OR IGNORE INTO members VALUES (?,?,?)", (inv["library"], user["id"], inv["role"]))
    return user, issue(user["id"], "web") if fresh else None, (inv["library"] if inv["kind"] == "join" else None) or user["library"]


# ---------- login names and passwords ----------

def _scrypt(password, salt):
    return hashlib.scrypt((password or "").encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex()


def _set_login(c, user_id, name, password):
    name = (name or "").strip()
    if not name or len(name) > 40:
        raise rules.Rejected("登录名要 1 到 40 个字")
    if len(password or "") < 8:
        raise rules.Rejected("密码至少 8 位")
    taken = c.execute("SELECT user_id FROM logins WHERE name=?", (name,)).fetchone()
    if taken and taken["user_id"] != user_id:
        raise rules.Rejected("这个名字已经有人用了，换一个")
    salt = secrets.token_hex(16)
    c.execute("DELETE FROM logins WHERE user_id=?", (user_id,))
    c.execute("INSERT INTO logins VALUES (?,?,?,?)", (name, user_id, salt, _scrypt(password, salt)))


def login_name(user_id):
    with _db() as c:
        r = c.execute("SELECT name FROM logins WHERE user_id=?", (user_id,)).fetchone()
    return r["name"] if r else None


def set_password(user_id, name, password, old=None):
    """Sets this account's login name and password; changing them needs the current password."""
    with _db() as c:
        r = c.execute("SELECT * FROM logins WHERE user_id=?", (user_id,)).fetchone()
        if r and not hmac.compare_digest(_scrypt(old, r["salt"]), r["hash"]):
            raise rules.Rejected("当前密码不对")
        _set_login(c, user_id, name, password)


def login(name, password):
    """A new web token for the right name and password. LOCK_AFTER wrong tries lock that name for LOCK_MINUTES."""
    key = (name or "").strip().lower()
    fails, until = _FAILS.get(key, (0, None))
    if until and until > _now():
        raise rules.Rejected(f"密码错误次数太多，{LOCK_MINUTES} 分钟后再试")
    with _db() as c:
        r = c.execute("SELECT * FROM logins WHERE name=?", ((name or "").strip(),)).fetchone()
    if not (r and hmac.compare_digest(_scrypt(password, r["salt"]), r["hash"])):
        if not r:
            _scrypt(password, "00" * 16)  # an unknown name takes as long as a wrong password
        _FAILS[key] = (0, _now(minutes=LOCK_MINUTES)) if fails + 1 >= LOCK_AFTER else (fails + 1, None)
        raise rules.Rejected("登录名或密码不对")
    _FAILS.pop(key, None)
    return issue(r["user_id"], "web")


# ---------- libraries and members ----------

def libraries(user_id):
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT l.id, l.name, l.kind, m.role FROM members m JOIN libraries l ON l.id=m.library "
                                           "WHERE m.user_id=? ORDER BY l.kind='space', l.created_at", (user_id,))]


def role(user_id, library):
    with _db() as c:
        r = c.execute("SELECT role FROM members WHERE library=? AND user_id=?", (library, user_id)).fetchone()
    return r["role"] if r else None


def all_libraries():
    with _db() as c:
        return [r["id"] for r in c.execute("SELECT id FROM libraries")]


def members(library):
    with _db() as c:
        return [dict(r) for r in c.execute("SELECT u.id, u.name, m.role FROM members m JOIN users u ON u.id=m.user_id "
                                           "WHERE m.library=? ORDER BY u.created_at", (library,))]


def _space(c, library):
    r = c.execute("SELECT kind FROM libraries WHERE id=?", (library,)).fetchone()
    if not r or r["kind"] != "space":
        raise rules.Rejected("个人论文库不能加成员；先把研究共享到一个空间")


def set_role(library, user_id, new_role):
    """new_role None removes the member. A space always keeps an owner."""
    if new_role not in (None, *ROLES):
        raise rules.Rejected("角色是 viewer、editor 或 owner")
    with _db() as c:
        _space(c, library)
        owners = {r["user_id"] for r in c.execute("SELECT user_id FROM members WHERE library=? AND role='owner'", (library,))}
        if owners == {user_id} and new_role != "owner":
            raise rules.Rejected("空间至少要有一个所有者：先把别人设为所有者")
        if new_role:
            c.execute("UPDATE members SET role=? WHERE library=? AND user_id=?", (new_role, library, user_id))
        else:
            c.execute("DELETE FROM members WHERE library=? AND user_id=?", (library, user_id))
    if agent_member(library) == user_id and new_role not in ("editor", "owner"):
        set_agent(library, next(m["id"] for m in members(library) if m["role"] == "owner"))


def _backup(why, rid):
    store.backup(f"p2b-before-{why}-{rid[:8]}-{datetime.now():%Y%m%d-%H%M%S}.sqlite")


def _carry_overleaf(rid, dst):
    """A moving research takes its Overleaf checkout (unpushed edits included) and, where dst has none, the token
    along. The checkout moves unless another research here uses it too (then it is copied); one already at dst stays."""
    pid = store.get_writing(rid)["overleaf"]
    if not pid:
        return
    here = overleaf.local_dir(pid)
    with store.at_home(dst):
        there = overleaf.local_dir(pid)
    if here.is_dir() and not there.exists():
        there.parent.mkdir(parents=True, exist_ok=True)
        if set(store.overleaf_researches(pid)) - {rid}:
            shutil.copytree(here, there, symlinks=True)
        else:
            shutil.move(str(here), str(there))
    token = overleaf._token()
    if token and not store.config_of(dst.name).get("overleaf_token"):
        write_config(dst.name, overleaf_token=token)


def create_space(user, name, research_id):
    """Move a research from the library in use into a new space owned by user (from a personal library it leaves,
    after a backup; its papers and discussions stay); the space's agents run on user's key and worker."""
    name = (name or "").strip()[:60] or "共享空间"
    with _db() as c:
        lib = _new_library(c, name, "space", user["id"])
    try:
        store.copy_research(research_id, store.library_home(lib))
    except BaseException:
        with _db() as c:
            c.execute("DELETE FROM libraries WHERE id=?", (lib,))
        raise
    write_config(lib, agent_library=user["library"])
    if store.home() == store.library_home(user["library"]):
        _backup("share", research_id)
        _carry_overleaf(research_id, store.library_home(lib))
        store.delete_research(research_id)
    return lib


def move_home(user, space, research_id):
    """A space's owner takes one of its researches back into their own library (replacing an older copy there); a
    space left with no research is archived on the server (root/archive), not deleted. Runs in the space."""
    home = store.library_home(user["library"])
    with store.at_home(home):
        _backup("move-home", research_id)
        if store.get_research(research_id):
            store.delete_research(research_id)
    store.copy_research(research_id, home)
    _backup("move-home", research_id)
    _carry_overleaf(research_id, home)
    store.delete_research(research_id)
    if not store.list_research():
        with _db() as c:
            c.execute("DELETE FROM libraries WHERE id=?", (space,))
        (root() / "archive").mkdir(exist_ok=True)
        shutil.move(str(store.library_home(space)), str(root() / "archive" / space))
    return user["library"]


def agent_member(library):
    """The member whose Cursor key and worker run the space's agents."""
    lib = store.config_of(library).get("agent_library")
    with _db() as c:
        r = c.execute("SELECT id FROM users WHERE library=?", (lib,)).fetchone()
    return r and r["id"]


def set_agent(library, user_id):
    if role(user_id, library) not in ("editor", "owner"):
        raise rules.Rejected("负责 agent 的成员要能编辑这个空间")
    with _db() as c:
        _space(c, library)
        lib = c.execute("SELECT library FROM users WHERE id=?", (user_id,)).fetchone()["library"]
    write_config(library, agent_library=lib)


def agent_libraries(user):
    """Libraries whose agents run on this user's key: their own, and spaces that chose them (while they may edit)."""
    return [x["id"] for x in libraries(user["id"]) if x["id"] == user["library"]
            or (x["role"] != "viewer" and store.config_of(x["id"]).get("agent_library") == user["library"])]


def agent_library(user, agent_id=None, wanted=None):
    """Where an agent's ./p2b command goes: the library whose live run is this agent's, else the one asked for
    (if the user may edit it), else the user's own."""
    if agent_id:
        for lib in agent_libraries(user):
            with store.at_home(store.library_home(lib)):
                if store.agent_live(agent_id):
                    return lib
    if wanted:
        if role(user["id"], wanted) not in ("editor", "owner"):
            raise rules.Rejected("你不能写入这个论文库")
        return wanted
    return user["library"]
