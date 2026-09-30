"""Assemble the bot: routers, default HTML parse mode, background jobs."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .. import db, provisioner
from ..config import settings
from . import admin, payments, user

log = logging.getLogger("vpn-panel.bot")


async def _expiry_loop(bot: Bot) -> None:
    """Downgrade expired users and push fresh configs so their keys stop working."""
    while True:
        try:
            expired = await db.expire_overdue()
            if expired:
                log.info("expired %d users, resyncing", len(expired))
                await provisioner.sync_all()
                for uid in expired:
                    try:
                        await bot.send_message(
                            uid, "⏳ Подписка закончилась. Продли в разделе «Управление подпиской»."
                        )
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001
            log.exception("expiry loop tick failed")
        await asyncio.sleep(300)


async def run_bot() -> None:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is not set (.env)")

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(admin.router)
    dp.include_router(payments.router)
    dp.include_router(user.router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Меню"),
        BotCommand(command="admin", description="Админка (только для владельца)"),
    ])

    asyncio.create_task(_expiry_loop(bot))
    log.info("bot polling as @%s", (await bot.get_me()).username)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
