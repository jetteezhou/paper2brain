#!/bin/sh
# The shared server on Linux (Ubuntu 24.04), safe to re-run: venv and dependencies, headless Chromium for diagrams,
# a systemd service, and Caddy in front for HTTPS (it gets the certificate itself once DOMAIN points at this machine).
# Usage: sudo ops/server.sh p2b.example.com
# Slow PyPI or Playwright downloads (servers in China): set PIP_INDEX_URL / PLAYWRIGHT_DOWNLOAD_HOST to a mirror first.
set -e
DOMAIN="$1"
[ -n "$DOMAIN" ] || { echo "用法：sudo ops/server.sh <域名>（先把域名的 A 记录指向这台服务器）"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "要用 sudo 运行（安装软件包、systemd 服务和 Caddy）"; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
RUNAS="${SUDO_USER:-root}"
ROOT="${P2B_ROOT:-/var/lib/paper2brain}"
as() { sudo --preserve-env=PIP_INDEX_URL,PLAYWRIGHT_DOWNLOAD_HOST -u "$RUNAS" -H "$@"; }

# Ubuntu before 24.04 has no caddy package: the official binary from GitHub, run as its own user like the package does
caddy_binary() {
  tag="$(curl -fsS -o /dev/null -w '%{redirect_url}' https://github.com/caddyserver/caddy/releases/latest | sed 's#.*/tag/##')"
  curl -fsSL "https://github.com/caddyserver/caddy/releases/download/$tag/caddy_${tag#v}_linux_$(dpkg --print-architecture).tar.gz" \
    | tar -xz -C /usr/local/bin caddy
  id caddy >/dev/null 2>&1 || useradd --system --home /var/lib/caddy --create-home --shell /usr/sbin/nologin caddy
  mkdir -p /etc/caddy
  cat > /etc/systemd/system/caddy.service <<'EOF'
[Unit]
Description=Caddy
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
User=caddy
Group=caddy
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
PrivateTmp=true
ProtectSystem=full
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF
}

apt-get update -q
apt-get install -y -q python3-venv git
command -v caddy >/dev/null || apt-get install -y -q caddy 2>/dev/null || caddy_binary
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
systemctl enable -q caddy
systemctl reload caddy 2>/dev/null || systemctl restart caddy
echo "✓ 服务已启动：https://$DOMAIN（日志：journalctl -u paper2brain -f）"
echo "第一个账号的邀请链接："
as env P2B_ROOT="$ROOT" "$REPO/p2b" admin invite --url "https://$DOMAIN"
