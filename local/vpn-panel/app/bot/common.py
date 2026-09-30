"""Shared bot helpers: formatting, guards, keyboards."""
from __future__ import annotations

import html
from datetime import datetime, timezone

import aiosqlite
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import settings
from .. import db, subscription

UNITS = "⭐"  # internal unit == 1 Telegram Star by default


def is_admin(uid: int) -> bool:
    return uid in settings.admin_ids


def esc(s: object) -> str:
    return html.escape(str(s))


def fmt_dt(ts: int | None) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


def fmt_left(ts: int | None) -> str:
    if not ts:
        return "—"
    secs = ts - int(datetime.now(tz=timezone.utc).timestamp())
    if secs <= 0:
        return "истекло"
    d, rem = divmod(secs, 86400)
    h = rem // 3600
    if d:
        return f"{d} дн {h} ч"
    return f"{h} ч"


def fmt_bytes(n: int | None) -> str:
    if not n:
        return "0"
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ПБ"


STATUS_RU = {
    "new": "не активен",
    "trial": "пробный",
    "active": "активна",
    "expired": "истекла",
    "banned": "заблокирован",
}


def main_menu_kb(uid: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🎁 Триал", callback_data="trial")
    b.button(text="👤 Профиль", callback_data="profile")
    b.button(text="⚙️ Управление подпиской", callback_data="sub")
    b.button(text="💳 Пополнить баланс", callback_data="topup")
    b.adjust(2, 1, 1)
    if is_admin(uid):
        b.row(InlineKeyboardButton(text="🛠 Админка", callback_data="admin"))
    return b.as_markup()


def back_kb(target: str = "menu", text: str = "‹ Назад") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, callback_data=target)
    ]])


async def profile_text(user: aiosqlite.Row) -> str:
    plan = await db.get_plan(user["plan_id"]) if user["plan_id"] else None
    lim = user["traffic_limit_bytes"]
    traffic = "безлимит" if not lim else (
        f"{fmt_bytes(user['traffic_used_bytes'])} / {fmt_bytes(lim)}"
    )
    return (
        f"<b>👤 Профиль</b>\n\n"
        f"ID: <code>{user['tg_id']}</code>\n"
        f"Статус: <b>{STATUS_RU.get(user['status'], user['status'])}</b>\n"
        f"Тариф: {esc(plan['name']) if plan else '—'}\n"
        f"Действует до: {fmt_dt(user['expires_at'])} ({fmt_left(user['expires_at'])})\n"
        f"Трафик: {traffic}\n"
        f"Устройств: до {user['device_limit']}\n"
        f"Баланс: <b>{user['balance']} {UNITS}</b>"
    )


def sub_manage_kb(has_access: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if has_access:
        b.button(text="📋 Показать ключи", callback_data="sub:keys")
        b.button(text="📲 Открыть в Happ", callback_data="sub:happ")
        b.button(text="📖 Инструкция", callback_data="sub:apps")
        b.button(text="♻️ Сменить ссылку", callback_data="sub:rotate")
        b.button(text="🛒 Продлить / купить", callback_data="buy")
    else:
        b.button(text="🎁 Активировать триал", callback_data="trial")
        b.button(text="🛒 Купить доступ", callback_data="buy")
    b.button(text="‹ Назад", callback_data="menu")
    b.adjust(1)
    return b.as_markup()


async def keys_text(user: aiosqlite.Row) -> str:
    servers = await db.active_servers()
    url = subscription.sub_url(user)
    happ = subscription.happ_link(user)
    lines = [
        "<b>🔑 Твои ключи</b>\n",
        "<b>Подписка</b> (один линк на все серверы — вставь в Happ / v2rayNG / Hiddify / Streisand):",
        f"<code>{esc(url)}</code>\n",
        f"<b>Happ deep-link:</b>\n<code>{esc(happ)}</code>\n",
    ]
    mtp = subscription.mtproto_links(user, servers)
    if mtp:
        lines.append("<b>MTProto-прокси для Telegram</b> (нажми, чтобы подключить):")
        for name, link in mtp:
            lines.append(f"• {esc(name)}: {esc(link)}")
        lines.append(
            "\n⚠️ MTProto-прокси и VPN-подписку <b>одновременно не включай</b> — "
            "трафик Telegram пойдёт через тоннель дважды и всё зависнет. "
            "Либо прокси, либо VPN (при включённом VPN прокси не нужен)."
        )
    if not servers:
        lines.append("\n⚠️ Серверы ещё не добавлены — ключи заработают, как только появится первый.")
    return "\n".join(lines)


APPS_GUIDE = (
    "<b>📖 Как подключиться</b>\n\n"
    "<b>Happ</b> (iOS / Android / Windows / macOS) — рекомендуется:\n"
    "1. Установи Happ из стора.\n"
    "2. Нажми «Открыть в Happ» — профиль добавится сам.\n"
    "3. Включи. Приложение само выберет самый быстрый сервер.\n\n"
    "<b>v2rayNG / NekoBox (Android), Streisand / Shadowrocket (iOS), Hiddify (все ОС):</b>\n"
    "Добавь профиль по ссылке-подписке из «Показать ключи».\n\n"
    "<b>Telegram напрямую (MTProto):</b> нажми на ссылку <code>tg://proxy…</code> "
    "из «Показать ключи» — Telegram предложит включить прокси.\n\n"
    "⚠️ <b>MTProto-прокси и VPN вместе не включай.</b> Happ/VPN заворачивает в тоннель "
    "весь трафик, включая коннект к прокси — Telegram зависнет. Держи что-то одно "
    "(при включённом VPN прокси не нужен). Если нужны оба — в Happ исключи Telegram "
    "из VPN (Android: Settings → Per-app proxy) или добавь IP прокси в прямые маршруты.\n\n"
    "Если перестало работать — обнови подписку в приложении, сервер мог смениться."
)
