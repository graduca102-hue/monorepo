#!/usr/bin/env bash
# Deploys perebiraika to srv2 (called locally on srv2 after files are uploaded).
# Usage: bash /opt/perebiraika/deploy.sh
set -euo pipefail

ROOT=/opt/perebiraika
cd "$ROOT"

if [ ! -f "$ROOT/.env" ]; then
    echo "creating $ROOT/.env from /opt/botshop/.env"
    key=$(grep -E '^MASKIFY_RESELLER_API_KEY=' /opt/botshop/.env | cut -d= -f2- || true)
    if [ -z "$key" ]; then
        echo "ERROR: MASKIFY_RESELLER_API_KEY missing in /opt/botshop/.env" >&2
        exit 1
    fi
    umask 077
    printf 'MASKIFY_RESELLER_API_KEY=%s\n' "$key" > "$ROOT/.env"
fi
chmod 600 "$ROOT/.env"

if [ ! -d "$ROOT/venv" ]; then
    python3 -m venv "$ROOT/venv"
fi
"$ROOT/venv/bin/pip" install --quiet --upgrade pip
"$ROOT/venv/bin/pip" install --quiet 'aiohttp>=3.9,<4'

mkdir -p "$ROOT/data"
chmod 700 "$ROOT/data"

install -m 644 "$ROOT/systemd/perebiraika.service" /etc/systemd/system/perebiraika.service
systemctl daemon-reload
systemctl enable perebiraika.service >/dev/null 2>&1 || true
systemctl restart perebiraika.service

sleep 2
systemctl --no-pager --lines=5 status perebiraika.service || true
echo
echo "deploy done. Enable the loop with: /opt/perebiraika/venv/bin/python $ROOT/perebiraika.py start"
