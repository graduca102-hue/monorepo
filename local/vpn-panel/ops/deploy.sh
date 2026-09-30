#!/usr/bin/env bash
# Deploy vpn-panel to a fresh Debian/Ubuntu VPS as a systemd service.
# Usage: ./ops/deploy.sh user@host   (run from the project root, .env must exist)
set -euo pipefail

TARGET="${1:?usage: deploy.sh user@host}"
REMOTE_DIR="/opt/vpn-panel"

echo ">> syncing source to $TARGET:$REMOTE_DIR"
rsync -az --delete \
  --exclude '.venv' --exclude 'data' --exclude '__pycache__' --exclude '.git' \
  ./ "$TARGET:$REMOTE_DIR/"
scp .env "$TARGET:$REMOTE_DIR/.env"

ssh "$TARGET" bash -euo pipefail <<'REMOTE'
cd /opt/vpn-panel
apt-get update -qq && apt-get install -y -qq python3 python3-venv rsync
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip -q install --upgrade pip
.venv/bin/pip -q install -r requirements.txt
install -m 644 ops/vpn-panel.service /etc/systemd/system/vpn-panel.service
chmod 700 /opt/vpn-panel; mkdir -p data; chmod 700 data
systemctl daemon-reload
systemctl enable --now vpn-panel
sleep 2
systemctl --no-pager --lines=20 status vpn-panel || true
REMOTE

echo ">> done. Point SUB_BASE_URL's domain at this host:WEB_PORT (behind Cloudflare)."
