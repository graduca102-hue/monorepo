#!/usr/bin/env python3
"""Keep mtproto.aikort.lol pointed at the MT3S Public listener's current IP.

MT3S ("MT3S Public", api.confmtbot.com) runs the actual FakeTLS MTProto proxy;
its public listener IP rotates. We own the domain and must keep an A record in
sync so the user-facing `tg://proxy?server=mtproto.aikort.lol` link keeps working.

Design (per the MT3S onboarding doc):
  * poll GET /public-listener every POLL_SECONDS with the merchant Bearer token
  * on a *changed* valid IPv4, rewrite the BIND zone file and `rndc reload`
  * on any API error / malformed response, keep the last good record untouched
  * the token lives only in /opt/mt3s-dns/.env (MT3S_API_TOKEN=...)

The zone file is fully owned by this daemon — do not hand-edit
/etc/bind/db.aikort.lol; add extra records to ZONE_EXTRA below instead.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

API_URL = os.environ.get("MT3S_API_URL", "https://api.confmtbot.com/public-listener")
TOKEN = os.environ.get("MT3S_API_TOKEN", "").strip()
ZONE = os.environ.get("MT3S_ZONE", "aikort.lol")
RECORD = os.environ.get("MT3S_RECORD", "mtproto")
ZONE_FILE = os.environ.get("MT3S_ZONE_FILE", f"/etc/bind/db.{ZONE}")
STATE_FILE = os.environ.get("MT3S_STATE_FILE", "/opt/mt3s-dns/last_ip")
POLL_SECONDS = int(os.environ.get("MT3S_POLL_SECONDS", "10"))
RECORD_TTL = int(os.environ.get("MT3S_RECORD_TTL", "60"))
NS1 = "ns1.31-77-145-160.sslip.io."
NS2 = "ns2.31-77-145-160.sslip.io."
APEX_IP = os.environ.get("MT3S_APEX_IP", "31.77.145.160")

# Extra records rendered into the zone verbatim (one RR per line, no trailing dot
# rules enforced — keep it simple).
ZONE_EXTRA: list[str] = []


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}", flush=True)


def valid_ipv4(ip: str) -> bool:
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 and str(int(p)) == p for p in parts)
    except ValueError:
        return False


def fetch_listener_ip() -> str:
    if not TOKEN:
        raise RuntimeError("MT3S_API_TOKEN is empty")
    req = urllib.request.Request(API_URL, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    ip = str(payload.get("ipv4", "")).strip()
    if not valid_ipv4(ip):
        raise ValueError(f"malformed ipv4 in response: {payload!r}")
    return ip


def last_ip() -> str | None:
    try:
        return pathlib.Path(STATE_FILE).read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None


def render_zone(ip: str, serial: int) -> str:
    lines = [
        "; managed by mt3s-dns.service — do not hand-edit",
        "$TTL 3600",
        f"@   IN  SOA {NS1} admin.{ZONE}. (",
        f"        {serial} ; Serial",
        "        3600       ; Refresh",
        "        1800       ; Retry",
        "        1209600    ; Expire",
        "        60 )       ; Negative Cache TTL",
        f"@       IN  NS      {NS1}",
        f"@       IN  NS      {NS2}",
        f"@       IN  A       {APEX_IP}",
        *ZONE_EXTRA,
        f"{RECORD}\t{RECORD_TTL}\tIN\tA\t{ip}",
        "",
    ]
    return "\n".join(lines)


def next_serial(current: str) -> int:
    today = int(time.strftime("%Y%m%d", time.gmtime())) * 100
    try:
        old = int(current.strip().split()[0])
    except (ValueError, IndexError):
        old = 0
    return max(today, old + 1)


def read_current_serial() -> str:
    try:
        for line in pathlib.Path(ZONE_FILE).read_text(encoding="utf-8").splitlines():
            if "; Serial" in line:
                return line
    except FileNotFoundError:
        pass
    return "0"


def apply_ip(ip: str) -> None:
    serial = next_serial(read_current_serial())
    new_zone = render_zone(ip, serial)
    tmp = ZONE_FILE + ".tmp"
    pathlib.Path(tmp).write_text(new_zone, encoding="utf-8")
    check = subprocess.run(
        ["named-checkzone", ZONE, tmp], capture_output=True, text=True
    )
    if check.returncode != 0:
        os.unlink(tmp)
        raise RuntimeError(f"named-checkzone rejected the new zone: {check.stdout}{check.stderr}")
    os.replace(tmp, ZONE_FILE)
    reload_res = subprocess.run(
        ["rndc", "reload", ZONE], capture_output=True, text=True
    )
    if reload_res.returncode != 0:
        raise RuntimeError(f"rndc reload failed: {reload_res.stdout}{reload_res.stderr}")
    pathlib.Path(STATE_FILE).write_text(ip, encoding="utf-8")
    log(f"updated {RECORD}.{ZONE} A -> {ip} (serial {serial})")


def main() -> int:
    log(f"mt3s-dns starting: {API_URL} -> {RECORD}.{ZONE} every {POLL_SECONDS}s")
    consecutive_errors = 0
    while True:
        try:
            ip = fetch_listener_ip()
            if ip != last_ip():
                apply_ip(ip)
            consecutive_errors = 0
        except (urllib.error.URLError, TimeoutError, ValueError, RuntimeError, OSError) as exc:
            consecutive_errors += 1
            # keep the last good record; only shout every ~5 min of failure
            if consecutive_errors == 1 or consecutive_errors % 30 == 0:
                log(f"skip (keeping last good {last_ip()}): {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
