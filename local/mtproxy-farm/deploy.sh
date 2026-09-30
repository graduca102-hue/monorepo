#!/usr/bin/env bash
# Run ON the target server. Files (mtprotoproxy.py, config.py, mtproxy-farm.service)
# must already be in /opt/mtproxy-farm. Usage: bash deploy.sh <PORT>
set -euo pipefail
PORT="${1:-8443}"
D=/opt/mtproxy-farm

test -f "$D/mtprotoproxy.py"; test -f "$D/config.py"; test -f "$D/mtproxy-farm.service"
python3 -c "import ast; ast.parse(open('$D/mtprotoproxy.py').read()); ast.parse(open('$D/config.py').read()); print('syntax ok')"

install -m644 "$D/mtproxy-farm.service" /etc/systemd/system/mtproxy-farm.service
sed -i "s/^Environment=MTPROXY_PORT=.*/Environment=MTPROXY_PORT=$PORT/" /etc/systemd/system/mtproxy-farm.service

systemctl daemon-reload
systemctl enable mtproxy-farm >/dev/null 2>&1 || true
systemctl restart mtproxy-farm
sleep 6
systemctl --no-pager -l status mtproxy-farm | head -10
echo "--- port check ---"
ss -ltn | grep ":$PORT " && echo "LISTENING on $PORT" || { echo "NOT LISTENING"; journalctl -u mtproxy-farm --no-pager -n 40; exit 1; }
