#!/bin/sh
# The shared server on Linux (Ubuntu 24.04), safe to re-run: venv and dependencies, headless Chromium for diagrams,
# a systemd service, and Caddy in front for HTTPS (it gets the certificate itself once DOMAIN points at this machine).
# Usage: sudo ops/server.sh p2b.example.com
set -e
DOMAIN="$1"
[ -n "$DOMAIN" ] || { echo "用法：sudo ops/server.sh <域名>（先把域名的 A 记录指向这台服务器）"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "要用 sudo 运行（安装软件包、systemd 服务和 Caddy）"; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNAS="${SUDO_USER:-root}"
ROOT="${P2B_ROOT:-/var/lib/paper2brain}"
as() { sudo -u "$RUNAS" -H "$@"; }

apt-get update -q
apt-get install -y -q python3-venv git caddy
as sh -c "cd '$REPO' && { [ -x .venv/bin/python ] || python3 -m venv .venv; } && .venv/bin/pip install -q -r requirements.txt"
"$REPO/.venv/bin/python" -m playwright install-deps chromium >/dev/null
as "$REPO/.venv/bin/python" -m playwright install chromium >/dev/null
echo "✓ Python 依赖和 Chromium"

mkdir -p "$ROOT"
chown "$RUNAS" "$ROOT"
chmod 700 "$ROOT"
sed -e "s#REPO#$REPO#g" -e "s#RUNAS#$RUNAS#g" -e "s#DATAROOT#$ROOT#g" -e "s#DOMAIN#$DOMAIN#g" "$REPO/ops/paper2brain.service" \
  > /etc/systemd/system/paper2brain.service
[ -f /etc/caddy/Caddyfile ] && ! grep -q "$DOMAIN" /etc/caddy/Caddyfile && cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.bak.$(date +%s)"
sed -e "s#DOMAIN#$DOMAIN#g" "$REPO/ops/Caddyfile" > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now paper2brain
systemctl restart paper2brain
systemctl reload caddy || systemctl restart caddy
echo "✓ 服务已启动：https://$DOMAIN（日志：journalctl -u paper2brain -f）"
echo "第一个账号的邀请链接："
as env P2B_ROOT="$ROOT" "$REPO/p2b" admin invite --url "https://$DOMAIN"
