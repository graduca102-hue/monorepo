"""Resolve and cache Telegram group member counts.

Used to give *large* groups priority everywhere in the pipeline:
distribution across accounts, join order, and the send rotation.

"Large" = at least ``PRIORITY_THRESHOLD`` members (по умолчанию 10 000 пдп).
Counts are cached in ``group_sizes.json`` and refreshed at most once per
``CACHE_TTL`` so we never hammer Telegram just to sort a list.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path

from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.errors import FloodWaitError

log = logging.getLogger(__name__)

CACHE_FILE = Path("group_sizes.json")
CACHE_TTL = 7 * 24 * 3600          # re-check a group at most once a week
PRIORITY_THRESHOLD = 10_000        # "большие группы (от 10к пдп)"
_SAVE_MIN_INTERVAL = 10            # seconds between disk writes

_cache: dict[str, dict] = {}
_loaded = False
_last_save = 0.0
_save_lock = asyncio.Lock()

_USERNAME_RE = re.compile(r"^(?:https?://)?t\.me/(?:s/)?([A-Za-z0-9_]{3,32})/?$", re.I)


# ─── key normalisation ──────────────────────────────────────────────────────

def norm(group: str) -> str:
    """Canonical cache key for a group link / @username."""
    g = (group or "").strip()
    m = _USERNAME_RE.match(g)
    if m:
        return m.group(1).lower()
    if g.startswith("@"):
        return g[1:].lower()
    return g.lower()


def _target(group: str) -> str:
    """What we actually hand to Telethon (bare username when we can)."""
    m = _USERNAME_RE.match((group or "").strip())
    return m.group(1) if m else group


# ─── cache load / save ──────────────────────────────────────────────────────

def _load() -> None:
    global _cache, _loaded
    if _loaded:
        return
    if CACHE_FILE.exists():
        try:
            _cache = json.loads(CACHE_FILE.read_text("utf-8"))
        except Exception:
            _cache = {}
    _loaded = True


def _save(force: bool = False) -> None:
    global _last_save
    now = time.time()
    if not force and now - _last_save < _SAVE_MIN_INTERVAL:
        return
    try:
        tmp = CACHE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_cache, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CACHE_FILE)
        _last_save = now
    except Exception as e:
        log.debug(f"group_sizes save failed: {e}")


# ─── reads ──────────────────────────────────────────────────────────────────

def cached_size(group: str) -> int | None:
    """Last known member count, or ``None`` if we never resolved it."""
    _load()
    entry = _cache.get(norm(group))
    return entry.get("count") if entry else None


def is_priority(group: str) -> bool:
    """True when the group is known to have >= PRIORITY_THRESHOLD members."""
    c = cached_size(group)
    return c is not None and c >= PRIORITY_THRESHOLD


def is_stale(group: str) -> bool:
    _load()
    entry = _cache.get(norm(group))
    if not entry:
        return True
    return time.time() - entry.get("ts", 0) >= CACHE_TTL


def prioritized(groups: list[str]) -> list[str]:
    """Return *groups* ordered by member count, biggest first.

    Groups with an unknown size keep their relative order and go last, so a
    fresh cache degrades gracefully to the original ordering.
    """
    _load()
    known = [g for g in groups if cached_size(g) is not None]
    unknown = [g for g in groups if cached_size(g) is None]
    known.sort(key=lambda g: cached_size(g) or 0, reverse=True)
    return known + unknown


def priority_count(groups: list[str]) -> int:
    return sum(1 for g in groups if is_priority(g))


def split_priority(groups: list[str]) -> tuple[list[str], list[str]]:
    """(large groups, biggest first) and (the rest, original order).

    "Large" = known size >= PRIORITY_THRESHOLD. Groups with an unknown size
    count as normal, so an empty cache => everything is "normal" and the
    caller falls back to a single plain round.
    """
    _load()
    big = [g for g in groups if is_priority(g)]
    rest = [g for g in groups if not is_priority(g)]
    big.sort(key=lambda g: cached_size(g) or 0, reverse=True)
    return big, rest


# ─── writes ─────────────────────────────────────────────────────────────────

def _store(group: str, count: int) -> None:
    _cache[norm(group)] = {"count": int(count), "ts": time.time()}


async def resolve_size(client, group: str, force: bool = False) -> int | None:
    """Fetch and cache a group's member count.

    Returns the count (int) or ``None`` if it could not be resolved.
    Re-raises :class:`FloodWaitError` so the caller can back off.
    """
    _load()
    if not force and not is_stale(group):
        return cached_size(group)

    try:
        full = await client(GetFullChannelRequest(_target(group)))
        count = getattr(full.full_chat, "participants_count", None)
    except FloodWaitError:
        raise
    except Exception as e:
        log.debug(f"size resolve failed for {group}: {e}")
        return cached_size(group)

    if count is None:
        return cached_size(group)

    async with _save_lock:
        _store(group, count)
        _save()
    return int(count)


async def scan_sizes(
    clients: list,
    groups: list[str],
    progress_cb=None,
    only_stale: bool = True,
) -> dict:
    """Resolve member counts for *groups*, spreading the load over *clients*.

    Each client works through its own slice; a client that hits a FloodWait
    just stops early (the rest is picked up on the next scan). Returns a small
    summary dict.
    """
    _load()
    targets = [g for g in groups if not only_stale or is_stale(g)]
    if not targets or not clients:
        return {
            "scanned": 0,
            "total": len(groups),
            "priority": priority_count(groups),
        }

    # round-robin the targets into per-client buckets
    buckets: list[list[str]] = [[] for _ in clients]
    for i, g in enumerate(targets):
        buckets[i % len(clients)].append(g)

    done = 0
    ok = 0
    done_lock = asyncio.Lock()

    async def _worker(client, bucket: list[str]) -> None:
        nonlocal done, ok
        for g in bucket:
            try:
                size = await resolve_size(client, g, force=not only_stale)
                if size is not None:
                    ok += 1
            except FloodWaitError as e:
                log.warning(f"scan_sizes: FloodWait {e.seconds}s — worker stops early")
                return
            except Exception as e:
                log.debug(f"scan_sizes {g}: {e}")
            async with done_lock:
                done += 1
                if progress_cb and done % 50 == 0:
                    await progress_cb(f"📊 Просканировано {done}/{len(targets)}...")
            await asyncio.sleep(0.7)

    await asyncio.gather(
        *[_worker(c, b) for c, b in zip(clients, buckets) if b],
        return_exceptions=True,
    )
    _save(force=True)

    return {
        "scanned": done,
        "resolved": ok,
        "total": len(groups),
        "priority": priority_count(groups),
        "top": sorted(
            ((cached_size(g) or 0, g) for g in groups),
            reverse=True,
        )[:5],
    }
