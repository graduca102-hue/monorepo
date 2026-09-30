"""Show exactly which proxy pools the bot would offer right now, and at what price.

Usage (on srv2):  cd /opt/asf && ./.venv/bin/python check_proxy_menu.py

Read-only: renders the real hub keyboard through the live handlers, so it proves
what a customer would see without touching the provider's money.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, "/opt/asf")
os.chdir("/opt/asf")

import aiohttp  # noqa: E402

from app.db import Database  # noqa: E402
from app.handlers_user import proxy_section_enabled, proxy_section_prices  # noqa: E402
from app.keyboards import proxy_menu  # noqa: E402
from app.security import SecretCipher  # noqa: E402
from app.strikeproxy import StrikeProxyClient  # noqa: E402


async def main() -> None:
    db = Database(
        Path(os.getenv("DATABASE_PATH", "data/shop.db")),
        SecretCipher(Path(os.getenv("ENCRYPTION_KEY_PATH", "data/.secret.key"))),
    )
    await db.init()

    async def key() -> str:
        return await db.get_setting("sp_api_key", secret=True)

    print("раздел прокси включён:", await proxy_section_enabled(db))
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
        sp = StrikeProxyClient(session, key)
        prices = await proxy_section_prices(db, sp)
    print("секции и цены (центы):", prices)
    overrides = await db.service_overrides()
    for row in proxy_menu(prices, overrides).inline_keyboard:
        for btn in row:
            print(f"  [{btn.text}] -> {btn.callback_data}")
    await db.close()


asyncio.run(main())
