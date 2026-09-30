"""Pull StrikeProxy reseller prices into the shop's settings and show the sale prices.

Usage (on srv2):  cd /opt/asf && ./.venv/bin/python sync_sp_costs.py

Read-only against the provider (catalog + balance); writes only ``sp_cost_<plan>``.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "/opt/asf")
os.chdir("/opt/asf")

import aiohttp  # noqa: E402

from app.clients import sale_price_kopecks  # noqa: E402
from app.db import Database  # noqa: E402
from app.security import SecretCipher  # noqa: E402
from app.strikeproxy import (  # noqa: E402
    PLAN_TYPES,
    StrikeProxyClient,
    cost_setting_key,
    plan_label,
    section_code,
)


async def main() -> None:
    db = Database(
        Path(os.getenv("DATABASE_PATH", "data/shop.db")),
        SecretCipher(Path(os.getenv("ENCRYPTION_KEY_PATH", "data/.secret.key"))),
    )
    await db.init()

    async def key() -> str:
        return await db.get_setting("sp_api_key", secret=True)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        sp = StrikeProxyClient(session, key)
        print("reseller balance: $", await sp.balance())
        costs = await sp.plan_costs()
        for plan, price in costs.items():
            await db.set_setting(cost_setting_key(plan), f"{price:g}")

    for plan in PLAN_TYPES:
        cost = await db.get_setting(cost_setting_key(plan))
        markup = await db.proxy_markup(section_code(plan))
        rate, _ = await db.proxy_pricing(section_code(plan))
        price = sale_price_kopecks(cost or "0", rate, markup) / 100 if cost else 0
        print(f"{plan:12s} {plan_label(plan):20s} закупка {cost or '—':>6} $ "
              f"наценка {markup:g}% → продажа {price:.2f} $/GB")
    await db.close()


asyncio.run(main())
