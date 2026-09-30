"""Is a candidate MTProxy IP actually reachable *from Russia*?

`app/health.py` only probes from the box the daemon runs on (srv2, abroad), so it
cannot see RKN blocking that only applies to Russian networks. This module asks
check-host.net to open a TCP connection to the IP from its Russian nodes
(Moscow / St. Petersburg by default) and reports whether they got through.

Returns:
  True  — enough RU nodes connected (see ``require_all`` / ``min_ok``)
  False — RU nodes ran the check but (too many) could not connect -> looks blocked
  None  — the check itself could not be completed (API down, no nodes) -> unknown,
          caller should not act on it
"""
from __future__ import annotations

import json
import time
import urllib.request

BASE = "https://check-host.net"


def _get(path: str, timeout: float):
    req = urllib.request.Request(
        BASE + path,
        headers={"Accept": "application/json", "User-Agent": "mtproxy-ddns"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(1 << 20))


def _node_connected(entry) -> bool:
    # success: [{"address": "1.2.3.4", "time": 0.05}]
    # failure: [{"error": "Connection timed out"}] / [null] / ["..."]
    return (
        isinstance(entry, list)
        and entry
        and isinstance(entry[0], dict)
        and bool(entry[0].get("address"))
        and "error" not in entry[0]
    )


def ru_tcp_reachable(ip: str, port: int, nodes, require_all: bool = True,
                     min_ok: int = 1, poll_timeout: float = 30.0,
                     http_timeout: float = 15.0):
    """See module docstring."""
    try:
        query = "&".join(f"node={n}" for n in nodes)
        started = _get(f"/check-tcp?host={ip}:{port}&{query}", http_timeout)
        request_id = started.get("request_id")
        planned = list((started.get("nodes") or {}).keys())
        if not request_id or not planned:
            return None
    except Exception:
        return None

    results: dict = {}
    deadline = time.time() + poll_timeout
    while time.time() < deadline:
        time.sleep(3)
        try:
            results = _get(f"/check-result/{request_id}", http_timeout) or {}
        except Exception:
            continue
        if all(results.get(n) is not None for n in planned):
            break

    done = [n for n in planned if results.get(n) is not None]
    if not done:
        return None
    ok = sum(1 for n in done if _node_connected(results.get(n)))
    need = len(done) if require_all else min(max(1, min_ok), len(done))
    return ok >= need
