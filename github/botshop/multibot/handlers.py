from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from .models import PartnerBot
from .repository import PartnerRepository


partner_router = Router(name="partner-router")


@partner_router.message(Command("partner_stats"))
async def partner_stats(
    message: Message,
    partner_bot: PartnerBot,
    partners_repo: PartnerRepository,
) -> None:
    """Example handler for a franchise owner inside their own bot."""

    if message.from_user is None or message.from_user.id != partner_bot.owner_id:
        await message.answer("Эта команда доступна только владельцу магазина.")
        return

    summary = await partners_repo.get_partner_summary(partner_bot.id)
    await message.answer(
        "Панель партнера\n\n"
        f"Бот: @{partner_bot.bot_username}\n"
        f"Текущая наценка: {partner_bot.margin_percentage}%\n"
        f"Заказов: {summary.total_orders}\n"
        f"Оборот: {summary.total_revenue:.2f}\n"
        f"Баланс партнера: {summary.partner_balance:.2f}"
    )
