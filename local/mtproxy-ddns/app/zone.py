"""Read/rewrite a single A record and the SOA serial in a BIND zone file."""
from __future__ import annotations

import datetime as _dt
import re

MANAGED_MARKER = "; mtproxy-ddns managed record — edited automatically, do not touch"

_SOA_SERIAL_RE = re.compile(
    r"(?is)\bSOA\b.*?\(\s*(?P<serial>\d{1,10})\b"
)


def _record_re(name: str) -> re.Pattern[str]:
    esc = re.escape(name)
    # "<name> [ttl] [IN] A <ipv4>"  — leading @ or label, optional ttl/class
    return re.compile(
        rf"(?im)^(?P<pre>{esc}[^\S\n]+)(?:(?P<ttl>\d+)[^\S\n]+)?(?:IN[^\S\n]+)?A[^\S\n]+"
        rf"(?P<ip>\d{{1,3}}(?:\.\d{{1,3}}){{3}})[^\S\n]*$"
    )


def read_record_ip(zone_text: str, name: str) -> str | None:
    m = _record_re(name).search(zone_text)
    return m.group("ip") if m else None


def _bump_serial(serial: str) -> str:
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    if len(serial) == 10 and serial.startswith(today):
        n = int(serial[8:]) + 1
        if n < 100:
            return f"{today}{n:02d}"
    if len(serial) == 10 and serial[:8].isdigit() and serial > today + "00":
        # serial already ahead of today (future-dated) — just increment
        return str(int(serial) + 1)
    return f"{today}01"


def bump_serial(zone_text: str) -> tuple[str, str, str]:
    """Return (new_zone_text, old_serial, new_serial). Raises if no SOA serial."""
    m = _SOA_SERIAL_RE.search(zone_text)
    if not m:
        raise ValueError("no SOA serial found in zone file")
    old = m.group("serial")
    new = _bump_serial(old)
    s, e = m.span("serial")
    return zone_text[:s] + new + zone_text[e:], old, new


def set_record_ip(zone_text: str, name: str, ip: str, ttl: int) -> str:
    """Replace the A record for ``name`` with ``ip``/``ttl``; append it (with the
    managed marker) if the zone has no such record yet. Also bumps the SOA serial.
    """
    return set_records_ip(zone_text, [name], ip, ttl)


def set_records_ip(zone_text: str, names, ip: str, ttl: int) -> str:
    """Point every A record in ``names`` at ``ip``/``ttl`` (replacing existing
    ones, appending missing ones under the managed marker) and bump the SOA
    serial exactly once."""
    appended_marker = MANAGED_MARKER in zone_text
    for name in names:
        line = f"{name}\t{ttl}\tIN\tA\t{ip}"
        rx = _record_re(name)
        if rx.search(zone_text):
            zone_text = rx.sub(line, zone_text, count=1)
        else:
            sep = "" if zone_text.endswith("\n") else "\n"
            prefix = "" if appended_marker else MANAGED_MARKER + "\n"
            zone_text = f"{zone_text}{sep}{prefix}{line}\n"
            appended_marker = True
    zone_text, _old, _new = bump_serial(zone_text)
    return zone_text
