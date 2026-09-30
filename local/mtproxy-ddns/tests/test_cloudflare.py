import io
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import cloudflare as cf  # noqa: E402


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_api(monkeypatch):
    """Route _call through an in-memory record store instead of HTTP."""
    store = {}   # fqdn -> {"id":..., "content":...}
    calls = []

    def _call(token, method, path, body=None, timeout=10.0):
        calls.append((method, path, body))
        p = path.split("?")[0]
        if method == "GET" and "name=" in path:
            name = path.split("name=")[1].split("&")[0].replace("%2E", ".")
            rec = store.get(name)
            return [rec] if rec else []
        if method == "POST" and p.endswith("/dns_records"):
            rid = f"id-{body['name']}"
            store[body["name"]] = {"id": rid, "content": body["content"],
                                   "name": body["name"]}
            return store[body["name"]]
        if method == "PUT" and "/dns_records/" in p:
            rid = p.rsplit("/", 1)[1]
            store[body["name"]] = {"id": rid, "content": body["content"],
                                   "name": body["name"]}
            return store[body["name"]]
        raise AssertionError(f"unexpected {method} {path}")

    monkeypatch.setattr(cf, "_call", _call)
    return store, calls


def test_set_ip_creates_then_updates_and_caches_ids(monkeypatch):
    store, calls = fake_api(monkeypatch)
    z = cf.CloudflareZone("tok", "zone1", "aikort.lol")

    z.set_ip(["mt", "proxy"], "1.2.3.4", ttl=30)  # ttl below CF min -> clamped
    assert store["mt.aikort.lol"]["content"] == "1.2.3.4"
    assert store["proxy.aikort.lol"]["content"] == "1.2.3.4"
    assert all(c[2]["ttl"] == 60 for c in calls if c[0] in ("POST", "PUT"))
    assert all(c[2]["proxied"] is False for c in calls if c[0] in ("POST", "PUT"))

    calls.clear()
    z.set_ip(["mt", "proxy"], "5.6.7.8", ttl=60)
    # ids are cached now: straight PUTs, no GET lookups
    assert [c[0] for c in calls] == ["PUT", "PUT"]
    assert store["mt.aikort.lol"]["content"] == "5.6.7.8"


def test_all_match_short_circuits_after_set(monkeypatch):
    store, calls = fake_api(monkeypatch)
    z = cf.CloudflareZone("tok", "zone1", "aikort.lol")
    z.set_ip(["mt", "proxy"], "1.2.3.4", ttl=60)

    calls.clear()
    assert z.all_match(["mt", "proxy"], "1.2.3.4") is True
    assert calls == []  # no API calls — served from last-set cache

    assert z.all_match(["mt", "proxy"], "9.9.9.9") is False
    assert calls  # had to actually look


def test_get_ip_none_when_absent(monkeypatch):
    fake_api(monkeypatch)
    z = cf.CloudflareZone("tok", "zone1", "aikort.lol")
    assert z.get_ip("mt") is None
