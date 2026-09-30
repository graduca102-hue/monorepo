"""User-facing router: main menu, trial, profile, subscription management."""
from __future__ import annotations

import asyncio

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from .. import db, provisioner, subscription
from ..config import settings
from .common import (
    APPS_GUIDE,
    back_kb,
    keys_text,
    main_menu_kb,
    profile_text,
    sub_manage_kb,
)

router = Router()

WELCOME = (
    "<b>{brand}</b> — быстрый VPN без логов.\n\n"
    "• Один ключ — все серверы и протоколы (VLESS-Reality + MTProto)\n"
    "• Работает в Happ, v2rayNG, Hiddify, Streisand\n"
    "• {trial} дней бесплатно на старте\n\n"
    "Выбери действие:"
)


def _has_access(user) -> bool:
    return user["status"] in ("trial", "active") and (
        not user["expires_at"] or user["expires_at"] > db.now()
    )


async def _resync():
    try:
        await provisioner.sync_all()
    except Exception:  # noqa: BLE001 - background best effort
        pass


@router.message(CommandStart())
async def start(msg: Message) -> None:
    await db.ensure_user(msg.from_user.id, msg.from_user.username)
    await msg.answer(
        WELCOME.format(brand=settings.brand, trial=settings.trial_days),
        reply_markup=main_menu_kb(msg.from_user.id),
    )


@router.callback_query(F.data == "menu")
async def menu(cb: CallbackQuery) -> None:
    await db.ensure_user(cb.from_user.id, cb.from_user.username)
    await cb.message.edit_text(
        WELCOME.format(brand=settings.brand, trial=settings.trial_days),
        reply_markup=main_menu_kb(cb.from_user.id),
    )
    await cb.answer()


@router.callback_query(F.data == "trial")
async def trial(cb: CallbackQuery) -> None:
    user = await db.ensure_user(cb.from_user.id, cb.from_user.username)
    if user["trial_used"]:
        await cb.answer("Триал уже был использован.", show_alert=True)
        return
    await db.start_trial(cb.from_user.id)
    user = await db.get_user(cb.from_user.id)
    asyncio.create_task(_resync())
    await cb.message.edit_text(
        f"🎁 Пробный доступ на {settings.trial_days} дн активирован!\n\n"
        + await keys_text(user),
        reply_markup=sub_manage_kb(True),
        disable_web_page_preview=True,
    )
    await cb.answer("Готово!")


@router.callback_query(F.data == "profile")
async def profile(cb: CallbackQuery) -> None:
    user = await db.ensure_user(cb.from_user.id, cb.from_user.username)
    await cb.message.edit_text(await profile_text(user), reply_markup=back_kb("menu"))
    await cb.answer()


@router.callback_query(F.data == "sub")
async def sub_manage(cb: CallbackQuery) -> None:
    user = await db.ensure_user(cb.from_user.id, cb.from_user.username)
    access = _has_access(user)
    head = "<b>⚙️ Управление подпиской</b>\n\n"
    if access:
        head += await keys_text(user)
    else:
        head += "У тебя нет активного доступа. Активируй триал или купи подписку."
    await cb.message.edit_text(head, reply_markup=sub_manage_kb(access),
                               disable_web_page_preview=True)
    await cb.answer()


@router.callback_query(F.data == "sub:keys")
async def sub_keys(cb: CallbackQuery) -> None:
    user = await db.get_user(cb.from_user.id)
    if not user or not _has_access(user):
        await cb.answer("Нет активного доступа.", show_alert=True)
        return
    await cb.message.edit_text(await keys_text(user), reply_markup=back_kb("sub"),
                               disable_web_page_preview=True)
    await cb.answer()


@router.callback_query(F.data == "sub:happ")
async def sub_happ(cb: CallbackQuery) -> None:
    user = await db.get_user(cb.from_user.id)
    if not user or not _has_access(user):
        await cb.answer("Нет активного доступа.", show_alert=True)
        return
    link = subscription.happ_link(user)
    await cb.message.answer(
        f"Открой эту ссылку на устройстве с установленным Happ:\n<code>{link}</code>\n\n"
        f"Или добавь вручную по подписке:\n<code>{subscription.sub_url(user)}</code>",
        disable_web_page_preview=True,
    )
    await cb.answer()


@router.callback_query(F.data == "sub:apps")
async def sub_apps(cb: CallbackQuery) -> None:
    await cb.message.edit_text(APPS_GUIDE, reply_markup=back_kb("sub"),
                               disable_web_page_preview=True)
    await cb.answer()


@router.callback_query(F.data == "sub:rotate")
async def sub_rotate(cb: CallbackQuery) -> None:
    user = await db.get_user(cb.from_user.id)
    if not user:
        await cb.answer()
        return
    await db.rotate_sub_token(cb.from_user.id)
    asyncio.create_task(_resync())
    user = await db.get_user(cb.from_user.id)
    await cb.message.edit_text(
        "♻️ Ссылка обновлена. Старая больше не работает — обнови профиль в приложении.\n\n"
        + await keys_text(user),
        reply_markup=back_kb("sub"),
        disable_web_page_preview=True,
    )
    await cb.answer("Ссылка сменена")
