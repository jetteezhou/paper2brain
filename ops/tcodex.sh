#!/bin/sh
# TCodex for the shared server, kept running on a member's Mac (installed by ops/client.sh when tcodex is here):
# takes the server's TCodex runs for this user and runs them on this Mac (paper2brain/tcodex_runner.py).
cd "$(dirname "$0")/.." || exit 1
CONFIG="${P2B_HOME:-$HOME/paper2brain-data}/config.json"
PY="$PWD/.venv/bin/python"
[ -x "$PY" ] || PY=python3
read_cfg() { "$PY" -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]) or '')" "$CONFIG" "$1" 2>/dev/null; }
while [ -z "$(read_cfg server_token)" ]; do
  echo "$(date '+%F %T') 等待 $CONFIG 里的 server / server_token（先运行 ops/client.sh）…"
  sleep 30
done
P2B_SERVER="$(read_cfg server)" P2B_TOKEN="$(read_cfg server_token)" exec "$PY" -m paper2brain.tcodex_runner
