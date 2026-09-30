"""Proxy pool management for the Rassil sender.

Responsibilities:

* keep a de-duplicated pool of proxy URLs in ``proxies.txt``
  (``http://user:pass@host:port`` or ``socks5://...``);
* health-check every proxy against Telegram and cache the verdict in
  ``proxy_health.json``;
* hand out one proxy per active account (sticky: a still-healthy assignment is
  never reshuffled), rotating extra accounts over the pool round-robin when
  there are more accounts than proxies;
* move accounts off a proxy that went dead onto a healthy one.

The module is import-safe (no side effects) and also runnable as a script::

    python proxy_manager.py check      # health-check the pool
    python proxy_manager.py assign     # (re)assign proxies to accounts in state.json
    python proxy_manager.py status     # print a summary
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

POOL_FILE = Path("proxies.txt")
HEALTH_FILE = Path("proxy_health.json")

# A proxy is considered stale (worth re-checking) after this many seconds.
HEALTH_TTL = 600
# Health check settings.
TEST_URL = "https://api.telegram.org"
TEST_TIMEOUT = 15
# The whole pool shares one upstream gateway IP, which rate-limits bursts, so
# keep concurrency low and retry once before calling a proxy dead.
CHECK_CONCURRENCY = 4
CHECK_RETRIES = 2
CHECK_RETRY_DELAY = 2.0


# ─── pool file ──────────────────────────────────────────────────────────────

def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def load_pool() -> list[str]:
    """Return the de-duplicated list of proxy URLs from ``proxies.txt``."""
    if not POOL_FILE.exists():
        return []
    return _dedup(POOL_FILE.read_text("utf-8").splitlines())


def save_pool(proxies: list[str]) -> list[str]:
    """Write ``proxies`` (de-duplicated, order preserved) to ``proxies.txt``."""
    clean = _dedup(proxies)
    tmp = POOL_FILE.with_suffix(".txt.tmp")
    tmp.write_text("\n".join(clean) + ("\n" if clean else ""), encoding="utf-8")
    tmp.replace(POOL_FILE)
    return clean


def add_to_pool(proxies: list[str]) -> list[str]:
    """Append new proxies to the pool, skipping duplicates. Returns full pool."""
    return save_pool(load_pool() + proxies)


# ─── health cache ───────────────────────────────────────────────────────────

def load_health() -> dict[str, dict]:
    if HEALTH_FILE.exists():
        try:
            data = json.loads(HEALTH_FILE.read_text("utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def _save_health(health: dict[str, dict]) -> None:
    tmp = HEALTH_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(HEALTH_FILE)


def is_alive(proxy: str, health: dict[str, dict] | None = None) -> bool:
    """True if the proxy is known-good or has never been checked (optimistic)."""
    health = health if health is not None else load_health()
    entry = health.get(proxy)
    if not entry:
        return True
    return bool(entry.get("alive"))


def alive_pool() -> list[str]:
    """Pool members that are healthy (or unchecked). Falls back to the full
    pool if that would otherwise be empty, so assignment never starves."""
    health = load_health()
    pool = load_pool()
    alive = [p for p in pool if is_alive(p, health)]
    return alive or pool


# ─── health check ──────────────────────────────────────────────────────────

async def check_one(proxy: str) -> tuple[bool, str]:
    """Probe a single proxy by fetching ``TEST_URL`` through it.

    Retries a few times because the shared gateway briefly drops bursty
    connections even when the proxy itself is fine.
    """
    try:
        import httpx
    except Exception as e:  # pragma: no cover
        return True, f"httpx unavailable ({e}) — assumed ok"
    last = "no attempt"
    for attempt in range(CHECK_RETRIES + 1):
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=TEST_TIMEOUT) as client:
                resp = await client.get(TEST_URL)
            return resp.status_code < 500, f"HTTP {resp.status_code}"
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:120]}"
            if attempt < CHECK_RETRIES:
                await asyncio.sleep(CHECK_RETRY_DELAY)
    return False, last


async def health_check(proxies: list[str] | None = None,
                       progress_cb=None) -> dict[str, dict]:
    """Check ``proxies`` (default: whole pool), update and return the cache."""
    proxies = proxies if proxies is not None else load_pool()
    health = load_health()
    sem = asyncio.Semaphore(CHECK_CONCURRENCY)

    async def run(proxy: str) -> None:
        async with sem:
            ok, detail = await check_one(proxy)
        health[proxy] = {"alive": ok, "detail": detail, "checked_at": int(time.time())}
        if progress_cb:
            mark = "🟢" if ok else "🔴"
            await progress_cb(f"{mark} {_short(proxy)} — {detail}")

    if proxies:
        await asyncio.gather(*(run(p) for p in proxies))

    pool = set(load_pool())
    for key in list(health):
        if key not in pool:
            health.pop(key)
    _save_health(health)
    return health


async def health_check_stale(progress_cb=None) -> dict[str, dict]:
    """Only (re)check pool members whose last verdict is older than HEALTH_TTL."""
    health = load_health()
    now = int(time.time())
    stale = [
        p for p in load_pool()
        if now - health.get(p, {}).get("checked_at", 0) > HEALTH_TTL
    ]
    if not stale:
        return health
    return await health_check(stale, progress_cb)


# ─── assignment ────────────────────────────────────────────────────────────

def assign(accounts, force: bool = False) -> dict:
    """Give every active account a proxy.

    * sticky — an existing assignment that is still in the healthy pool and not
      already taken by another account is kept (unless ``force``);
    * 1-to-1 while the pool lasts, then round-robin over the whole pool;
    * healthy proxies are handed out before unchecked/dead ones.

    Mutates ``account.proxy`` in place; the caller persists via ``save_state``.
    Returns a small stats dict.
    """
    pool = load_pool()
    active = [a for a in accounts if getattr(a, "active", True)]

    if not pool:
        for a in active:
            a.proxy = None
        return {"pool": 0, "assigned": 0, "kept": 0, "accounts": len(active)}

    health = load_health()
    healthy = [p for p in pool if is_alive(p, health)]
    healthy_set = set(healthy)

    used: set[str] = set()
    kept = 0
    if not force:
        for a in active:
            if a.proxy and a.proxy in healthy_set and a.proxy not in used:
                used.add(a.proxy)
                kept += 1
            else:
                a.proxy = None
    else:
        for a in active:
            a.proxy = None

    # free proxies, healthy first
    order = [p for p in healthy if p not in used] + \
            [p for p in pool if p not in healthy_set and p not in used]
    rr = healthy or pool  # round-robin source once the free list is exhausted

    ai = 0
    for a in active:
        if a.proxy:
            continue
        if ai < len(order):
            a.proxy = order[ai]
        else:
            a.proxy = rr[ai % len(rr)]
        used.add(a.proxy)
        ai += 1

    return {
        "pool": len(pool),
        "healthy": len(healthy),
        "assigned": sum(1 for a in active if a.proxy),
        "kept": kept,
        "accounts": len(active),
        "unique_in_use": len({a.proxy for a in active if a.proxy}),
    }


def reassign_dead(accounts) -> dict:
    """Point accounts that sit on a now-dead proxy at a healthy one.

    Only touches those accounts, so healthy assignments stay put. Returns stats
    including the list of affected ``item_id``s so the caller can reconnect just
    those clients.
    """
    health = load_health()
    healthy = [p for p in load_pool() if is_alive(p, health)]
    if not healthy:
        return {"moved": 0, "item_ids": [], "reason": "no healthy proxies"}

    active = [a for a in accounts if getattr(a, "active", True)]
    load = {p: 0 for p in healthy}
    for a in active:
        if a.proxy in load:
            load[a.proxy] += 1

    moved: list = []
    for a in active:
        if a.proxy and not is_alive(a.proxy, health):
            target = min(load, key=load.get)
            a.proxy = target
            load[target] += 1
            moved.append(a.item_id)

    return {"moved": len(moved), "item_ids": moved}


def prune_dead() -> dict:
    """Drop proxies with a cached ``alive: false`` verdict from ``proxies.txt``.

    Unchecked proxies are kept (optimistic, same as :func:`is_alive`). The
    caller must have run a fresh :func:`health_check` first, and must not call
    this when the check found zero live proxies (that usually means the shared
    gateway is down, not that every proxy died). Returns ``{"removed", "kept"}``.
    """
    health = load_health()
    pool = load_pool()
    kept = [p for p in pool if is_alive(p, health)]
    removed = [p for p in pool if p not in kept]
    if removed:
        save_pool(kept)
        h = load_health()
        for p in removed:
            h.pop(p, None)
        _save_health(h)
    return {"removed": len(removed), "kept": len(kept)}


# ─── reporting ─────────────────────────────────────────────────────────────

def _short(proxy: str) -> str:
    """Readable, credential-free label for a proxy URL."""
    try:
        from urllib.parse import urlparse
        p = urlparse(proxy)
        host = p.hostname or "?"
        tail = ""
        if p.username and "-s-" in p.username:
            tail = " #" + p.username.split("-s-", 1)[1].split("-", 1)[0][:6]
        return f"{p.scheme}://{host}:{p.port or ''}{tail}"
    except Exception:
        return proxy[:24] + "…"


def summary(accounts) -> str:
    pool = load_pool()
    health = load_health()
    alive = sum(1 for p in pool if health.get(p, {}).get("alive"))
    dead = sum(1 for p in pool if p in health and not health[p].get("alive"))
    unchecked = len(pool) - alive - dead

    active = [a for a in accounts if getattr(a, "active", True)]
    with_proxy = sum(1 for a in active if a.proxy)
    unique = len({a.proxy for a in active if a.proxy})
    on_dead = sum(1 for a in active if a.proxy and a.proxy in health and not health[a.proxy].get("alive"))

    last = max((e.get("checked_at", 0) for e in health.values()), default=0)
    last_str = time.strftime("%d.%m %H:%M", time.localtime(last)) if last else "никогда"

    lines = [
        f"🌐 Пул прокси: {len(pool)}",
        f"   🟢 живых: {alive}   🔴 мёртвых: {dead}   ⚪ не проверено: {unchecked}",
        f"   последняя проверка: {last_str}",
        f"👥 Аккаунтов активных: {len(active)}",
        f"   с прокси: {with_proxy}   уникальных прокси в работе: {unique}",
    ]
    if on_dead:
        lines.append(f"   ⚠️ на мёртвых прокси: {on_dead} — нажми «Переназначить битые»")
    if with_proxy < len(active):
        lines.append(f"   ⚠️ без прокси: {len(active) - with_proxy}")
    return "\n".join(lines)


# ─── CLI ───────────────────────────────────────────────────────────────────

def _cli(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "status"

    if cmd == "check":
        async def _pr(line):
            print(line)
        health = asyncio.run(health_check(progress_cb=_pr))
        alive = sum(1 for e in health.values() if e.get("alive"))
        print(f"\n{alive}/{len(health)} живых")
        return 0

    # assign / status need the account list
    from account_manager import load_accounts, save_state
    accs = load_accounts()

    if cmd == "assign":
        force = "--force" in argv
        stats = assign(accs, force=force)
        save_state(accs)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    if cmd == "reassign":
        stats = reassign_dead(accs)
        save_state(accs)
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    print(summary(accs))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_cli(sys.argv))
