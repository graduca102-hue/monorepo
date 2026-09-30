"""Minimal Cloudflare DNS API client (stdlib only).

Manages the flat set of grey-cloud (``proxied: false``) A records that the
updater keeps pointed at the live MTProxy IP. Record ids are cached so a normal
rotation is one PUT per name; ``all_match`` short-circuits on the last IP we
wrote so the steady-state costs no API calls beyond the single read the updater
does to anchor ``have``.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("mtproxy-ddns")

API = "https://api.cloudflare.com/client/v4"
CF_MIN_TTL = 60  # Cloudflare rejects TTL < 60 on non-enterprise zones


class CloudflareError(RuntimeError):
    """Any non-success answer from the Cloudflare API."""


def _call(token: str, method: str, path: str, body=None, timeout: float = 10.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read(1 << 20))
    except urllib.error.HTTPError as e:
        detail = e.read(4096).decode("utf-8", "replace")
        raise CloudflareError(f"{method} {path} -> HTTP {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise CloudflareError(f"{method} {path} failed: {e}") from e
    if not payload.get("success"):
        raise CloudflareError(f"{method} {path} -> {payload.get('errors')}")
    return payload.get("result")


def find_zone_id(token: str, zone_name: str, timeout: float = 10.0) -> str:
    zone_name = zone_name.strip().rstrip(".")
    q = urllib.parse.urlencode({"name": zone_name})
    result = _call(token, "GET", f"/zones?{q}", timeout=timeout) or []
    for z in result:
        if z.get("name") == zone_name:
            return z["id"]
    raise CloudflareError(f"zone {zone_name!r} not found on this Cloudflare account")


class CloudflareZone:
    def __init__(self, token: str, zone_id: str, zone_name: str, timeout: float = 10.0):
        self._token = token
        self._zone_id = zone_id
        self._zone = zone_name.strip().rstrip(".")
        self._timeout = timeout
        self._ids: dict[str, str] = {}   # fqdn -> record id
        self._last_set_ip: str | None = None
        self._last_set_names: frozenset[str] = frozenset()

    def _req(self, method: str, path: str, body=None):
        return _call(self._token, method, path, body, self._timeout)

    def _fqdn(self, name: str) -> str:
        name = name.strip().rstrip(".")
        if name in ("@", "", self._zone):
            return self._zone
        return f"{name}.{self._zone}"

    def _lookup(self, fqdn: str):
        q = urllib.parse.urlencode({"type": "A", "name": fqdn})
        recs = self._req("GET", f"/zones/{self._zone_id}/dns_records?{q}") or []
        if recs:
            self._ids[fqdn] = recs[0]["id"]
            return recs[0]
        return None

    def get_ip(self, name: str) -> str | None:
        rec = self._lookup(self._fqdn(name))
        return rec["content"] if rec else None

    def all_match(self, names, ip: str) -> bool:
        want = frozenset(self._fqdn(n) for n in names)
        if ip == self._last_set_ip and want <= self._last_set_names:
            return True
        return all(self.get_ip(n) == ip for n in names)

    def set_ip(self, names, ip: str, ttl: int) -> None:
        ttl = max(int(ttl), CF_MIN_TTL)
        for name in names:
            fqdn = self._fqdn(name)
            body = {"type": "A", "name": fqdn, "content": ip,
                    "ttl": ttl, "proxied": False}
            rid = self._ids.get(fqdn)
            if rid:
                try:
                    self._req(
                        "PUT", f"/zones/{self._zone_id}/dns_records/{rid}", body)
                    continue
                except CloudflareError:
                    self._ids.pop(fqdn, None)  # stale id — re-resolve below
            if self._lookup(fqdn):
                self._req(
                    "PUT",
                    f"/zones/{self._zone_id}/dns_records/{self._ids[fqdn]}", body)
            else:
                created = self._req(
                    "POST", f"/zones/{self._zone_id}/dns_records", body)
                self._ids[fqdn] = created["id"]
        self._last_set_ip = ip
        self._last_set_names = frozenset(self._fqdn(n) for n in names)
