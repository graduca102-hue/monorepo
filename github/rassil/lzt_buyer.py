"""LZT Market buyer — async wrapper for use inside the Telegram bot."""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_URL = "https://api.lzt.market"
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class MarketError(RuntimeError):
    pass


def _request_json(
    method: str,
    path: str,
    token: str,
    params: dict[str, Any] | None = None,
    retries: int = 5,
) -> dict[str, Any]:
    url = f"{API_URL}{path}"
    if params:
        encoded = urlencode(
            {k: v for k, v in params.items() if v is not None}, doseq=True
        )
        url = f"{url}?{encoded}"
    req = Request(
        url,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=310) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise MarketError(f"Unexpected response from {path}")
                return payload
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in RETRYABLE_STATUSES and attempt < retries - 1:
                reset = exc.headers.get("X-RateLimit-Reset")
                delay = (
                    max(1, int(reset) - int(time.time()))
                    if reset and reset.isdigit()
                    else 2**attempt
                )
                time.sleep(min(delay, 60))
                continue
            raise MarketError(f"HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            if attempt < retries - 1:
                time.sleep(2**attempt)
                continue
            raise MarketError(f"Network error: {exc.reason}") from exc
    raise AssertionError("unreachable")


def _items_from_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _save_purchase(
    accounts_dir: Path, listing: dict[str, Any], purchase: dict[str, Any]
) -> Path:
    item_id = int(listing["item_id"])
    accounts_dir.mkdir(parents=True, exist_ok=True)
    dest = accounts_dir / f"{item_id}.json"
    record = {
        "purchased_at": datetime.now(timezone.utc).isoformat(),
        "listing": listing,
        "purchase": purchase,
    }
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(dest)
    return dest


MAX_CONCURRENT_BUYS = 10  # parallel purchase workers


async def buy_accounts(
    count: int,
    config: dict[str, Any],
    token: str,
    progress_cb=None,
) -> list[dict[str, Any]]:
    """Buy `count` accounts from LZT Market in parallel (up to 50 concurrent).

    Paginates search results. Returns list of purchase records.
    """
    loop = asyncio.get_event_loop()
    accounts_dir = Path(config.get("accounts_dir", "accounts"))
    accounts_dir.mkdir(parents=True, exist_ok=True)

    filters = dict(config.get("filters", {}))
    filters.update(
        {
            "pmax": config["max_price"],
            "currency": config["currency"],
            "order_by": "price_to_up",
        }
    )

    # Collect enough candidates across pages
    candidates: list[dict[str, Any]] = []
    page = 1
    while len(candidates) < count and page <= 10:
        page_filters = dict(filters)
        page_filters["page"] = page

        def _do_search(pf=page_filters):
            return _request_json("GET", "/telegram", token, pf)

        search = await loop.run_in_executor(None, _do_search)
        items = _items_from_search(search)
        if not items:
            break

        for listing in items:
            if len(candidates) >= count:
                break
            try:
                item_id = int(listing["item_id"])
                price = float(listing.get("price", 0))
            except (KeyError, TypeError, ValueError):
                continue
            if price > float(config["max_price"]):
                continue
            if (accounts_dir / f"{item_id}.json").exists():
                continue
            candidates.append(listing)

        if not search.get("hasNextPage", False):
            break
        page += 1

    if not candidates:
        if progress_cb:
            await progress_cb("⚠️ Нет подходящих аккаунтов")
        return []

    if progress_cb:
        await progress_cb(f"🔍 Найдено {len(candidates)} аккаунтов, покупаю параллельно...")

    # Parallel purchase with semaphore
    sem = asyncio.Semaphore(MAX_CONCURRENT_BUYS)
    bought: list[dict[str, Any]] = []
    lock = asyncio.Lock()

    async def _buy_one(listing: dict[str, Any]):
        item_id = int(listing["item_id"])
        price = float(listing.get("price", 0))

        async with sem:
            def _do_buy():
                return _request_json("POST", f"/{item_id}/fast-buy", token)

            try:
                result = await loop.run_in_executor(None, _do_buy)
                retries = 0
                while result.get("status") == "retry_request" and retries < 30:
                    retries += 1
                    await asyncio.sleep(2)
                    result = await loop.run_in_executor(None, _do_buy)

                path = _save_purchase(accounts_dir, listing, result)
                async with lock:
                    bought.append({"item_id": item_id, "price": price, "file": str(path)})

                if progress_cb:
                    await progress_cb(f"✅ #{item_id} за {price:.0f}₽ [{len(bought)}/{len(candidates)}]")
            except MarketError as e:
                if progress_cb:
                    await progress_cb(f"❌ #{item_id}: {e}")

    await asyncio.gather(*[_buy_one(c) for c in candidates], return_exceptions=True)

    return bought


async def get_telegram_code(item_id: int, token: str) -> str | None:
    """Get Telegram login code for a purchased account via LZT API."""
    loop = asyncio.get_event_loop()

    def _do():
        return _request_json("GET", f"/{item_id}/telegram-login-code", token)

    try:
        result = await loop.run_in_executor(None, _do)
        # Response typically has {"code": "12345"} or {"item": {"code": ...}}
        code = result.get("code")
        if not code:
            item = result.get("item", {})
            code = item.get("code") or item.get("telegram_code")
        return str(code) if code else None
    except MarketError:
        return None


def get_phone_from_purchase(purchase_data: dict[str, Any]) -> str | None:
    """Extract phone number from a purchase JSON."""
    item = purchase_data.get("item", {})
    if isinstance(item, dict):
        phone = item.get("telegram_phone")
        if phone:
            return str(phone)
    return None


async def search_accounts(
    config: dict[str, Any], token: str
) -> list[dict[str, Any]]:
    """Search available accounts without buying."""
    loop = asyncio.get_event_loop()
    filters = dict(config.get("filters", {}))
    filters.update(
        {
            "pmax": config["max_price"],
            "currency": config["currency"],
            "order_by": "price_to_up",
        }
    )

    def _do():
        return _request_json("GET", "/telegram", token, filters)

    search = await loop.run_in_executor(None, _do)
    return _items_from_search(search)
