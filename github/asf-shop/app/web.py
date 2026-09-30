from __future__ import annotations

import logging
from typing import Any

from aiohttp import web
from aiogram import Bot

from .clients import HeleketClient, SousClient
from .db import Database
from .handlers_user import process_payment_status


logger = logging.getLogger(__name__)


async def health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True, "service": "telegram-shop"})


async def heleket_webhook(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    bot: Bot = request.app["bot"]
    sous: SousClient = request.app["sous"]
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)
    api_key = await db.get_setting("heleket_api_key", secret=True)
    if not HeleketClient.verify_webhook(payload, api_key):
        logger.warning("Rejected Heleket webhook with invalid signature")
        return web.json_response({"ok": False, "error": "invalid_signature"}, status=401)
    order_id = str(payload.get("order_id") or "")
    status = str(payload.get("status") or payload.get("payment_status") or "")
    if not order_id or not status:
        return web.json_response({"ok": False, "error": "missing_fields"}, status=400)
    try:
        await process_payment_status(
            db=db,
            sous=sous,
            order_id=order_id,
            provider_status=status,
            bot=bot,
            admin_ids=request.app["admin_ids"],
        )
    except Exception:
        logger.exception("Could not process payment webhook for %s", order_id)
        return web.json_response({"ok": False, "error": "processing_failed"}, status=500)
    return web.json_response({"ok": True})


def create_web_app(
    db: Database,
    bot: Bot,
    sous: SousClient,
    admin_ids: frozenset[int] = frozenset(),
) -> web.Application:
    app = web.Application(client_max_size=256 * 1024)
    app["db"] = db
    app["bot"] = bot
    app["sous"] = sous
    app["admin_ids"] = admin_ids
    app.router.add_get("/health", health)
    app.router.add_post("/webhooks/heleket", heleket_webhook)
    return app
