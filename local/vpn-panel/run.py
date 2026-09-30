"""Entrypoint: subscription web server + Telegram bot in one process."""
from __future__ import annotations

import asyncio
import logging

from app import db
from app.bot.main import run_bot
from app.config import settings
from app.web.server import start_web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("vpn-panel")


async def main() -> None:
    await db.init()
    runner = await start_web()
    log.info("subscription web on %s:%s (public: %s)",
             settings.web_host, settings.web_port, settings.sub_base_url)
    try:
        await run_bot()
    finally:
        await runner.cleanup()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as e:
        log.info("shutdown: %s", e)
