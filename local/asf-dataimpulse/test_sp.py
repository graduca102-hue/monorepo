"""Read-only smoke test for the StrikeProxy integration.

Run on srv2 against the staged tree:

    /opt/asf/.venv/bin/python /opt/asf/_stage_sp/../test_sp.py

It touches the provider only through GET endpoints (profile, balance, catalog,
services, locations) — nothing is bought — and exercises the local pieces:
the new DB tables, the per-section markup override, and the line rewriter that
hides the provider endpoint behind our relay.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Import the *staged* tree, not the running bot: the live files fill in the
# modules the deploy does not touch (config, security, ui, web).
_ROOT = Path(tempfile.mkdtemp(prefix="sp_test_"))
_PKG = _ROOT / "app"
_PKG.mkdir()
for _src in sorted(Path("/opt/asf/app").glob("*.py")):
    shutil.copy2(_src, _PKG / _src.name)
for _src in sorted(Path("/opt/asf/_stage_sp").glob("*.py")):
    shutil.copy2(_src, _PKG / _src.name)
sys.path.insert(0, str(_ROOT))

import aiohttp

from app.db import Database, proxy_markup_key
from app.security import SecretCipher
from app.strikeproxy import (
    PLAN_TYPES,
    StrikeProxyClient,
    relay_endpoint,
    rewrite_lines,
    section_code,
)
from app.clients import ApiError, sale_price_kopecks


SAMPLE = [
    "proxy_abc123-country-us-session-strike_a1b2c3d4-time-1:pass1@beta.strikeproxy.net:8080",
    "proxy_abc123-country-us-session-strike_e5f6g7h8-time-1:pass1@beta.strikeproxy.net:8080",
]


async def check_local() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        key = Path(tmp) / ".secret.key"
        db = Database(Path(tmp) / "t.db", SecretCipher(key))
        await db.init()

        # per-section markup: override wins, reset falls back to the common one
        await db.set_setting("proxy_markup_percent", "100")
        assert await db.proxy_markup(section_code("residential")) == 100.0
        await db.set_setting(proxy_markup_key(section_code("residential")), "40")
        assert await db.proxy_markup(section_code("residential")) == 40.0
        assert await db.proxy_markup(section_code("mobile")) == 100.0
        await db.clear_setting(proxy_markup_key(section_code("residential")))
        assert await db.proxy_markup(section_code("residential")) == 100.0
        print("markup override: ok")

        # service bookkeeping
        await db.save_sp_service(
            7, "residential", service_id=0, proxy_username="", proxy_password=""
        )
        await db.add_sp_paid_gb(7, "residential", 0.05)
        await db.save_sp_service(
            7, "residential", service_id=1042, proxy_username="proxy_abc", proxy_password="p"
        )
        await db.add_sp_provisioned_gb(7, "residential", 1)
        row = await db.get_sp_service(7, "residential")
        assert row and row["paid_gb"] == 0.05 and row["provisioned_gb"] == 1.0
        assert row["service_id"] == 1042 and row["proxy_username"] == "proxy_abc"
        await db.set_sp_password(7, "residential", "newpass")
        row = await db.get_sp_service(7, "residential")
        assert row["proxy_password"] == "newpass"
        print("sp_services bookkeeping: ok")

        settings = await db.get_sp_settings(7, "residential")
        assert settings["rotation"] == "rotating" and settings["proxy_count"] == 10
        settings["country"] = "US"
        await db.save_sp_settings(7, "residential", settings)
        assert (await db.get_sp_settings(7, "residential"))["country"] == "US"
        print("sp_settings: ok")

        await db.close()

    # pricing arithmetic: markup applies to the provider's real rate
    assert sale_price_kopecks("0.5", 1.0, 100) == 100
    assert sale_price_kopecks("0.8", 1.0, 100) == 160
    print("pricing: ok")

    host, port = relay_endpoint()
    lines = rewrite_lines(SAMPLE, "hpu")
    assert all(line.startswith(f"{host}:{port}:") for line in lines), lines
    assert "strikeproxy" not in " ".join(lines), lines
    assert len(set(lines)) == 2, lines
    print(f"line rewrite → {host}:{port}: ok")
    print("   ", lines[0])
    print("   ", rewrite_lines(SAMPLE, "uph")[0])
    print("   ", rewrite_lines(SAMPLE, "url")[0])


async def check_remote(api_key: str) -> None:
    timeout = aiohttp.ClientTimeout(total=60, connect=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        sp = StrikeProxyClient(session, lambda: _key(api_key))
        me = await sp.reseller_me()
        print(
            "reseller:", me.get("username"), "| role:", me.get("role"),
            "| approved:", me.get("is_reseller"),
        )
        print("balance: $", await sp.balance())
        costs = await sp.plan_costs()
        print("costs per GB:", costs)
        assert set(costs) == set(PLAN_TYPES), costs
        services = await sp.services()
        print("services:", len(services))
        for plan in PLAN_TYPES:
            rows = await sp.locations(plan)
            print(f"locations {plan}: {len(rows)} стран, напр. {rows[:2]}")
            assert rows, plan
        try:
            await sp.generate(1, count=1)
        except ApiError as exc:
            print("generate on a foreign service refused as expected:", exc)
        else:
            raise AssertionError("generate should not accept a foreign service id")


async def _key(value: str) -> str:
    return value


async def main() -> int:
    await check_local()
    api_key = os.getenv("SP_KEY", "")
    if api_key:
        await check_remote(api_key)
    else:
        print("SP_KEY not set — remote checks skipped")
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
