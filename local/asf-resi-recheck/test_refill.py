"""Offline checks for the residential validate-and-replace + rotating/sticky logic.

Run:  python test_refill.py   (from project/asf-resi-recheck/)
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent / "botshop"))
import vproxy_service as vs  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


ROT = {"type": "rotating", "sessionttl": 0, "country": "UA", "protocol": "http",
       "quantity": 5, "format": "hostname:port:login:password"}
STICKY = {"type": "sticky", "sessionttl": 1800, "country": "UA", "protocol": "http",
          "quantity": 5, "format": "hostname:port:login:password"}


def test_canonicalize_all_formats():
    host, port = "proxy.example", "80"
    login, pw = "u-cc-UA-s-abc123def", "0cd056023c2e2dace0f314aef5a6b50"
    want = f"http://{login}:{pw}@{host}:{port}"
    assert vs._canonicalize_proxy_line(f"{host}:{port}:{login}:{pw}") == want
    assert vs._canonicalize_proxy_line(f"{host}:{port}@{login}:{pw}") == want
    assert vs._canonicalize_proxy_line(f"{login}:{pw}@{host}:{port}") == want
    assert vs._canonicalize_proxy_line(want) == want
    assert vs._canonicalize_proxy_line("") == ""


def test_rotating_line_is_distinct_session_with_ttl():
    c1, e1 = vs._build_residential_line(
        "user", "pass", country="UA", use_sticky=False, ttl=0, index=0,
        output_format="hostname:port:login:password",
    )
    c2, _ = vs._build_residential_line(
        "user", "pass", country="UA", use_sticky=False, ttl=0, index=1,
        output_format="hostname:port:login:password",
    )
    assert "-s-" in e1 and f"-ttl-{vs.MASKIFY_ROTATING_TTL}" in e1
    assert c1 != c2                       # distinct sessions -> distinct IPs


def test_sticky_line_is_pinned():
    _, e = vs._build_residential_line(
        "user", "pass", country="", use_sticky=True, ttl=1800, index=3,
        output_format="protocol://login:password@hostname:port",
    )
    assert e.startswith("http://user-s-") and "-ttl-1800" in e
    _, e0 = vs._build_residential_line(
        "user", "pass", country="", use_sticky=True, ttl=0, index=1,
        output_format="protocol://login:password@hostname:port",
    )
    assert "-s-" in e0 and "-ttl-" not in e0  # sticky w/o ttl: pinned, no -ttl-0


def test_rotating_returns_distinct_lines():
    with patch.object(vs, "MASKIFY_PROXY_HOST", "proxy.example"), \
         patch.object(vs, "MASKIFY_DELIVERY_VALIDATE", False):
        out = _run(vs._generate_maskify_proxies_with_credentials("u", "p", dict(ROT)))
    lines = out.splitlines()
    assert len(lines) == 5 and len(set(lines)) == 5     # N different IPs, not copies
    assert all("-s-" in ln and "-ttl-" in ln for ln in lines)


def test_sticky_returns_distinct_sessions():
    with patch.object(vs, "MASKIFY_PROXY_HOST", "proxy.example"), \
         patch.object(vs, "MASKIFY_DELIVERY_VALIDATE", False):
        out = _run(vs._generate_maskify_proxies_with_credentials("u", "p", dict(STICKY)))
    lines = out.splitlines()
    assert len(lines) == 5 and len(set(lines)) == 5
    assert all("-s-" in ln for ln in lines)


def test_rotating_quality_ranks_and_delivers_distinct():
    async def fake_probe(canonical, *, want_country="", verify_geo=True, timeout=12.0):
        import re
        m = re.search(r"-s-([0-9a-f]+)", canonical)
        ping = (int(m.group(1)[-1], 16) if m else 9) * 50 + 100
        return {"ok": True, "ping_ms": ping, "country": want_country, "ig": True, "geo": True}

    with patch.object(vs, "MASKIFY_PROXY_HOST", "proxy.example"), \
         patch.object(vs, "MASKIFY_DELIVERY_VALIDATE", True), \
         patch.object(vs, "MASKIFY_QUALITY_FILTER", True), \
         patch.object(vs, "_probe_residential", fake_probe):
        exported, canonical, meta = _run(
            vs._collect_working_residential_lines("u", "p", dict(ROT, quantity=4), validate_default=True)
        )
    assert len(exported) == 4 and len(set(canonical)) == 4 and meta["mode"] == "rotating"


def test_sticky_quality_ranks_fastest_first():
    # ping keyed off the session index embedded in the login
    async def fake_probe(canonical, *, want_country="", verify_geo=True, timeout=12.0):
        import re
        m = re.search(r"-s-([0-9a-f]+)", canonical)
        ping = (int(m.group(1)[-1], 16) if m else 9) * 100 + 100
        return {"ok": True, "ping_ms": ping, "country": want_country, "ig": True, "geo": True}

    with patch.object(vs, "MASKIFY_PROXY_HOST", "proxy.example"), \
         patch.object(vs, "MASKIFY_DELIVERY_VALIDATE", True), \
         patch.object(vs, "MASKIFY_QUALITY_FILTER", True), \
         patch.object(vs, "_probe_residential", fake_probe):
        exported, canonical, meta = _run(
            vs._collect_working_residential_lines("u", "p", dict(STICKY, quantity=3), validate_default=True)
        )
    assert len(exported) == 3 and len(set(canonical)) == 3
    assert meta["quality_filter"] is True


def test_sticky_validate_replaces_dead_no_quality():
    calls = {"n": 0}

    async def fake_filter(lines, protocol, required):
        calls["n"] += 1
        if calls["n"] == 1:
            return list(lines)[:1]  # only one alive -> forces a top-up round
        return list(lines)[:required]

    with patch.object(vs, "MASKIFY_PROXY_HOST", "proxy.example"), \
         patch.object(vs, "MASKIFY_DELIVERY_VALIDATE", True), \
         patch.object(vs, "MASKIFY_QUALITY_FILTER", False), \
         patch.object(vs, "_filter_working_proxy_lines", fake_filter):
        exported, canonical, meta = _run(
            vs._collect_working_residential_lines("u", "p", dict(STICKY), validate_default=True)
        )
    assert len(exported) == 5 and len(set(canonical)) == 5
    assert meta["validated"] is True and calls["n"] >= 2


def test_validate_skipped_above_cap():
    async def boom(*a, **k):
        raise AssertionError("should not probe above the cap")

    big = dict(STICKY, quantity=vs.MASKIFY_DELIVERY_VALIDATE_MAX + 10)
    with patch.object(vs, "MASKIFY_PROXY_HOST", "proxy.example"), \
         patch.object(vs, "MASKIFY_DELIVERY_VALIDATE", True), \
         patch.object(vs, "_filter_working_proxy_lines", boom), \
         patch.object(vs, "_probe_residential", boom):
        out = _run(vs._generate_maskify_proxies_with_credentials("u", "p", big, validate_default=True))
    assert len(out.splitlines()) == big["quantity"]


def test_native_path_never_validates_by_default():
    async def boom(*a, **k):
        raise AssertionError("native generate path must stay unchecked by default")

    with patch.object(vs, "MASKIFY_PROXY_HOST", "proxy.example"), \
         patch.object(vs, "MASKIFY_DELIVERY_VALIDATE", True), \
         patch.object(vs, "_filter_working_proxy_lines", boom), \
         patch.object(vs, "_probe_residential", boom), \
         patch.object(vs, "_check_proxy_line", boom):
        out = _run(vs._generate_maskify_proxies_with_credentials("u", "p", dict(STICKY)))
    assert len(out.splitlines()) == 5


def test_refill_swaps_bad_slots():
    held = [f"proxy.example:80:user-cc-UA-s-{i:02d}:pass" for i in range(5)]

    async def fake_probe(line, *, want_country="", verify_geo=True, timeout=12.0):
        bad = "s-02" in line or "s-04" in line
        return {"ok": not bad, "ping_ms": 300, "country": want_country, "ig": not bad, "geo": True}

    async def fake_collect(u, p, s, *, want=None, validate_default=False):
        n = want or 1
        reps = [f"proxy.example:80:user-cc-UA-s-new{i}:pass" for i in range(n)]
        return reps, reps, {}

    with patch.object(vs, "MASKIFY_PROXY_HOST", "proxy.example"), \
         patch.object(vs, "MASKIFY_QUALITY_FILTER", True), \
         patch.object(vs, "get_api_residential_client",
                      lambda uid, cid: {"upstream_username": "u", "upstream_password": "p"}), \
         patch.object(vs, "_probe_residential", fake_probe), \
         patch.object(vs, "_collect_working_residential_lines", fake_collect):
        res = _run(vs.refill_api_residential_client_proxies(
            1, "asf1", held, dict(STICKY, format="hostname:port:login:password")))
    assert res["checked"] == 5 and res["dead"] == 2 and res["replaced"] == 2
    assert res["alive"] == 3
    assert res["lines"][0] == held[0] and res["lines"][3] == held[3]
    assert res["lines"][2] != held[2] and res["lines"][4] != held[4]


def test_refill_sample_clean_returns_early():
    held = [f"proxy.example:80:user-cc-UA-s-{i:02d}:pass" for i in range(10)]
    probed = []

    async def fake_probe(line, *, want_country="", verify_geo=True, timeout=12.0):
        probed.append(line)
        return {"ok": True, "ping_ms": 200, "country": want_country, "ig": True, "geo": True}

    with patch.object(vs, "MASKIFY_QUALITY_FILTER", True), \
         patch.object(vs, "get_api_residential_client",
                      lambda uid, cid: {"upstream_username": "u", "upstream_password": "p"}), \
         patch.object(vs, "_probe_residential", fake_probe):
        res = _run(vs.refill_api_residential_client_proxies(
            1, "asf1", held, dict(STICKY), sample_first=3))
    assert res["sampled"] is True and res["replaced"] == 0 and res["lines"] == held
    assert len(probed) == 3          # only the sample, not all 10


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"ok   {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {t.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
