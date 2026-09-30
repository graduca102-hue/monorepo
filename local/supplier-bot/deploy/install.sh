#!/bin/bash
# Install / update supplier-bot on srv2 from /tmp/supplier-bot.tgz (+ /tmp/supplier-bot.env on first run).
set -eu
APP=/opt/supplier-bot

mkdir -p "$APP/data"
if [ -d "$APP/app" ]; then
    TS=$(date +%Y%m%d_%H%M%S)
    mkdir -p "$APP/backups/$TS"
    cp -a "$APP/app" "$APP/requirements.txt" "$APP/backups/$TS/" 2>/dev/null || true
    [ -f "$APP/data/supplier.db" ] && cp -a "$APP/data/supplier.db" "$APP/backups/$TS/"
fi
tar -xzf /tmp/supplier-bot.tgz -C "$APP"
rm -f /tmp/supplier-bot.tgz

if [ ! -f "$APP/.env" ]; then
    install -m 600 /tmp/supplier-bot.env "$APP/.env"
    # Shop bot token for the admin fallback notification (never printed).
    grep '^TOKEN=' /opt/botshop/.env | sed 's/^TOKEN=/NOTIFY_BOT_TOKEN=/' >> "$APP/.env"
fi
rm -f /tmp/supplier-bot.env

[ -x "$APP/.venv/bin/python" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements.txt"

install -m 644 "$APP/deploy/supplier-bot.service" /etc/systemd/system/supplier-bot.service
systemctl daemon-reload
systemctl enable supplier-bot >/dev/null 2>&1
SINCE=$(date '+%Y-%m-%d %H:%M:%S')
systemctl restart supplier-bot
sleep 10
echo "active=$(systemctl is-active supplier-bot)"
journalctl -u supplier-bot --since "$SINCE" --no-pager | grep -E "started as|Traceback|Error" | tail -5
