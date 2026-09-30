from __future__ import annotations

import asyncpg
from aiohttp import web
from aiogram import Dispatcher

from .engine import PartnerBotEngine
from .handlers import partner_router
from .middleware import PartnerBotContextMiddleware
from .repository import PartnerRepository


async def create_multibot_app(
    postgres_dsn: str,
    public_base_url: str,
    extra_routers: list | None = None,
) -> tuple[web.Application, PartnerBotEngine]:
    """
    Bootstrap helper.

    Usage:
        app, engine = await create_multibot_app(
            postgres_dsn="postgresql://user:pass@localhost:5432/botshop",
            public_base_url="https://your-domain.com",
        )
    """

    pool = await asyncpg.create_pool(dsn=postgres_dsn, min_size=1, max_size=10)
    repository = PartnerRepository(pool)

    dispatcher = Dispatcher()
    dispatcher.include_router(partner_router)

    for router in extra_routers or []:
        dispatcher.include_router(router)

    engine = PartnerBotEngine(
        dispatcher=dispatcher,
        repository=repository,
        public_base_url=public_base_url,
    )
    dispatcher.update.outer_middleware(PartnerBotContextMiddleware(engine))

    app = web.Application()
    app["postgres_pool"] = pool
    app["partner_engine"] = engine
    engine.register_routes(app)

    async def on_startup(_: web.Application) -> None:
        await engine.load_active_bots()

    async def on_shutdown(_: web.Application) -> None:
        await engine.close()
        await pool.close()

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    return app, engine
