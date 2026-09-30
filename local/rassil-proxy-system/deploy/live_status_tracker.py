#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path("/opt/rassil")
ENV_PATH = ROOT / ".env"
STATE_PATH = ROOT / "state.json"
RUNTIME_STATE_PATH = ROOT / "runtime_state.json"
SESSIONS_DIR = ROOT / "sessions"
PID_PATH = ROOT / "live_status_tracker.pid"
CHAT_ID = 1907513941
MAX_LINES = 22
UPDATE_EVERY = 10
ACTIVITY_WINDOW = "40 min ago"

CONNECT_RE = re.compile(r"Telethon client connected: (\d+)")
TIMEOUT_RE = re.compile(r"Failed to connect (\d+): timeout")
INTERESTING = (
    "message_sender:",
    "Telethon client connected:",
    "Failed to connect",
)


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def tg_call(token: str, method: str, payload: dict) -> dict:
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sh(cmd: list[str]) -> str:
    return subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace")


def service_start() -> str:
    return sh(["systemctl", "show", "rassil-bot", "-p", "ActiveEnterTimestamp", "--value"]).strip()


def read_journal(since: str) -> str:
    return sh(["journalctl", "-u", "rassil-bot", "--since", since, "--no-pager", "-l"])


def state_counts() -> tuple[int, int]:
    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    accounts = data.get("accounts", [])
    session_ids = {
        int(p.stem) for p in SESSIONS_DIR.glob("*.session")
        if p.stem.isdigit()
    }
    eligible = [
        a for a in accounts
        if a.get("active") and a.get("joined_groups") and a.get("item_id") in session_ids
    ]
    return len(eligible), sum(len(a.get("joined_groups", [])) for a in eligible)


def sending_active() -> bool:
    if not RUNTIME_STATE_PATH.exists():
        return False
    try:
        data = json.loads(RUNTIME_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return False
    return bool(data.get("sending_active"))


def build_text(since: str) -> str:
    connect_journal = read_journal(since)
    activity_journal = read_journal(ACTIVITY_WINDOW)
    connected: set[int] = set()
    timeouted: set[int] = set()
    lines: list[str] = []
    send_lines: list[str] = []
    system_lines: list[str] = []

    for raw in connect_journal.splitlines():
        m = CONNECT_RE.search(raw)
        if m:
            connected.add(int(m.group(1)))
        m = TIMEOUT_RE.search(raw)
        if m:
            timeouted.add(int(m.group(1)))

    for raw in activity_journal.splitlines():
        if not any(marker in raw for marker in INTERESTING):
            continue
        pretty = raw
        if "message_sender:" in pretty:
            pretty = pretty.split("message_sender:", 1)[1].strip()
            send_lines.append(pretty)
        elif "__main__:" in pretty:
            pretty = pretty.split("__main__:", 1)[1].strip()
            system_lines.append(pretty)
        elif "python[" in pretty:
            pretty = pretty.split("python", 1)[-1].strip()
            system_lines.append(pretty)
        lines.append(pretty)

    eligible_accounts, total_chats = state_counts()
    waiting = max(eligible_accounts - len(connected), 0)
    active_send = sending_active()

    header = [
        "📨 Рассылка",
        "",
        (
            f"🚀 {len(connected)} аккаунтов в работе, {total_chats} чатов в очереди"
            if active_send
            else f"📱 {len(connected)} клиентов подключено, {total_chats} чатов назначено"
        ),
        f"📊 Подходят по условиям: {eligible_accounts} аккаунтов",
        f"⚠️ Не поднялись / таймаутятся: {waiting}",
    ]
    if timeouted:
        header.append(f"❌ Таймауты коннекта: {len(timeouted)}")
    if not active_send:
        header.append("⏸ Рассылка сейчас не активна")
    header.extend([
        "",
        "🔄 Live-обновление по мере работы",
        "",
    ])

    body = send_lines[-MAX_LINES:]
    if not body:
        body = system_lines[-MAX_LINES:]
    return "\n".join(header + body)


def main() -> int:
    env = load_env(ENV_PATH)
    token = env["BOT_TOKEN"]
    since = service_start()

    PID_PATH.write_text(str(os.getpid()), encoding="utf-8")

    first = tg_call(token, "sendMessage", {"chat_id": CHAT_ID, "text": build_text(since)})
    message_id = first["result"]["message_id"]
    last_text = ""

    while True:
        try:
            text = build_text(since)
            if text != last_text:
                tg_call(
                    token,
                    "editMessageText",
                    {"chat_id": CHAT_ID, "message_id": message_id, "text": text},
                )
                last_text = text
        except Exception as e:
            err = f"📨 Рассылка\n\n❌ live_status_tracker error: {e}"
            try:
                tg_call(
                    token,
                    "editMessageText",
                    {"chat_id": CHAT_ID, "message_id": message_id, "text": err},
                )
            except Exception:
                pass
        time.sleep(UPDATE_EVERY)


if __name__ == "__main__":
    sys.exit(main())
