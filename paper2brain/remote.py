"""./p2b on a member's Mac when the library lives on the shared server (P2B_SERVER set, as ops/worker.sh does for the
agent's shell): the command runs on the server, as this user, in the library whose agent is asking. Standard library
only, so this Mac needs none of the server's dependencies."""
import base64
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "paper2brain"


def _fail(error, code=2):
    print(json.dumps({"ok": False, "error": error}, ensure_ascii=False, indent=1))
    sys.exit(code)


def _files(argv):
    """--file=x becomes --file x; each --file / --image value goes along with its content ("-": standard input)."""
    out, files = [], {}
    for a in argv:
        flag, eq, value = a.partition("=")
        out += [flag, value] if eq and flag in ("--file", "--image") else [a]
    for i in range(1, len(out)):
        if out[i - 1] in ("--file", "--image"):
            try:
                data = sys.stdin.buffer.read() if out[i] == "-" else Path(out[i]).expanduser().read_bytes()
            except OSError as e:
                _fail(f"读不到 {out[i]}：{e.strerror}")
            files[out[i]] = base64.b64encode(data).decode()
    return out, files


def _localize(value, local):
    if isinstance(value, str):
        return local.get(value, value)
    if isinstance(value, list):
        return [_localize(v, local) for v in value]
    if isinstance(value, dict):
        return {k: _localize(v, local) for k, v in value.items()}
    return value


def _checkout(repo):
    """The server's checkout is not reachable from here: read the same commit from a clone on this Mac."""
    if not str(repo.get("url", "")).startswith("https://") or not repo.get("head"):
        return repo
    path = CACHE / "repos" / repo["id"]
    git = lambda *a: subprocess.run(["git", "-C", str(path), *a], capture_output=True, text=True)  # noqa: E731
    if not path.is_dir():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--quiet", repo["url"], str(path)], capture_output=True)
    if git("cat-file", "-e", repo["head"]).returncode:
        git("fetch", "--quiet", "origin")
    if git("checkout", "--quiet", "--detach", repo["head"]).returncode:
        return {**repo, "note": f"没能在本机检出 {repo['url']} 的 {repo['head']}，path 是服务器上的路径，读不到"}
    return {**repo, "path": str(path)}


def main(argv=None):
    server, token = os.environ.get("P2B_SERVER", "").rstrip("/"), os.environ.get("P2B_TOKEN", "")
    if not token:
        _fail("缺 P2B_TOKEN：在网页的 Agent 设置里生成本机 agent 的口令，再运行 ops/client.sh")
    argv, files = _files(sys.argv[1:] if argv is None else argv)
    body = {"argv": argv, "files": files, "agent_id": os.environ.get("CURSOR_CONVERSATION_ID"), "library": os.environ.get("P2B_LIBRARY")}
    req = urllib.request.Request(f"{server}/api/cli", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            got = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            error = json.loads(e.read()).get("error")
        except ValueError:
            error = None
        _fail(error or f"服务器返回 {e.code}", 2 if e.code == 400 else 1)
    except (urllib.error.URLError, OSError) as e:
        _fail(f"连不上 {server}：{getattr(e, 'reason', e)}", 1)
    if isinstance(got.get("out"), dict) and set(got["out"]) == {"help"}:
        print(got["out"]["help"])
        return
    local = {}
    for path, data in (got.get("files") or {}).items():
        mine = CACHE / "files" / hashlib.sha1(path.encode()).hexdigest()[:16] / Path(path).name
        mine.parent.mkdir(parents=True, exist_ok=True)
        mine.write_bytes(base64.b64decode(data))
        local[path] = str(mine)
    out = _localize(got.get("out"), local)
    if argv[:1] == ["repo"] and isinstance(out, dict):
        out = _checkout(out)
    if out is not None:
        print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
