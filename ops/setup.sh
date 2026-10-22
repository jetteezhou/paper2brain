#!/bin/sh
# One-step setup, safe to re-run: Python venv and dependencies, data dir and config.json, an agent backend,
# then the web server as a login-time LaunchAgent (ops/install.sh).
# Usage: ops/setup.sh [tcodex|cursor]   (no argument: keep the backend already in config.json)
set -e
cd "$(dirname "$0")/.."
BACKEND="$1"
case "$BACKEND" in ""|tcodex|cursor) ;; *) echo "用法：ops/setup.sh [tcodex|cursor]"; exit 1 ;; esac

command -v python3 >/dev/null || { echo "缺 python3：brew install python@3.12"; exit 1; }
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
.venv/bin/python -m playwright install chromium >/dev/null
# PDFMathTranslate (Chinese copies of the PDFs) in its own venv: its pinned dependencies clash with ours
# (tmt 3.1 dropped the class it imports)
[ -x .venv-pdf2zh/bin/python ] || python3 -m venv .venv-pdf2zh
.venv-pdf2zh/bin/pip install -q pdf2zh 'tencentcloud-sdk-python-tmt<3.1'
echo "✓ Python 依赖已装好（.venv），含画方法图用的 Chromium 和 PDF 翻译（.venv-pdf2zh）"

DATA="${P2B_HOME:-$HOME/paper2brain-data}"
CONFIG="$DATA/config.json"
mkdir -p "$DATA"
[ -f "$CONFIG" ] || echo '{}' > "$CONFIG"
if [ -n "$BACKEND" ]; then
  .venv/bin/python -c "import json,sys; p=sys.argv[1]; c=json.load(open(p)); c['backend']=sys.argv[2]; open(p,'w').write(json.dumps(c, ensure_ascii=False, indent=2))" "$CONFIG" "$BACKEND"
fi
echo "✓ 数据目录 $DATA"

if [ "$BACKEND" = tcodex ]; then
  PATH="$HOME/.local/bin:$PATH"
  command -v tcodex >/dev/null || curl -fsSL https://mirrors.tencent.com/repository/generic/tcodex/tcodex/install.sh | sh
  echo "✓ TCodex：$(tcodex -v 2>/dev/null | head -1)。还没登录过的话运行一次：tcodex login"
elif [ "$BACKEND" = cursor ]; then
  echo "Cursor 后端：还需要 Cursor CLI、API key 和 machine，见 README「连接 Cursor agent」"
fi

case "$(uname)" in
  Darwin) sh ops/install.sh || echo "launchd 没装上，改在终端里前台运行：ops/run.sh" ;;
  *) echo "非 macOS：直接运行 ./p2b serve" ;;
esac
