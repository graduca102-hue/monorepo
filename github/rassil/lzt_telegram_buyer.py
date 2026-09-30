#!/usr/bin/env python3
"""Buy matching Telegram listings on LZT Market and archive completed purchases.

The script is deliberately dry-run by default. It only sends the purchase request
when ``auto_buy`` is true in the config and LZT_TOKEN is present in the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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


def load_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MarketError(f"Config file was not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise MarketError(f"Invalid JSON in {path}: {exc}") from exc

    required = ("max_price", "currency", "max_items_per_run")
    missing = [key for key in required if key not in config]
    if missing:
        raise MarketError(f"Missing required config keys: {', '.join(missing)}")
    if not isinstance(config["max_price"], (int, float)) or config["max_price"] <= 0:
        raise MarketError("max_price must be a positive number")
    if not isinstance(config["max_items_per_run"], int) or config["max_items_per_run"] < 1:
        raise MarketError("max_items_per_run must be a positive integer")
    return config


def request_json(
    method: str,
    path: str,
    token: str,
    params: dict[str, Any] | None = None,
    retries: int = 5,
) -> dict[str, Any]:
    url = f"{API_URL}{path}"
    if params:
        encoded = urlencode({key: value for key, value in params.items() if value is not None}, doseq=True)
        url = f"{url}?{encoded}"
    request = Request(url, method=method, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})

    for attempt in range(retries):
        try:
            with urlopen(request, timeout=310) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise MarketError(f"Unexpected response type from {path}")
                return payload
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in RETRYABLE_STATUSES and attempt < retries - 1:
                reset = exc.headers.get("X-RateLimit-Reset")
                delay = max(1, int(reset) - int(time.time())) if reset and reset.isdigit() else 2 ** attempt
                time.sleep(min(delay, 60))
                continue
            raise MarketError(f"{method} {path} failed with HTTP {exc.code}: {body}") from exc
        except URLError as exc:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise MarketError(f"Network error calling {path}: {exc.reason}") from exc

    raise AssertionError("unreachable")


def items_from_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # Current API responses use ``items``; accepting common alternatives keeps
    # the client usable if the market wraps a paginated result differently.
    for key in ("items", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def item_price(item: dict[str, Any]) -> float | None:
    # price is the buyer-facing field documented for category results.
    value = item.get("price")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def item_id(item: dict[str, Any]) -> int | None:
    try:
        return int(item["item_id"])
    except (KeyError, TypeError, ValueError):
        return None


def save_purchase(accounts_dir: Path, listing: dict[str, Any], purchase: dict[str, Any]) -> Path:
    item = item_id(listing)
    if item is None:
        raise MarketError("Cannot archive a purchase without item_id")
    accounts_dir.mkdir(parents=True, exist_ok=True)
    destination = accounts_dir / f"{item}.json"
    record = {
        "purchased_at": datetime.now(timezone.utc).isoformat(),
        "listing": listing,
        "purchase": purchase,
    }
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--dry-run", action="store_true", help="Search only; never submit a purchase")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        token = os.environ.get("LZT_TOKEN")
        if not token:
            raise MarketError("Set LZT_TOKEN to an access token with the market scope")

        filters = dict(config.get("filters", {}))
        filters.update({
            "pmax": config["max_price"],
            "currency": config["currency"],
            "order_by": "price_to_up",
            "page": 1,
        })
        search = request_json("GET", "/telegram", token, filters)
        listings = items_from_search(search)
        accounts_dir = Path(config.get("accounts_dir", "accounts"))
        auto_buy = bool(config.get("auto_buy", False)) and not args.dry_run
        bought = 0

        for listing in listings:
            if bought >= config["max_items_per_run"]:
                break
            identifier, price = item_id(listing), item_price(listing)
            if identifier is None or price is None or price > float(config["max_price"]):
                continue
            archived = accounts_dir / f"{identifier}.json"
            if archived.exists():
                print(f"skip {identifier}: already archived")
                continue
            if not auto_buy:
                print(f"would buy {identifier} for {price:g} {config['currency']}")
                continue

            result = request_json("POST", f"/{identifier}/fast-buy", token)
            # The Market API can ask clients to repeat an in-progress check.
            retry_count = 0
            while result.get("status") == "retry_request" and retry_count < 100:
                retry_count += 1
                time.sleep(3)
                result = request_json("POST", f"/{identifier}/fast-buy", token)
            if result.get("status") == "retry_request":
                raise MarketError(f"Purchase of {identifier} did not finish after 100 retries")

            archive = save_purchase(accounts_dir, listing, result)
            bought += 1
            print(f"bought {identifier}; archived at {archive}")

        print(f"completed: {bought} purchase(s), {len(listings)} listing(s) inspected")
        return 0
    except MarketError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
