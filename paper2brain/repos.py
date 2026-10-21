"""Read-only git checkouts of code linked to a research: clone, pull, read files at a commit. Never commits or pushes."""
import re
import subprocess
from pathlib import Path

from . import rules, sources, store


def normalize(url):
    """https://github.com/a/b(.git|/) and git@github.com:a/b.git name the same repo; local paths stay paths."""
    url = url.strip().rstrip("/")
    m = re.match(r"^git@([^:]+):(.+?)(?:\.git)?$", url)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    m = re.match(r"^https?://([^/]+)/(.+?)(?:\.git)?(?:/tree/.*)?$", url)
    if m:
        return f"https://{m.group(1)}/{m.group(2)}"
    if url.startswith(("/", "~")):
        return str(Path(url).expanduser())
    raise rules.Rejected(f"认不出代码仓库地址：{url}（例：https://github.com/Physical-Intelligence/openpi）")


def local_dir(home, url):
    if url.startswith("/"):
        if store.multi():
            raise rules.Rejected("多用户服务器上只能关联 https 的代码仓库")  # a server path would read other libraries
        return Path(url)
    host, path = re.match(r"https://([^/]+)/(.+)", url).groups()
    out = home / "repos" / host / path
    if store.multi():
        sources.check_public(url)
        if (home / "repos").resolve() not in out.resolve().parents:
            raise rules.Rejected(f"认不出代码仓库地址：{url}")
    return out


def _git(path, *args, timeout=600):
    r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip()[-400:])
    return r.stdout


def clone(url, path):
    """Partial clone (blobs fetched on demand). Private repos fall back to SSH with this Mac's credentials.
    The push URL is disabled so nothing can be pushed from this checkout by accident."""
    path = Path(path)
    if url.startswith("/"):
        if not (path / ".git").exists():
            raise RuntimeError("本机路径不是 git 仓库")
        return head(path)
    if not (path / ".git").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        candidates = [url + ".git"]
        m = re.match(r"https://([^/]+)/(.+)", url)
        if m:
            candidates.append(f"git@{m.group(1)}:{m.group(2)}.git")
        errors = []
        for src in candidates:
            r = subprocess.run(["git", "clone", "--filter=blob:none", "--quiet", src, str(path)],
                               capture_output=True, text=True, timeout=1800,
                               env={"GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes", **_env()})
            if r.returncode == 0:
                break
            errors.append(r.stderr.strip()[-200:])
        else:
            raise RuntimeError("克隆失败：" + " | ".join(errors))
    _git(path, "remote", "set-url", "--push", "origin", "DISABLED-read-only")
    return head(path)


def _env():
    import os
    return {k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "SSH_AUTH_SOCK", "USER", "LANG")}


def pull(path):
    if (Path(path) / ".git").exists():
        _git(path, "pull", "--ff-only", "--quiet")
    return head(path)


def head(path):
    return _git(path, "rev-parse", "HEAD").strip()


def log(path, n=10):
    out = _git(path, "log", f"-{n}", "--date=iso-strict", "--pretty=format:%H%x1f%ad%x1f%an%x1f%s")
    return [dict(zip(("sha", "date", "author", "subject"), line.split("\x1f"))) for line in out.splitlines() if line]


def reader(path, commit):
    """file -> text at commit, or None when the file does not exist there."""
    def read(file):
        try:
            return _git(path, "show", f"{commit}:{file.removeprefix('./')}", timeout=120)
        except RuntimeError:
            return None
    return read


def web_url(url, commit, file, line=None):
    if not url.startswith("https://github.com/"):
        return None
    return f"{url}/blob/{commit}/{file.removeprefix('./')}" + (f"#L{line}" if line else "")
