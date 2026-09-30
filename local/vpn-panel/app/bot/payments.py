"""Payments: plan purchase (from balance or Telegram Stars) and balance top-up.

Internal unit == 1 Telegram Star by default (``STARS_PER_UNIT``). Stars need no
provider token and no external processor, which is what makes them usable for RU.
"""
from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import db, provisioner
from ..config import settings
from .common import UNITS, back_kb

router = Router()


async def _resync():
    try:
        await provisioner.sync_all()
    except Exception:  # noqa: BLE001
        pass


# --- plan catalogue ----------------------------------------------------

@router.callback_query(F.data == "buy")
async def buy_menu(cb: CallbackQuery) -> None:
    plans = await db.plans()
    user = await db.get_user(cb.from_user.id)
    b = InlineKeyboardBuilder()
    for p in plans:
        traffic = "∞" if not p["traffic_gb"] else f"{p['traffic_gb']}ГБ"
        b.button(
            text=f"{p['name']} — {p['price']} {UNITS} · {traffic}",
            callback_data=f"buy:{p['id']}",
        )
    b.button(text="‹ Назад", callback_data="menu")
    b.adjust(1)
    await cb.message.edit_text(
        f"<b>🛒 Тарифы</b>\n\nБаланс: <b>{user['balance']} {UNITS}</b>\n"
        f"Оплата — с баланса или напрямую звёздами Telegram.",
        reply_markup=b.as_markup(),
    )
    await cb.answer()


@router.callback_query(F.data.regexp(r"^buy:\d+$"))
async def buy_plan(cb: CallbackQuery) -> None:
    plan_id = int(cb.data.split(":")[1])
    plan = await db.get_plan(plan_id)
    if not plan:
        await cb.answer("Тариф не найден", show_alert=True)
        return
    user = await db.get_user(cb.from_user.id)
    b = InlineKeyboardBuilder()
    if user["balance"] >= plan["price"]:
        b.button(text=f"Оплатить с баланса ({plan['price']} {UNITS})",
                 callback_data=f"buy:{plan_id}:balance")
    b.button(text=f"Оплатить звёздами ({plan['price']} ⭐)",
             callback_data=f"buy:{plan_id}:stars")
    b.button(text="‹ Назад", callback_data="buy")
    b.adjust(1)
    traffic = "безлимит" if not plan["traffic_gb"] else f"{plan['traffic_gb']} ГБ"
    await cb.message.edit_text(
        f"<b>{plan['name']}</b>\n\n"
        f"Срок: {plan['duration_days']} дн\n"
        f"Трафик: {traffic}\n"
        f"Устройств: до {plan['device_limit']}\n"
        f"Цена: <b>{plan['price']} {UNITS}</b>",
        reply_markup=b.as_markup(),
    )
    await cb.answer()


async def _apply_plan(tg_id: int, plan: "db.aiosqlite.Row") -> None:
    await db.grant_time(
        tg_id, plan["duration_days"], plan_id=plan["id"],
        traffic_gb=plan["traffic_gb"], device_limit=plan["device_limit"],
        status="active",
    )
    asyncio.create_task(_resync())


@router.callback_query(F.data.regexp(r"^buy:\d+:balance$"))
async def buy_with_balance(cb: CallbackQuery) -> None:
    plan_id = int(cb.data.split(":")[1])
    plan = await db.get_plan(plan_id)
    if not plan:
        await cb.answer("Тариф не найден", show_alert=True)
        return
    if not await db.spend_balance(cb.from_user.id, plan["price"], plan_id):
        await cb.answer("Недостаточно средств на балансе.", show_alert=True)
        return
    await _apply_plan(cb.from_user.id, plan)
    await cb.message.edit_text(
        f"✅ Тариф «{plan['name']}» активирован на {plan['duration_days']} дн.\n"
        f"Ключи не меняются — просто обнови подписку в приложении.",
        reply_markup=back_kb("menu"),
    )
    await cb.answer("Оплачено")


@router.callback_query(F.data.regexp(r"^buy:\d+:stars$"))
async def buy_with_stars(cb: CallbackQuery) -> None:
    plan_id = int(cb.data.split(":")[1])
    plan = await db.get_plan(plan_id)
    if not plan:
        await cb.answer("Тариф не найден", show_alert=True)
        return
    await cb.message.answer_invoice(
        title=f"{settings.brand}: {plan['name']}",
        description=f"VPN-доступ на {plan['duration_days']} дней",
        payload=f"plan:{plan_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=plan["name"], amount=plan["price"])],
    )
    await cb.answer()


# --- balance top-up --------------------------------------------------

@router.callback_query(F.data == "topup")
async def topup_menu(cb: CallbackQuery) -> None:
    user = await db.get_user(cb.from_user.id)
    b = InlineKeyboardBuilder()
    for amount in db.TOPUP_PACKS:
        b.button(text=f"{amount} {UNITS}  ({amount} ⭐)", callback_data=f"topup:{amount}")
    b.button(text="‹ Назад", callback_data="menu")
    b.adjust(2, 2, 1)
    await cb.message.edit_text(
        f"<b>💳 Пополнить баланс</b>\n\n"
        f"Текущий баланс: <b>{user['balance']} {UNITS}</b>\n"
        f"1 {UNITS} = 1 звезда Telegram. С баланса можно оплачивать любые тарифы.",
        reply_markup=b.as_markup(),
    )
    await cb.answer()


@router.callback_query(F.data.startswith("topup:"))
async def topup_invoice(cb: CallbackQuery) -> None:
    amount = int(cb.data.split(":")[1])
    await cb.message.answer_invoice(
        title=f"{settings.brand}: пополнение",
        description=f"Пополнение баланса на {amount} {UNITS}",
        payload=f"topup:{amount}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount} единиц", amount=amount)],
    )
    await cb.answer()


# --- payment completion --------------------------------------------

@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery) -> None:
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(msg: Message) -> None:
    sp = msg.successful_payment
    payload = sp.invoice_payload
    stars = sp.total_amount
    await db.ensure_user(msg.from_user.id, msg.from_user.username)

    if payload.startswith("topup:"):
        units = stars // settings.stars_per_unit
        await db.add_balance(msg.from_user.id, units, method="stars",
                             payload=sp.telegram_payment_charge_id)
        user = await db.get_user(msg.from_user.id)
        await msg.answer(f"✅ Баланс пополнен на {units} {UNITS}. "
                         f"Текущий баланс: <b>{user['balance']} {UNITS}</b>.")
        return

    if payload.startswith("plan:"):
        plan = await db.get_plan(int(payload.split(":")[1]))
        if plan:
            await db.add_txn(msg.from_user.id, "purchase", -plan["price"],
                             plan_id=plan["id"], method="stars",
                             payload=sp.telegram_payment_charge_id)
            await _apply_plan(msg.from_user.id, plan)
            await msg.answer(f"✅ Тариф «{plan['name']}» активирован на "
                             f"{plan['duration_days']} дн. Обнови подписку в приложении.")
        return

    await msg.answer("Платёж получен, но не распознан. Напиши в поддержку.")
