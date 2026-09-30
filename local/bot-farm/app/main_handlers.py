"""Хэндлеры главного бота-управления: приём .txt с токенами и команды администрирования."""
from __future__ import annotations

import asyncio
import io
import logging
import re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError
from aiogram.filters import Command
from aiogram.types import Message

from . import storage
from .config import Config
from .manager import BotManager

log = logging.getLogger("bot-farm.main")
router = Router()

_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")


def _is_owner(msg: Message, cfg: Config) -> bool:
    return bool(msg.from_user) and msg.from_user.id == cfg.owner_id


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "|" in line:
        token, text = line.split("|", 1)
        return token.strip(), text.strip()
    parts = line.split(None, 1)
    token = parts[0].strip()
    text = parts[1].strip() if len(parts) > 1 else ""
    return token, text


def _fmt_bot_line(row: dict, running: bool, audience: int) -> str:
    icon = "🟢" if running else "🔴"
    uname = f"@{row['username']}" if row.get("username") else "?"
    preview = (row.get("reply_text") or "").strip()
    preview = (preview[:40] + "…") if len(preview) > 40 else (preview or "—")
    return f"{icon} {uname} · id={row['id']} · аудитория: {audience}\n   текст: {preview}"


@router.message(Command("start", "help"))
async def cmd_start(msg: Message, cfg: Config) -> None:
    if not _is_owner(msg, cfg):
        return
    await msg.answer(
        "🤖 <b>Бот-управление ботами</b>\n\n"
        "Пришли <code>.txt</code>-файл со списком токенов (по одному на строку) — "
        "для каждого поднимется отдельный бот, который копит аудиторию (реагирует на "
        "/start и любое сообщение) и отвечает заданным текстом.\n\n"
        "Формат строки: <code>ТОКЕН</code> или <code>ТОКЕН | текст ответа</code>. "
        "Без текста используется общий текст по умолчанию — задаётся через /setall.\n\n"
        "<b>Команды:</b>\n"
        "/list — список ботов, статус, размер аудитории\n"
        "/settext &lt;@username|id&gt; &lt;текст&gt; — текст ответа для одного бота\n"
        "/setall &lt;текст&gt; — текст ответа для всех ботов сразу\n"
        "/stats — сводная статистика\n"
        "/remove &lt;@username|id&gt; — остановить и удалить бота\n"
        "/broadcast &lt;@username|id&gt; &lt;текст&gt; — разослать текст всей накопленной аудитории бота",
        parse_mode="HTML",
    )


@router.message(F.document.mime_type == "text/plain")
async def on_tokens_file(msg: Message, bot: Bot, cfg: Config, manager: BotManager) -> None:
    if not _is_owner(msg, cfg):
        return
    doc = msg.document
    if not (doc.file_name or "").lower().endswith(".txt"):
        return

    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    try:
        raw = buf.getvalue().decode("utf-8")
    except UnicodeDecodeError:
        await msg.answer("Не смог прочитать файл как UTF-8 текст.")
        return

    lines = [_parse_line(l) for l in raw.splitlines()]
    entries = [e for e in lines if e]
    if not entries:
        await msg.answer("В файле не нашлось ни одной строки с токеном.")
        return

    added: list[str] = []
    failed: list[str] = []
    for token, text in entries:
        if not _TOKEN_RE.match(token):
            failed.append(f"{token[:20]}… — не похоже на токен")
            continue
        try:
            row = await manager.add_from_token(token, text)
            added.append(f"@{row['username']}")
        except (TelegramUnauthorizedError, TelegramBadRequest) as e:
            failed.append(f"{token[:15]}… — невалидный токен ({e.message if hasattr(e, 'message') else e})")
        except Exception as e:
            log.exception("не смог добавить бота из файла")
            failed.append(f"{token[:15]}… — ошибка: {e}")

    lines_out = [f"Обработано строк: {len(entries)}"]
    if added:
        lines_out.append(f"\n✅ Запущено ({len(added)}): " + ", ".join(added))
    if failed:
        lines_out.append(f"\n⚠️ Ошибки ({len(failed)}):\n" + "\n".join(failed))
    await msg.answer("\n".join(lines_out))


@router.message(Command("list"))
async def cmd_list(msg: Message, cfg: Config, manager: BotManager) -> None:
    if not _is_owner(msg, cfg):
        return
    rows = await storage.list_bots()
    if not rows:
        await msg.answer("Пока ни одного бота не добавлено. Пришли .txt со списком токенов.")
        return
    parts = []
    for row in rows:
        audience = await storage.audience_count(row["id"])
        parts.append(_fmt_bot_line(row, manager.is_running(row["id"]), audience))
    await msg.answer("\n\n".join(parts))


@router.message(Command("stats"))
async def cmd_stats(msg: Message, cfg: Config, manager: BotManager) -> None:
    if not _is_owner(msg, cfg):
        return
    rows = await storage.list_bots()
    total_audience = 0
    running = 0
    for row in rows:
        total_audience += await storage.audience_count(row["id"])
        if manager.is_running(row["id"]):
            running += 1
    await msg.answer(
        f"Ботов: {len(rows)} (запущено: {running})\n"
        f"Суммарная аудитория: {total_audience}"
    )


@router.message(Command("settext"))
async def cmd_settext(msg: Message, cfg: Config) -> None:
    if not _is_owner(msg, cfg):
        return
    args = (msg.text or "").split(maxsplit=2)
    if len(args) < 3:
        await msg.answer("Использование: /settext @username_бота текст ответа")
        return
    _, ref, text = args
    row = await storage.find_bot(ref)
    if not row:
        await msg.answer(f"Бот {ref} не найден.")
        return
    await storage.set_reply_text(row["id"], text)
    await msg.answer(f"Готово. @{row['username']} теперь отвечает:\n{text}")


@router.message(Command("setall"))
async def cmd_setall(msg: Message, cfg: Config) -> None:
    if not _is_owner(msg, cfg):
        return
    args = (msg.text or "").split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("Использование: /setall текст ответа")
        return
    text = args[1]
    rows = await storage.list_bots()
    for row in rows:
        await storage.set_reply_text(row["id"], text)
    await msg.answer(f"Текст обновлён у {len(rows)} бот(ов):\n{text}")


@router.message(Command("remove"))
async def cmd_remove(msg: Message, cfg: Config, manager: BotManager) -> None:
    if not _is_owner(msg, cfg):
        return
    args = (msg.text or "").split(maxsplit=1)
    if len(args) < 2:
        await msg.answer("Использование: /remove @username_бота")
        return
    row = await storage.find_bot(args[1])
    if not row:
        await msg.answer(f"Бот {args[1]} не найден.")
        return
    await manager.remove(row["id"])
    await msg.answer(f"Бот @{row['username']} остановлен и удалён.")


@router.message(Command("broadcast"))
async def cmd_broadcast(msg: Message, cfg: Config, manager: BotManager) -> None:
    if not _is_owner(msg, cfg):
        return
    args = (msg.text or "").split(maxsplit=2)
    if len(args) < 3:
        await msg.answer("Использование: /broadcast @username_бота текст рассылки")
        return
    _, ref, text = args
    row = await storage.find_bot(ref)
    if not row:
        await msg.answer(f"Бот {ref} не найден.")
        return
    child_bot = manager.get_bot(row["id"])
    if not child_bot:
        await msg.answer(f"Бот @{row['username']} сейчас не запущен.")
        return

    user_ids = await storage.list_audience_ids(row["id"])
    if not user_ids:
        await msg.answer(f"У @{row['username']} пока нет накопленной аудитории.")
        return

    await msg.answer(f"Рассылаю {len(user_ids)} пользователям от @{row['username']}…")
    ok = fail = 0
    for uid in user_ids:
        try:
            await child_bot.send_message(uid, text)
            ok += 1
        except (TelegramForbiddenError, TelegramBadRequest):
            fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await msg.answer(f"Готово. Доставлено: {ok}, не доставлено: {fail}.")
