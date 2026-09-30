"""Live smoke test: DB init, web endpoint, Telegram getMe. Needs .env + network."""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import aiohttp

from app import db
from app.config import settings
from app.web.server import start_web


async def main() -> None:
    settings_db = settings.db_path
    await db.init()
    print("db init ok:", settings_db)

    u = await db.ensure_user(999001, "smoke")
    await db.add_server("SMOKE-NL", "203.0.113.7", "root", "pw")
    sid = (await db.servers())[-1]["id"]
    await db.set_server(sid, reality_public_key="TESTPUBKEY", status="active")
    await db.start_trial(999001)
    u = await db.get_user(999001)
    print("user token:", u["sub_token"], "status:", u["status"])

    runner = await start_web()
    try:
        async with aiohttp.ClientSession() as s:
            for ua in ["Happ/1.0", "sing-box/1.9", "clash-verge/1.0"]:
                async with s.get(
                    f"http://127.0.0.1:{settings.web_port}/sub/{u['sub_token']}",
                    headers={"User-Agent": ua},
                ) as r:
                    body = await r.text()
                    print(f"\n== UA={ua} == {r.status}")
                    print("  profile-title:", r.headers.get("profile-title"))
                    print("  userinfo:", r.headers.get("subscription-userinfo"))
                    print("  body[:120]:", body[:120].replace("\n", " "))
            async with s.get(
                f"http://127.0.0.1:{settings.web_port}/sub/does-not-exist"
            ) as r:
                print("\nunknown token ->", r.status)
    finally:
        await runner.cleanup()

    # Telegram reachability
    from aiogram import Bot
    try:
        bot = Bot(settings.bot_token)
        me = await bot.get_me()
        print("\ngetMe ok: @%s (id %s)" % (me.username, me.id))
        await bot.session.close()
    except Exception as e:  # noqa: BLE001
        print("\ngetMe FAILED:", e)

    await db.close()
    # clean smoke rows
    os.remove(settings.db_path)


asyncio.run(main())
