"""Обработка заявок на вступление."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import ChatJoinRequest, ChatMemberUpdated, Message

from .config import Config
from . import storage

log = logging.getLogger("join-approver")
router = Router()


def _chat_allowed(cfg: Config, chat_id: int) -> bool:
    return not cfg.allowed_chat_ids or chat_id in cfg.allowed_chat_ids


def _who(req: ChatJoinRequest) -> str:
    u = req.from_user
    tag = f"@{u.username}" if u.username else ""
    return f"{u.full_name} {tag} (id={u.id})".strip()


async def _notify_owner(bot: Bot, cfg: Config, text: str) -> None:
    if not cfg.owner_id:
        return
    try:
        await bot.send_message(cfg.owner_id, text, disable_web_page_preview=True)
    except Exception as e:  # уведомления не должны ронять обработку
        log.warning("owner notify failed: %s", e)


@router.my_chat_member()
async def on_my_status(upd: ChatMemberUpdated, bot: Bot, cfg: Config) -> None:
    """Бота добавили/повысили/удалили — запоминаем чат."""
    chat = upd.chat
    status = upd.new_chat_member.status  # administrator / member / left / kicked / restricted
    storage.remember_chat(chat.id, chat.title or "", str(status), chat.username)
    log.info("my_chat_member chat=%r -> %s", chat.title or chat.id, status)

    if status == "administrator":
        can_invite = bool(getattr(upd.new_chat_member, "can_invite_users", False))
        note = "" if can_invite else "\n⚠️ Нет права «Добавление участников» — заявки одобрять не смогу."
        await _notify_owner(bot, cfg, f"➕ Добавлен админом: {chat.title or chat.id}{note}")
    elif status in ("left", "kicked"):
        await _notify_owner(bot, cfg, f"➖ Удалён из: {chat.title or chat.id}")


@router.chat_join_request()
async def on_join_request(req: ChatJoinRequest, bot: Bot, cfg: Config) -> None:
    chat = req.chat
    who = _who(req)
    storage.remember_chat(chat.id, chat.title or "", "administrator", chat.username)

    if not _chat_allowed(cfg, chat.id):
        log.info("skip (chat not allowed) chat=%s user=%s", chat.id, who)
        return

    if not cfg.auto_approve:
        log.info("join request (approve OFF) chat=%r user=%s", chat.title or chat.id, who)
        await _notify_owner(bot, cfg, f"📥 Заявка (авто-одобрение выкл)\nЧат: {chat.title or chat.id}\nОт: {who}")
        return

    if cfg.approve_delay:
        await asyncio.sleep(cfg.approve_delay)

    try:
        await req.approve()
        log.info("approved chat=%r user=%s", chat.title or chat.id, who)
    except TelegramBadRequest as e:
        # заявка уже обработана / истекла — не критично
        log.warning("approve failed chat=%s user=%s: %s", chat.id, who, e.message)
        return
    except TelegramForbiddenError as e:
        log.error("no rights to approve in chat=%r: %s", chat.title or chat.id, e.message)
        await _notify_owner(bot, cfg, f"⚠️ Нет прав одобрять заявки в «{chat.title or chat.id}». Дай боту право добавлять участников.")
        return

    storage.bump_approved(chat.id)
    await _notify_owner(bot, cfg, f"✅ Одобрен: {who}\nЧат: {chat.title or chat.id}")

    if cfg.welcome_text:
        try:
            await bot.send_message(req.from_user.id, cfg.welcome_text, disable_web_page_preview=True)
        except (TelegramForbiddenError, TelegramBadRequest):
            pass  # пользователь не открыл ЛС с ботом — норма


@router.message(Command("start", "help"))
async def cmd_start(msg: Message, cfg: Config) -> None:
    mode = "включено ✅" if cfg.auto_approve else "выключено ⛔"
    scope = "во всех чатах, где я админ" if not cfg.allowed_chat_ids else f"в {len(cfg.allowed_chat_ids)} чат(ах) из списка"
    await msg.answer(
        "Бот моментально одобряет заявки на вступление.\n\n"
        f"Авто-одобрение: {mode}\n"
        f"Область: {scope}\n\n"
        "Добавь меня админом в канал/группу с правом «Добавление участников» "
        "и включи «Одобрять заявки на вступление».\n\n"
        "/chats — список известных чатов"
    )


@router.message(Command("id"))
async def cmd_id(msg: Message) -> None:
    await msg.answer(f"chat_id: <code>{msg.chat.id}</code>", parse_mode="HTML")


@router.message(Command("chats"))
async def cmd_chats(msg: Message, cfg: Config) -> None:
    if cfg.owner_id and msg.from_user and msg.from_user.id != cfg.owner_id:
        return
    rows = storage.list_chats()
    if not rows:
        await msg.answer(
            "Пока ни одного чата не зафиксировано.\n"
            "Чат появится здесь, когда меня туда добавят админом или придёт первая заявка."
        )
        return
    lines = ["<b>Известные чаты:</b>"]
    for r in rows:
        icon = "✅" if r.get("status") == "administrator" else "➖"
        uname = f" @{r['username']}" if r.get("username") else ""
        appr = r.get("approved_count", 0)
        tail = f" · одобрено: {appr}" if appr else ""
        seen = dt.datetime.fromtimestamp(r.get("updated", 0)).strftime("%d.%m %H:%M")
        lines.append(f"{icon} {r.get('title') or r['id']}{uname}\n   <code>{r['id']}</code>{tail} · {seen}")
    await msg.answer("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
