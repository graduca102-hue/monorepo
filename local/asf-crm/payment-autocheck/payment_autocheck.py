"""Background safety net for missed Heleket payment webhooks.

The webhook (`POST /webhooks/heleket`) is the normal way a payment gets
credited, but it depends on Heleket actually reaching our public endpoint —
if `public_base_url` is misconfigured, the reverse proxy in front of it is
down, or a single webhook delivery is lost, the invoice is genuinely paid on
Heleket's side yet stays uncredited locally forever (the only other trigger is
the customer manually pressing "Проверить оплату"). This loop periodically
re-polls Heleket's `payment/info` for any locally-uncredited payment and runs
it through the same `process_payment_status` the webhook uses, so a missed
webhook self-heals instead of requiring a manual balance top-up.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Iterable

from aiogram import Bot

from .clients import ApiError, HeleketClient, SousClient
from .db import Database
from .handlers_user import process_payment_status

logger = logging.getLogger(__name__)

# Minutes between sweeps. 0 disables the loop entirely.
AUTOCHECK_MIN = int(os.getenv("PAYMENT_AUTOCHECK_MIN", "5") or 5)
# How far back to keep re-checking an uncredited payment before giving up on it.
MAX_AGE_DAYS = int(os.getenv("PAYMENT_AUTOCHECK_MAX_AGE_DAYS", "14") or 14)
# Local statuses that Heleket has already told us are final and unpaid — skip
# re-polling these every sweep, they will not turn into "paid".
TERMINAL_STATUSES = ("cancel", "fail", "system_fail", "refund_fail", "wrong_amount")
# Pause between orders so a large backlog can't hammer the Heleket API.
PER_ORDER_PAUSE = 1.0


async def _sweep(db: Database, heleket: HeleketClient, sous: SousClient, bot: Bot, admin_ids: Iterable[int]) -> None:
    pending = await db.pending_payments(max_age_days=MAX_AGE_DAYS, exclude_statuses=TERMINAL_STATUSES)
    if not pending:
        return
    logger.info("payment autocheck: re-polling %d uncredited payment(s)", len(pending))
    for payment in pending:
        order_id = str(payment["order_id"])
        try:
            info = await heleket.payment_info(order_id)
        except ApiError as exc:
            logger.warning("payment autocheck: payment_info(%s) failed: %s", order_id, exc)
            await asyncio.sleep(PER_ORDER_PAUSE)
            continue
        status = str(info.get("payment_status") or info.get("status") or "")
        if not status:
            await asyncio.sleep(PER_ORDER_PAUSE)
            continue
        try:
            credited = await process_payment_status(
                db=db,
                sous=sous,
                order_id=order_id,
                provider_status=status,
                bot=bot,
                notify_user=True,
                admin_ids=admin_ids,
            )
        except Exception:
            logger.exception("payment autocheck: could not process %s (status=%s)", order_id, status)
            await asyncio.sleep(PER_ORDER_PAUSE)
            continue
        if credited and not credited.get("already_credited"):
            logger.info("payment autocheck: credited missed payment %s (status=%s)", order_id, status)
        await asyncio.sleep(PER_ORDER_PAUSE)


async def payment_autocheck_loop(
    db: Database, heleket: HeleketClient, sous: SousClient, bot: Bot, admin_ids: Iterable[int]
) -> None:
    if AUTOCHECK_MIN <= 0:
        logger.info("payment autocheck disabled (PAYMENT_AUTOCHECK_MIN=0)")
        return
    logger.info("payment autocheck loop: every %d min", AUTOCHECK_MIN)
    await asyncio.sleep(45)  # let startup settle before the first sweep
    while True:
        try:
            await _sweep(db, heleket, sous, bot, admin_ids)
        except Exception:
            logger.exception("payment autocheck sweep failed")
        await asyncio.sleep(AUTOCHECK_MIN * 60)
