#!/bin/sh
cd /opt/rassil || exit 1
pkill -f live_status_tracker.py 2>/dev/null || true
nohup ./venv/bin/python /opt/rassil/live_status_tracker.py >/opt/rassil/live_status_tracker.out 2>/opt/rassil/live_status_tracker.err </dev/null &
sleep 1
pgrep -af live_status_tracker.py
