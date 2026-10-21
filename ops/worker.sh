#!/bin/sh
# Cursor worker for this repo, kept connected: agents started through the API (env.type "machine") run their tools here.
# Waits until ~/paper2brain-data/config.json has a cursor_api_key, so it can be installed before the key exists.
cd "$(dirname "$0")/.." || exit 1
CONFIG="${P2B_HOME:-$HOME/paper2brain-data}/config.json"
PY="$PWD/.venv/bin/python"
[ -x "$PY" ] || PY=python3  # a member's Mac of the shared server has no venv
read_cfg() { "$PY" -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]) or '')" "$CONFIG" "$1" 2>/dev/null; }

agent_bin() {
  for b in agent cursor-agent "$HOME/.local/bin/agent"; do command -v "$b" >/dev/null 2>&1 && { command -v "$b"; return; }; done
  # the CLI bundled with the Cursor app, newest version first
  ls -d "$HOME/Library/Application Support/Cursor/User/globalStorage/anysphere.cursor-agent-worker/agent-cli/.local/share/cursor-agent/versions/"*/cursor-agent 2>/dev/null | sort | tail -1
}

while [ -z "$(read_cfg cursor_api_key)" ]; do
  echo "$(date '+%F %T') 等待 $CONFIG 里的 cursor_api_key …"
  sleep 30
done
BIN="$(agent_bin)"
[ -n "$BIN" ] || { echo "找不到 Cursor CLI，请先运行：curl https://cursor.com/install -fsS | bash"; sleep 300; exit 1; }
NAME="$(read_cfg machine)"
echo "$(date '+%F %T') 启动 worker：$BIN（名字 ${NAME:-p2b-mac}）"
DATA="${P2B_HOME:-$HOME/paper2brain-data}/worker"
mkdir -p "$DATA"
# clean environment: when launched from inside another Cursor agent session, inherited CURSOR_* variables would
# make this worker share that session's worker id and lock
exec env -i HOME="$HOME" USER="$USER" PATH="$PATH" LANG="${LANG:-en_US.UTF-8}" TMPDIR="${TMPDIR:-/tmp}" \
  CURSOR_API_KEY="$(read_cfg cursor_api_key)" \
  P2B_SERVER="$(read_cfg server)" P2B_TOKEN="$(read_cfg server_token)" \
  "$BIN" worker --worker-dir "$PWD" --data-dir "$DATA" --name "${NAME:-p2b-mac}" --idle-release-timeout 0 start
