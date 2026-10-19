"""The Overleaf project of a research, as a local git checkout through Overleaf's Git integration (or the GitHub
repository the project syncs with). The agent edits files here; nothing leaves until the user pushes from the
research page."""
import base64
import json
import os
import re
import subprocess

from . import rules, store

MAX_DIFF = 200_000
WHO = ("-c", "user.name=paper2brain", "-c", "user.email=paper2brain@localhost")


def project_id(text):
    """An Overleaf project id, or github:<owner>/<repo> for the GitHub repository an Overleaf project syncs with."""
    text = (text or "").strip()
    m = re.fullmatch(r"(?:https?://(?:www\.)?overleaf\.com/project/|https?://git\.overleaf\.com/)?([0-9a-f]{24})/?", text)
    if m:
        return m.group(1)
    m = re.fullmatch(r"(?:https?://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?/?", text)
    if m:
        return "github:" + m.group(1)
    raise rules.Rejected("认不出项目：粘贴 Overleaf 项目链接（https://www.overleaf.com/project/<24 位 id>），"
                         "或它同步的 GitHub 仓库（https://github.com/<用户>/<仓库>）")


def is_github(pid):
    return pid.startswith("github:")


def local_dir(pid):
    return store.home() / "overleaf" / pid.replace(":", "/")


def _token():
    path = store.home() / "config.json"
    return os.environ.get("OVERLEAF_TOKEN") or (json.loads(path.read_text()) if path.exists() else {}).get("overleaf_token")


def _git(path, *args, remote=None):
    """remote: the project id when the command talks to its remote; Overleaf gets the token, GitHub this Mac's SSH."""
    tok = _token() if remote and not is_github(remote) else None
    # passed per command so the token never lands in .git/config
    auth = ["-c", "http.extraHeader=Authorization: Basic " + base64.b64encode(f"git:{tok}".encode()).decode()] if tok else []
    r = subprocess.run(["git", *auth, "-C", str(path), *args], capture_output=True, text=True, timeout=300,
                       env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes"})
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:])
    return r.stdout


def clone(pid, remote=None):
    path = local_dir(pid)
    if (path / ".git").exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    default = f"git@github.com:{pid[7:]}.git" if is_github(pid) else f"https://git.overleaf.com/{pid}"
    try:
        _git(path.parent, "clone", "--quiet", remote or default, str(path), remote=pid)
    except RuntimeError as e:
        need = "这台 Mac 的 SSH 要能访问这个 GitHub 仓库" if is_github(pid) else "需要 Overleaf 的 Git 集成，并在 config.json 里填 overleaf_token"
        raise rules.Rejected(f"克隆失败（{need}）：{e}") from None
    return path


def changed(pid):
    """How many files differ from what Overleaf has: the agent's (or the user's) edits not pushed yet."""
    path = local_dir(pid)
    return len(_git(path, "status", "--porcelain").splitlines()) if (path / ".git").exists() else 0


def changes(pid):
    path = local_dir(pid)
    _git(path, "add", "-A")
    files = [line.split("\t", 1) for line in _git(path, "diff", "--cached", "--name-status").splitlines()]
    return {"path": str(path), "files": [{"status": s, "file": f} for s, f in files],
            "diff": _git(path, "diff", "--cached", "--no-color")[:MAX_DIFF]}


def pull(pid):
    if changed(pid):
        raise rules.Rejected("还有没推送的改动：先推送或放弃，再拉取")
    _git(local_dir(pid), "pull", "--ff-only", "--quiet", remote=pid)


def push(pid, message):
    path = local_dir(pid)
    _git(path, "add", "-A")
    if not _git(path, "diff", "--cached", "--name-only").strip():
        raise rules.Rejected("没有要推送的改动")
    _git(path, *WHO, "commit", "--quiet", "-m", message or "paper2brain")
    try:
        _git(path, *WHO, "pull", "--rebase", "--quiet", remote=pid)
        _git(path, "push", "--quiet", "origin", "HEAD", remote=pid)
    except RuntimeError as e:
        if (path / ".git" / "rebase-merge").exists() or (path / ".git" / "rebase-apply").exists():
            _git(path, "rebase", "--abort")
        _git(path, "reset", "--soft", "HEAD~1")  # back to uncommitted edits: nothing is lost
        raise rules.Rejected(f"推送失败，改动还留在本地（Overleaf 上可能改了同一处）：{e}") from None


def discard(pid):
    path = local_dir(pid)
    _git(path, "reset", "--hard", "--quiet")
    _git(path, "clean", "-fdq")
