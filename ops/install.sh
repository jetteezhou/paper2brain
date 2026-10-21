#!/bin/sh
# Install (or reinstall) the web server and the Cursor worker as login-time LaunchAgents that restart when they exit.
# Usage: ops/install.sh [server] [worker]   (default: both; ops/client.sh installs only the worker)
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
case "$REPO" in *"/Mobile Documents/"*)
  echo "仓库在 iCloud 云盘里：macOS 不允许 launchd 后台任务读取 iCloud 文件，装上也会报 Operation not permitted。"
  echo "可选：把仓库移到 iCloud 之外（例如 ~/paper2brain）后再运行本脚本；或者在终端里运行 ops/run.sh 保持前台常驻。"
  exit 1 ;;
esac
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "${P2B_HOME:-$HOME/paper2brain-data}"
CONFIG="${P2B_HOME:-$HOME/paper2brain-data}/config.json"
[ -f "$CONFIG" ] || printf '{\n  "cursor_api_key": "",\n  "machine": "p2b-mac"\n}\n' > "$CONFIG"
for name in ${*:-server worker}; do
  label="com.paper2brain.$name"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  # bootout returns before the job is gone; bootstrapping too early fails with "5: Input/output error"
  for _ in 1 2 3 4 5 6 7 8 9 10; do launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 || break; sleep 1; done
  sed -e "s#REPO#$REPO#g" -e "s#HOMEDIR#$HOME#g" "$REPO/ops/$label.plist" > "$AGENTS/$label.plist"
  launchctl bootstrap "gui/$(id -u)" "$AGENTS/$label.plist"
done
echo "已安装：${*:-server worker}（网页 http://127.0.0.1:8765，日志 /tmp/paper2brain.log；worker 日志 /tmp/paper2brain-worker.log）"
echo "worker 用 $CONFIG 里的 cursor_api_key，填上后 30 秒内自动连上。"
