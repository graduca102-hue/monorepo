from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .clients import HeleketClient, SousClient
from .config import Config
from .dataimpulse import DataImpulseClient
from .db import Database
from .handlers_admin import setup_admin_router
from .handlers_user import market_delivery_recovery_loop, router as user_router
from .payment_autocheck import payment_autocheck_loop
from .security import SecretCipher
from .strikeproxy import StrikeProxyClient
from .web import create_web_app


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    config = Config.from_env()
    db = Database(config.database_path, SecretCipher(config.encryption_key_path))
    await db.init()
    await db.seed_config(
        sous_api_key=config.sous_api_key,
        heleket_merchant_id=config.heleket_merchant_id,
        heleket_api_key=config.heleket_api_key,
        public_base_url=config.public_base_url,
        support_username=config.support_username,
    )

    timeout = aiohttp.ClientTimeout(total=35, connect=10)
    session = aiohttp.ClientSession(timeout=timeout)
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    async def sous_key() -> str:
        return await db.get_setting("sous_api_key", secret=True)

    async def heleket_credentials() -> tuple[str, str]:
        return (
            await db.get_setting("heleket_merchant_id", secret=True),
            await db.get_setting("heleket_api_key", secret=True),
        )

    async def dataimpulse_credentials() -> tuple[str, str]:
        return (
            await db.get_setting("di_login", secret=True),
            await db.get_setting("di_password", secret=True),
        )

    async def strikeproxy_key() -> str:
        return await db.get_setting("sp_api_key", secret=True)

    sous = SousClient(session, config.sous_api_base, sous_key)
    dataimpulse = DataImpulseClient(session, dataimpulse_credentials)
    strikeproxy = StrikeProxyClient(session, strikeproxy_key)
    heleket = HeleketClient(session, heleket_credentials)

    dispatcher = Dispatcher()
    dispatcher.include_router(setup_admin_router(config.admin_ids))
    dispatcher.include_router(user_router)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Открыть магазин"),
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="admin", description="Админка"),
            BotCommand(command="cancel", description="Отменить действие"),
        ]
    )

    app = create_web_app(db, bot, sous, config.admin_ids)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.host, config.port)
    await site.start()
    logging.getLogger(__name__).info("HTTP server is listening on %s:%s", config.host, config.port)

    payment_autocheck_task = asyncio.create_task(
        payment_autocheck_loop(db, heleket, sous, bot, config.admin_ids)
    )
    # Deliveries that outlive their purchase-time poller (or a restart) still
    # have to reach the buyer, or be refunded when SOUS gave the money back.
    delivery_recovery_task = asyncio.create_task(
        market_delivery_recovery_loop(db, bot, sous)
    )

    try:
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            config=config,
            db=db,
            sous=sous,
            di=dataimpulse,
            sp=strikeproxy,
            heleket=heleket,
        )
    finally:
        payment_autocheck_task.cancel()
        delivery_recovery_task.cancel()
        for task in (payment_autocheck_task, delivery_recovery_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await runner.cleanup()
        await bot.session.close()
        await session.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())

