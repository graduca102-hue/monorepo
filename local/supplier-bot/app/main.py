"""Entry point: long-polling supplier bot for SOUS MARKET."""
from __future__ import annotations

import asyncio
import html
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BotCommand

from .catalog import Catalog
from .config import Config
from .db import Database
from .handlers import money, router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("supplier-bot")

SALES_POLL_SECONDS = 15


async def notify_sales(bot: Bot, db: Database) -> None:
    """Tell suppliers about storefront orders that botshop recorded in supplier_orders."""
    while True:
        try:
            for sale in db.pending_sale_notifications():
                amount = round(sale["quantity"] * sale["supplier_unit_usd"], 2)
                try:
                    await bot.send_message(
                        sale["supplier_id"],
                        f"💰 <b>Продажа</b> · заказ {html.escape(sale['order_number'] or '')}\n\n"
                        f"🏷 {html.escape(sale['title'] or 'Товар')}\n"
                        f"📦 {sale['quantity']} шт × {money(sale['supplier_unit_usd'])} $ = <b>{money(amount)} $</b>",
                    )
                except (TelegramForbiddenError, TelegramBadRequest) as error:
                    log.warning("sale notify to %s failed permanently: %s", sale["supplier_id"], error)
                except Exception as error:
                    log.warning("sale notify to %s failed, will retry: %s", sale["supplier_id"], error)
                    continue
                db.mark_sale_notified(sale["uuid"])
        except Exception:
            log.exception("sales notifier iteration failed")
        await asyncio.sleep(SALES_POLL_SECONDS)


async def _run() -> None:
    cfg = Config.from_env()
    db = Database(cfg.db_path)
    db.init()
    catalog = Catalog(cfg.catalog_url)
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = Dispatcher()
    dp.include_router(router)

    me = await bot.get_me()
    try:
        await bot.set_my_commands([BotCommand(command="start", description="Меню поставщика")])
        await bot.set_my_short_description(
            f"Меню поставщика SOUS MARKET. Тех поддержка: @{cfg.support_username}"
        )
    except Exception as error:
        log.warning("bot profile setup failed: %s", error)
    log.info(
        "started as @%s | admins=%s | markup=%s%% | max_cards_per_category=%s",
        me.username, sorted(cfg.admin_ids), cfg.markup_percent, cfg.max_cards_per_category,
    )
    notifier = asyncio.create_task(notify_sales(bot, db))
    try:
        await dp.start_polling(bot, cfg=cfg, db=db, catalog=catalog,
                               allowed_updates=dp.resolve_used_update_types())
    finally:
        notifier.cancel()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(_run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
