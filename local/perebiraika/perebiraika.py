#!/usr/bin/env python3
"""Perebiraika - owner-only fast-RU residential IP sifter.

Runs an async cycle: pull a rotating session from the maskify RU gateway,
probe it (geo + short throughput sample), keep only sessions whose measured
throughput exceeds the current threshold. The warm pool is exposed via the
`get` CLI subcommand and is meant for the owner only - no customer bot ever
touches this file. Sifter subuser lives on the shop's maskify reseller
account with a small standing allocation; probe traffic is tracked and
capped per hour so the shop's GB pool is not silently drained.

Measured mbit is a *relative ranking* signal: a 256 KB probe is dominated
by TCP slow-start, so real throughput of a kept session is typically
3-5x higher than the reported number.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import sys
import time
from pathlib import Path

import aiohttp


ROOT = Path(os.getenv("PEREBIRAIKA_ROOT", "/opt/perebiraika"))
DATA = ROOT / "data"
DATA.mkdir(parents=True, exist_ok=True)

POOL_FILE = DATA / "pool.json"
STATS_FILE = DATA / "stats.json"
STATE_FILE = DATA / "state.json"
SUB_FILE = DATA / "subuser.json"
LOG_FILE = ROOT / "perebiraika.log"

MASKIFY_HOST = "proxy.sousmarketfranchize.shop"
MASKIFY_PORT = 80
MASKIFY_RESELLER_BASE = "https://maskify.su/api/reseller/v2"
COUNTRY = "RU"

DEFAULT_THRESHOLD_MBIT = 1.5
DEFAULT_POOL_SIZE = 15
STICKY_TTL = 300
PROBE_BYTES = 262144
PROBE_URL = "http://speedtest.selectel.ru/10MB"
GEO_URL = "http://ip-api.com/json/?fields=status,countryCode,query"
PROBE_TIMEOUT = 12
BUDGET_MB_PER_HOUR = 40
SUBUSER_MIN_GB_KEEP = 0.15
SUBUSER_TOPUP_GB = 1.0
SUBUSER_CHECK_INTERVAL = 90


def _load_env_file() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env_file()
RESELLER_KEY = os.getenv("MASKIFY_RESELLER_API_KEY", "").strip()


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _save(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    tmp.replace(path)


def load_pool() -> list[dict]:
    return _load(POOL_FILE, [])


def save_pool(pool: list[dict]) -> None:
    _save(POOL_FILE, pool)


def load_stats() -> dict:
    return _load(STATS_FILE, {"probes": 0, "kept": 0, "bytes": 0, "issued": 0, "history": []})


def save_stats(stats: dict) -> None:
    cutoff = time.time() - 24 * 3600
    stats["history"] = [h for h in stats.get("history", []) if h.get("t", 0) >= cutoff]
    _save(STATS_FILE, stats)


def load_state() -> dict:
    return _load(
        STATE_FILE,
        {"enabled": False, "threshold_mbit": DEFAULT_THRESHOLD_MBIT, "pool_size": DEFAULT_POOL_SIZE},
    )


def save_state(state: dict) -> None:
    _save(STATE_FILE, state)


def load_subuser() -> dict | None:
    return _load(SUB_FILE, None)


def save_subuser(sub: dict) -> None:
    _save(SUB_FILE, sub)


async def reseller(method: str, path: str, *, json_body: dict | None = None) -> dict:
    if not RESELLER_KEY:
        raise RuntimeError("MASKIFY_RESELLER_API_KEY is not set")
    headers = {"X-Reseller-API-Key": RESELLER_KEY, "Accept": "application/json"}
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(
            method,
            MASKIFY_RESELLER_BASE + path,
            headers=headers,
            json=json_body,
        ) as resp:
            body = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"maskify {method} {path}: {resp.status} {body[:240]}")
            return json.loads(body) if body else {}


async def ensure_subuser() -> dict:
    existing = load_subuser()
    if existing:
        try:
            info = await reseller("GET", f"/subusers/{existing['username']}")
            remaining = float(info.get("gb_remaining") or 0.0)
            if remaining < SUBUSER_MIN_GB_KEEP:
                await reseller(
                    "PATCH",
                    f"/subusers/{existing['username']}",
                    json_body={"add_gb": SUBUSER_TOPUP_GB},
                )
                info = await reseller("GET", f"/subusers/{existing['username']}")
                logging.info("subuser topped up +%.1f GB", SUBUSER_TOPUP_GB)
            return {**existing, **info}
        except Exception as exc:
            logging.warning("existing subuser broken (%s), creating fresh", exc)
    email = f"perebiraika-{secrets.token_hex(4)}@sous-market.com"
    payload = await reseller(
        "POST", "/subusers", json_body={"email": email, "gb": SUBUSER_TOPUP_GB}
    )
    save_subuser({"username": payload["username"], "password": payload["password"]})
    logging.info("created new subuser %s", payload["username"])
    return payload


def format_login(user: str, sid: str) -> str:
    return f"{user}-cc-{COUNTRY}-s-{sid}-ttl-{STICKY_TTL}"


def format_proxy_url(user: str, password: str, sid: str) -> str:
    return f"http://{format_login(user, sid)}:{password}@{MASKIFY_HOST}:{MASKIFY_PORT}"


def format_delivery_line(user: str, password: str, sid: str) -> str:
    return f"{MASKIFY_HOST}:{MASKIFY_PORT}:{format_login(user, sid)}:{password}"


async def probe_once(user: str, password: str, sid: str, threshold_mbit: float):
    proxy = format_proxy_url(user, password, sid)
    conn = aiohttp.TCPConnector(force_close=True)
    timeout = aiohttp.ClientTimeout(total=PROBE_TIMEOUT)
    bytes_used = 0
    async with aiohttp.ClientSession(connector=conn, timeout=timeout) as session:
        try:
            async with session.get(GEO_URL, proxy=proxy) as resp:
                geo_body = await resp.read()
                bytes_used += len(geo_body)
                geo = json.loads(geo_body.decode() or "{}")
        except Exception as exc:
            return None, bytes_used, f"geo:{exc}"
        if geo.get("status") != "success" or geo.get("countryCode") != COUNTRY:
            return None, bytes_used, f"geo-fail:{geo.get('countryCode')}"
        ip = str(geo.get("query") or "")
        try:
            headers = {"Range": f"bytes=0-{PROBE_BYTES - 1}"}
            started = time.monotonic()
            async with session.get(PROBE_URL, proxy=proxy, headers=headers) as resp:
                data = await resp.read()
            elapsed = time.monotonic() - started
            bytes_used += len(data)
        except Exception as exc:
            return None, bytes_used, f"dl:{exc}"
    if not data or elapsed <= 0:
        return None, bytes_used, "no-data"
    mbit = (len(data) * 8) / (elapsed * 1_000_000)
    if mbit < threshold_mbit:
        return None, bytes_used, f"slow:{mbit:.1f}"
    entry = {
        "sid": sid,
        "ip": ip,
        "mbit": round(mbit, 2),
        "created_at": int(time.time()),
        "expires_at": int(time.time()) + STICKY_TTL - 30,
        "issued": 0,
    }
    return entry, bytes_used, "ok"


def prune_pool(pool: list[dict]) -> list[dict]:
    now = time.time()
    return [e for e in pool if e.get("expires_at", 0) > now]


def recent_bytes(stats: dict, window_sec: int = 3600) -> int:
    cutoff = time.time() - window_sec
    return sum(int(h.get("b", 0)) for h in stats.get("history", []) if h.get("t", 0) >= cutoff)


async def cycle_loop() -> None:
    logging.info("perebiraika cycle starting")
    sub = await ensure_subuser()
    user = sub["username"]
    password = sub["password"]
    last_sub_check = 0.0

    while True:
        state = load_state()
        if not state.get("enabled"):
            await asyncio.sleep(3)
            continue

        pool = prune_pool(load_pool())
        pool_size = int(state.get("pool_size", DEFAULT_POOL_SIZE))
        threshold = float(state.get("threshold_mbit", DEFAULT_THRESHOLD_MBIT))
        stats = load_stats()

        if recent_bytes(stats) > BUDGET_MB_PER_HOUR * 1024 * 1024:
            save_pool(pool)
            logging.debug("hourly budget hit; sleeping")
            await asyncio.sleep(20)
            continue

        if len(pool) >= pool_size:
            save_pool(pool)
            await asyncio.sleep(5)
            continue

        now = time.time()
        if now - last_sub_check > SUBUSER_CHECK_INTERVAL:
            try:
                info = await reseller("GET", f"/subusers/{user}")
                if float(info.get("gb_remaining") or 0.0) < SUBUSER_MIN_GB_KEEP:
                    await reseller(
                        "PATCH", f"/subusers/{user}", json_body={"add_gb": SUBUSER_TOPUP_GB}
                    )
                    logging.info("subuser topped up +%.1f GB", SUBUSER_TOPUP_GB)
                last_sub_check = now
            except Exception as exc:
                logging.warning("subuser check failed: %s", exc)

        sid = secrets.token_hex(10)
        entry, used, why = await probe_once(user, password, sid, threshold)
        stats["probes"] = int(stats.get("probes", 0)) + 1
        stats["bytes"] = int(stats.get("bytes", 0)) + int(used)
        stats.setdefault("history", []).append(
            {"t": int(time.time()), "b": int(used), "ok": bool(entry), "mbit": entry["mbit"] if entry else 0.0}
        )
        if entry:
            pool.append(entry)
            stats["kept"] = int(stats.get("kept", 0)) + 1
            logging.info(
                "keep %s ip=%s mbit=%.1f used=%dB (pool=%d/%d)",
                sid, entry["ip"], entry["mbit"], used, len(pool), pool_size,
            )
        else:
            logging.debug("drop %s: %s (used=%dB)", sid, why, used)
        save_pool(pool)
        save_stats(stats)
        await asyncio.sleep(0.7)


def cmd_status(_args) -> None:
    state = load_state()
    stats = load_stats()
    pool = prune_pool(load_pool())
    sub = load_subuser()
    hour = time.time() - 3600
    recent = [h for h in stats.get("history", []) if h.get("t", 0) >= hour]
    kept_h = sum(1 for h in recent if h.get("ok"))
    bytes_h = sum(int(h.get("b", 0)) for h in recent)
    print(f"enabled: {state.get('enabled')}")
    print(
        f"порог: {state.get('threshold_mbit')} Мбит/с   "
        f"размер пула: {state.get('pool_size')}   "
        f"стики TTL: {STICKY_TTL}s"
    )
    print(f"в пуле живых: {len(pool)}")
    for entry in sorted(pool, key=lambda e: -float(e.get("mbit", 0)))[:12]:
        ttl_left = max(0, int(entry.get("expires_at", 0) - time.time()))
        print(
            f"  {entry.get('ip',''):<16} "
            f"{entry.get('mbit',0):>5.1f} Мбит/с  "
            f"осталось {ttl_left:>3}s  "
            f"выдач {entry.get('issued', 0)}  "
            f"sid={entry.get('sid')}"
        )
    print(
        f"всего probes={stats.get('probes')} keep={stats.get('kept')} "
        f"issued={stats.get('issued')} bytes={stats.get('bytes'):,}"
    )
    print(
        f"за последний час: probes={len(recent)} keep={kept_h} "
        f"трафик={bytes_h / 1024 / 1024:.2f} MB "
        f"(бюджет {BUDGET_MB_PER_HOUR} MB/час)"
    )
    if sub:
        print(f"maskify subuser: {sub['username']}")


def cmd_get(args) -> None:
    n = max(1, int(args.n))
    sub = load_subuser()
    if not sub:
        print("нет активного субпула — сначала запусти perebiraika start"); return
    pool = prune_pool(load_pool())
    if not pool:
        print("пул пуст — включи 'perebiraika start' и подожди пару минут"); return
    pool.sort(key=lambda e: (-float(e.get("mbit", 0)), int(e.get("issued", 0))))
    picked = pool[:n]
    print(f"# {len(picked)} быстрых RU-линий (sticky {STICKY_TTL}s) — perebiraika")
    for entry in picked:
        line = format_delivery_line(sub["username"], sub["password"], entry["sid"])
        ttl_left = max(0, int(entry.get("expires_at", 0) - time.time()))
        print(
            f"{line}   # exit={entry.get('ip')} "
            f"{entry.get('mbit', 0):.1f} Мбит/с осталось {ttl_left}s"
        )
        entry["issued"] = int(entry.get("issued", 0)) + 1
    stats = load_stats()
    stats["issued"] = int(stats.get("issued", 0)) + len(picked)
    save_stats(stats)
    save_pool(pool)


def cmd_start(_args) -> None:
    state = load_state(); state["enabled"] = True; save_state(state)
    print("перебирайка включена")


def cmd_stop(_args) -> None:
    state = load_state(); state["enabled"] = False; save_state(state)
    print("перебирайка выключена")


def cmd_threshold(args) -> None:
    state = load_state()
    state["threshold_mbit"] = float(args.mbit)
    save_state(state)
    pool = [e for e in prune_pool(load_pool()) if float(e.get("mbit", 0)) >= float(args.mbit)]
    save_pool(pool)
    print(f"порог = {args.mbit} Мбит/с (в пуле осталось {len(pool)})")


def cmd_size(args) -> None:
    state = load_state()
    state["pool_size"] = max(1, int(args.n))
    save_state(state)
    print(f"размер пула = {state['pool_size']}")


def cmd_reset(_args) -> None:
    save_pool([])
    print("пул очищен")


def cmd_daemon(_args) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
    )
    asyncio.run(cycle_loop())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="perebiraika", description="fast-RU proxy sifter")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    g = sub.add_parser("get", help="выдать N быстрых линий")
    g.add_argument("n", type=int, default=10, nargs="?")
    g.set_defaults(fn=cmd_get)
    sub.add_parser("start").set_defaults(fn=cmd_start)
    sub.add_parser("stop").set_defaults(fn=cmd_stop)
    t = sub.add_parser("threshold", help="Мбит/с — минимальный порог")
    t.add_argument("mbit", type=float)
    t.set_defaults(fn=cmd_threshold)
    sz = sub.add_parser("size", help="сколько линий держать в пуле")
    sz.add_argument("n", type=int)
    sz.set_defaults(fn=cmd_size)
    sub.add_parser("reset").set_defaults(fn=cmd_reset)
    sub.add_parser("daemon").set_defaults(fn=cmd_daemon)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
