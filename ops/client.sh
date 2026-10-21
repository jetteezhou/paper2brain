#!/bin/sh
# A member's Mac for the shared server, safe to re-run: registers this Mac's Cursor worker with the server and keeps it
# running. The library stays on the server; ./p2b here (what the agent runs) forwards to it, see paper2brain/remote.py.
# Usage: ops/client.sh https://p2b.example.com <本机 agent 口令>   (the token comes from the web page's Agent settings)
set -e
cd "$(dirname "$0")/.."
SERVER="${1%/}"
TOKEN="$2"
[ -n "$SERVER" ] && [ -n "$TOKEN" ] || { echo "用法：ops/client.sh <服务器地址> <本机 agent 口令>（口令在网页左下角的 Agent 设置里生成）"; exit 1; }
command -v python3 >/dev/null || { echo "缺 python3：xcode-select --install"; exit 1; }
DATA="${P2B_HOME:-$HOME/paper2brain-data}"
CONFIG="$DATA/config.json"
mkdir -p "$DATA"
[ -f "$CONFIG" ] || echo '{}' > "$CONFIG"
cfg() { python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]) or '')" "$CONFIG" "$1"; }

KEY="$(cfg cursor_api_key)"
if [ -z "$KEY" ]; then
  printf "Cursor API key（在 https://cursor.com/dashboard/integrations 创建）："
  stty -echo; read -r KEY; stty echo; echo
fi
MACHINE="$(cfg machine)"
[ -n "$MACHINE" ] || MACHINE="$(printf 'p2b-%s-%s' "$(whoami)" "$(hostname -s)" | tr -c 'A-Za-z0-9.\n-' '-')"

# secrets go through the environment, not the command line other users can see in ps
P2B_KEY="$KEY" P2B_TOKEN="$TOKEN" python3 - "$CONFIG" "$SERVER" "$MACHINE" <<'EOF'
import json, os, sys, urllib.error, urllib.request
path, server, machine = sys.argv[1:]
cfg = json.load(open(path))
cfg.update(cursor_api_key=os.environ["P2B_KEY"], machine=machine, server=server, server_token=os.environ["P2B_TOKEN"])
req = urllib.request.Request(f"{server}/api/agent/connect", method="POST",
                             data=json.dumps({"cursor_api_key": cfg["cursor_api_key"], "machine": machine}).encode(),
                             headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['server_token']}"})
try:
    urllib.request.urlopen(req, timeout=30).read()
except urllib.error.HTTPError as e:
    sys.exit(f"服务器拒绝了登记（{e.code}）：{e.read().decode(errors='replace')[:300]}")
except urllib.error.URLError as e:
    sys.exit(f"连不上 {server}：{e.reason}")
with open(path, "w") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
os.chmod(path, 0o600)
print(f"✓ 已在 {server} 登记本机 worker：{machine}")
EOF

PATH="$HOME/.local/bin:$PATH"
command -v agent >/dev/null || command -v cursor-agent >/dev/null || curl https://cursor.com/install -fsS | bash
sh ops/install.sh worker
echo "✓ 好了：网页上的 agent 会在这台 Mac 上运行（Mac 关机时 agent 不会回复，其他功能照常）。"
