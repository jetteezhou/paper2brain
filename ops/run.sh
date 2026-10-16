#!/bin/sh
# Run the web server and the Cursor worker from a terminal (works wherever the repo lives, including iCloud Drive).
# Both are restarted if they exit; Ctrl-C stops both.
cd "$(dirname "$0")/.." || exit 1
keep() { while true; do "$@"; echo "$(date '+%F %T') $1 退出，5 秒后重启"; sleep 5; done; }
keep ./p2b serve > /tmp/paper2brain.log 2>&1 &
keep sh ops/worker.sh > /tmp/paper2brain-worker.log 2>&1 &
trap 'kill 0' INT TERM
echo "网页 http://127.0.0.1:8765  日志：/tmp/paper2brain.log  /tmp/paper2brain-worker.log"
wait
