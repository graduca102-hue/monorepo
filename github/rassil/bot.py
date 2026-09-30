#!/usr/bin/env python3
"""Telegram bot interface for managing accounts, joining groups, and sending messages."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from account_manager import (
    load_accounts,
    save_state,
    assign_groups_to_accounts,
    get_accounts_summary,
    load_chats,
    add_chats,
    Account,
    SESSIONS_DIR,
    ensure_session_file,
)
from lzt_buyer import buy_accounts, search_accounts, MarketError
from session_builder import create_session_file, extract_login_data
from group_manager import join_groups_all_accounts
from message_sender import send_messages_loop, request_stop, reset_stop, is_running
import group_sizes
import proxy_manager

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
LZT_TOKEN = os.environ.get("LZT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
CONFIG_PATH = Path("config.json")

# In-memory state
accounts: list[Account] = []
telethon_clients: dict[int, object] = {}  # item_id -> TelegramClient
sending_task: asyncio.Task | None = None
joining_task: asyncio.Task | None = None
current_message: str = ""

RUNTIME_STATE = Path("runtime_state.json")


def _save_runtime():
    """Persist current_message and sending flag so they survive restarts."""
    data = {"current_message": current_message, "sending_active": is_running()}
    tmp = RUNTIME_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(RUNTIME_STATE)


def _load_runtime():
    """Load saved runtime state."""
    global current_message
    if RUNTIME_STATE.exists():
        try:
            data = json.loads(RUNTIME_STATE.read_text("utf-8"))
            current_message = data.get("current_message", "")
            return data.get("sending_active", False)
        except Exception:
            pass
    return False

# ─── Editable progress message helper ────────────────────────────────────────

MAX_LOG_LINES = 30  # keep last N lines to avoid Telegram message length limit


class ProgressMsg:
    """Accumulates log lines and edits a single Telegram message."""

    def __init__(self, bot, chat_id: int, header: str = ""):
        self._bot = bot
        self._chat_id = chat_id
        self._header = header
        self._lines: list[str] = []
        self._msg_id: int | None = None
        self._last_edit: float = 0
        self._edit_lock = asyncio.Lock()
        self._min_interval = 3.0  # seconds between edits (100+ callers share one msg)

    async def init(self) -> None:
        """Send the initial message."""
        msg = await self._bot.send_message(self._chat_id, self._header or "⏳ Начинаю...")
        self._msg_id = msg.message_id

    async def log(self, line: str) -> None:
        """Append a line and edit the message."""
        self._lines.append(line)
        if len(self._lines) > MAX_LOG_LINES:
            self._lines = self._lines[-MAX_LOG_LINES:]
        await self._edit()

    async def done(self, footer: str = "") -> None:
        """Final edit with optional footer text."""
        if footer:
            self._lines.append(footer)
        await self._edit(force=True)

    async def _edit(self, force: bool = False) -> None:
        if not self._msg_id:
            return
        # Many account coroutines call log() at once — coalesce: if an edit is
        # already in flight or the last one was < _min_interval ago, just drop
        # this refresh (the newest lines are already buffered for the next one).
        if not force:
            if self._edit_lock.locked():
                return
            if time.monotonic() - self._last_edit < self._min_interval:
                return

        async with self._edit_lock:
            text = self._header + "\n\n" + "\n".join(self._lines) if self._header else "\n".join(self._lines)
            if len(text) > 4000:
                text = text[-4000:]
            try:
                await self._bot.edit_message_text(
                    text, chat_id=self._chat_id, message_id=self._msg_id
                )
            except Exception as e:
                log.debug(f"ProgressMsg edit failed: {e}")
            self._last_edit = time.monotonic()


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text("utf-8"))
    return {
        "auto_buy": True,
        "max_price": 28,
        "currency": "rub",
        "max_items_per_run": 10,
        "accounts_dir": "accounts",
        "filters": {"country[]": ["US"], "password": "no", "spam": "no"},
    }


def _admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id if update.effective_user else 0
        if ADMIN_ID and user_id != ADMIN_ID:
            if update.callback_query:
                await update.callback_query.answer("⛔ Нет доступа", show_alert=True)
            return
        return await func(update, context)
    return wrapper


# ─── Auto-create sessions from LZT loginData ────────────────────────────────

async def _create_session_for_account(item_id: int, progress: ProgressMsg | None = None) -> tuple[bool, str | None]:
    api_id = os.environ.get("API_ID")
    api_hash = os.environ.get("API_HASH")
    if not api_id or not api_hash:
        if progress:
            await progress.log(f"❌ [{item_id}] API_ID/API_HASH не заданы")
        return False, None

    config = _load_config()
    purchase_file = Path(config.get("accounts_dir", "accounts")) / f"{item_id}.json"
    if not purchase_file.exists():
        if progress:
            await progress.log(f"❌ [{item_id}] Файл покупки не найден")
        return False, None

    try:
        pdata = json.loads(purchase_file.read_text("utf-8"))
    except Exception:
        if progress:
            await progress.log(f"❌ [{item_id}] Ошибка чтения файла покупки")
        return False, None

    login_raw = extract_login_data(pdata)
    if not login_raw:
        if progress:
            await progress.log(f"❌ [{item_id}] loginData не найден")
        return False, None

    if progress:
        await progress.log(f"🔧 [{item_id}] Создаю сессию...")

    ok, phone_or_err = await create_session_file(
        item_id, login_raw, int(api_id), api_hash, SESSIONS_DIR
    )

    if ok:
        if progress:
            phone_str = f"+{phone_or_err}" if phone_or_err else "н/д"
            await progress.log(f"✅ [{item_id}] Сессия создана | {phone_str}")
        connected = await _connect_single_client(item_id)
        if connected and progress:
            await progress.log(f"📱 [{item_id}] Клиент подключён")
    else:
        if progress:
            await progress.log(f"❌ [{item_id}] {phone_or_err}")

    return ok, phone_or_err if ok else None


# ─── Main menu ───────────────────────────────────────────────────────────────

def main_menu_kb() -> InlineKeyboardMarkup:
    sending_label = "🛑 Остановить рассылку" if is_running() else "📨 Включить рассылку"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Купить аккаунты", callback_data="buy")],
        [InlineKeyboardButton("📋 Мои аккаунты", callback_data="accounts")],
        [InlineKeyboardButton("🔗 Задать группы", callback_data="groups")],
        [InlineKeyboardButton("📊 Скан размеров групп", callback_data="scan_sizes")],
        [InlineKeyboardButton("🚪 Вступить в группы", callback_data="join")],
        [InlineKeyboardButton("✏️ Задать сообщение", callback_data="set_message")],
        [InlineKeyboardButton(sending_label, callback_data="toggle_sending")],
        [InlineKeyboardButton("🌐 Прокси", callback_data="proxy")],
        [InlineKeyboardButton("📎 Загрузить .session", callback_data="upload_session")],
        [InlineKeyboardButton("🔄 Обновить", callback_data="refresh")],
    ])


@_admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global accounts
    accounts = load_accounts()
    await update.message.reply_text(_build_status(), reply_markup=main_menu_kb())


def _build_status() -> str:
    chats = load_chats()
    ready_accounts = sum(
        1 for a in accounts if a.active and a.session_file and Path(a.session_file).exists()
    )
    lines = ["🤖 Панель управления\n"]
    lines.append(f"📊 Аккаунтов: {len(accounts)} ({sum(1 for a in accounts if a.active)} активных)")
    lines.append(f"🧩 Готовых сессий: {ready_accounts}")
    lines.append(f"📱 Клиентов подключено: {len(telethon_clients)}")
    lines.append(f"📂 Групп в chats.txt: {len(chats)}")
    total_groups = sum(len(a.assigned_groups) for a in accounts)
    total_joined = sum(len(a.joined_groups) for a in accounts)
    total_pending = sum(len(a.pending_groups) for a in accounts)
    pend = f" | заявок: {total_pending}" if total_pending else ""
    lines.append(f"🔗 Назначено: {total_groups} | вступил: {total_joined}{pend}")
    lines.append(f"📨 Рассылка: {'🟢 Активна' if is_running() else '🔴 Остановлена'}")
    joining_str = "🟢 Идёт" if joining_task and not joining_task.done() else "🔴 Нет"
    lines.append(f"🚪 Вступление: {joining_str}")
    try:
        pool = proxy_manager.load_pool()
        if pool:
            health = proxy_manager.load_health()
            alive = sum(1 for p in pool if health.get(p, {}).get("alive"))
            with_proxy = sum(1 for a in accounts if a.active and a.proxy)
            lines.append(f"🌐 Прокси: {alive}/{len(pool)} живых | у {with_proxy} акк.")
    except Exception:
        pass
    lines.append(f"✉️ Сообщение: {current_message[:50] + '...' if len(current_message) > 50 else current_message or 'не задано'}")
    return "\n".join(lines)


# ─── Callback router ────────────────────────────────────────────────────────

@_admin_only
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "buy":
        await query.edit_message_text(
            "🛒 Сколько аккаунтов купить? Отправь число (1–50):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="back")]]),
        )
        context.user_data["awaiting"] = "buy_count"

    elif data == "accounts":
        await query.edit_message_text(
            get_accounts_summary(accounts),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="back")]]),
        )

    elif data == "groups":
        chats = load_chats()
        if chats:
            preview = "\n".join(f"  • {c}" for c in chats[:30])
            if len(chats) > 30:
                preview += f"\n  ... и ещё {len(chats) - 30}"
            text = f"🔗 Группы из chats.txt ({len(chats)} шт.):\n{preview}\n\nОтправь новые ссылки — добавятся к существующим."
        else:
            text = "🔗 chats.txt пуст.\nОтправь ссылки на группы (по одной на строку)."
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Распределить по аккаунтам", callback_data="distribute_groups")],
                [InlineKeyboardButton("« Назад", callback_data="back")],
            ]),
        )
        context.user_data["awaiting"] = "groups_input"

    elif data == "distribute_groups":
        chats = load_chats()
        if not chats:
            await query.edit_message_text("❌ chats.txt пуст", reply_markup=main_menu_kb())
            return
        if not accounts:
            await query.edit_message_text("❌ Нет аккаунтов", reply_markup=main_menu_kb())
            return
        for acc in accounts:
            acc.assigned_groups = []
            acc.pending_groups = []
        assign_groups_to_accounts(accounts, chats)
        save_state(accounts)
        from account_manager import GROUPS_PER_ACCOUNT, REPEAT_MIN_SIZE
        active_n = len([a for a in accounts if a.active])
        total = sum(len(a.assigned_groups) for a in accounts)
        repeats = total - min(len(set(chats)), active_n * GROUPS_PER_ACCOUNT)
        prio = group_sizes.priority_count(chats)
        note = (
            f"\n📊 Крупные группы (≥{group_sizes.PRIORITY_THRESHOLD // 1000}к пдп) распределены первыми: {prio}"
            if prio else
            "\n⚠️ Размеры групп ещё не сканировались — нажми «📊 Скан размеров групп» для приоритета крупных."
        )
        if repeats > 0:
            note += f"\n♻️ Повторов больших групп (>{REPEAT_MIN_SIZE // 1000}к) на доп. сессии: {repeats}"
        await query.edit_message_text(
            f"✅ {len(chats)} групп → {active_n} аккаунтов × до {GROUPS_PER_ACCOUNT} "
            f"({total} назначений){note}",
            reply_markup=main_menu_kb(),
        )

    elif data == "scan_sizes":
        await _do_scan_sizes(query, context)

    elif data == "join":
        await _do_join(query, context)

    elif data == "set_message":
        await query.edit_message_text(
            "✏️ Отправь текст сообщения для рассылки:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="back")]]),
        )
        context.user_data["awaiting"] = "message_input"

    elif data == "toggle_sending":
        await _toggle_sending(query, context)

    elif data == "proxy":
        text = (
            proxy_manager.summary(accounts)
            + "\n\nОтправь список прокси (по одной на строку) — заменит пул:\n"
            "  • http://user:pass@host:port\n  • socks5://user:pass@host:port\n"
            "Или «➕ Добавить» чтобы дописать к пулу."
        )
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Проверить пул", callback_data="proxy_check")],
                [InlineKeyboardButton("🔗 Переназначить всем", callback_data="proxy_assign")],
                [InlineKeyboardButton("♻️ Переназначить битые", callback_data="proxy_reassign")],
                [InlineKeyboardButton("➕ Добавить к пулу", callback_data="proxy_add")],
                [InlineKeyboardButton("🗑 Очистить пул", callback_data="proxy_clear")],
                [InlineKeyboardButton("« Назад", callback_data="back")],
            ]),
        )
        context.user_data["awaiting"] = "proxy_input"
        context.user_data["proxy_mode"] = "replace"

    elif data == "proxy_add":
        context.user_data["awaiting"] = "proxy_input"
        context.user_data["proxy_mode"] = "add"
        await query.edit_message_text(
            "➕ Отправь прокси для добавления к пулу (по одной на строку).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="proxy")]]),
        )

    elif data == "proxy_clear":
        proxy_manager.save_pool([])
        for acc in accounts:
            acc.proxy = None
        save_state(accounts)
        await query.edit_message_text("🗑 Пул прокси очищен, аккаунты без прокси.", reply_markup=main_menu_kb())

    elif data == "proxy_check":
        chat_id = query.message.chat_id
        progress = ProgressMsg(context.bot, chat_id, "🔍 Проверка пула прокси")
        await progress.init()
        try:
            await query.edit_message_text(_build_status(), reply_markup=main_menu_kb())
        except Exception:
            pass

        async def _check_task():
            try:
                health = await proxy_manager.health_check(progress_cb=progress.log)
                alive = sum(1 for e in health.values() if e.get("alive"))
                moved = proxy_manager.reassign_dead(accounts)
                save_state(accounts)
                extra = ""
                if moved["moved"]:
                    if not is_running():
                        await _reconnect_clients(moved["item_ids"])
                        extra = f"\n♻️ {moved['moved']} акк. переведены на живые прокси (переподключены)"
                    else:
                        extra = f"\n♻️ {moved['moved']} акк. переведены на живые прокси (применится после рассылки)"
                await progress.done(f"\n✅ Живых прокси: {alive}/{len(health)}{extra}")
            except Exception as e:
                log.error(f"proxy check error: {e}", exc_info=True)
                await progress.done(f"\n❌ Ошибка: {e}")

        asyncio.create_task(_check_task())

    elif data == "proxy_assign":
        stats = proxy_manager.assign(accounts, force=True)
        save_state(accounts)
        note = ""
        if not is_running():
            note = await _reconnect_clients()
        else:
            note = "\n⚠️ Идёт рассылка — новые прокси подхватятся после её остановки/рестарта."
        await query.edit_message_text(
            f"🔗 Прокси розданы: {stats['assigned']}/{stats['accounts']} акк., "
            f"уникальных в работе {stats.get('unique_in_use', 0)} из пула {stats['pool']}.{note}",
            reply_markup=main_menu_kb(),
        )

    elif data == "proxy_reassign":
        moved = proxy_manager.reassign_dead(accounts)
        save_state(accounts)
        if moved["moved"] and not is_running():
            await _reconnect_clients(moved["item_ids"])
        await query.edit_message_text(
            f"♻️ Переведено с мёртвых прокси: {moved['moved']} акк."
            + ("" if moved["moved"] else " (нечего переносить)"),
            reply_markup=main_menu_kb(),
        )

    elif data in ("back", "refresh"):
        await query.edit_message_text(_build_status(), reply_markup=main_menu_kb())
        context.user_data.pop("awaiting", None)

    elif data == "upload_session":
        await query.edit_message_text(
            "📎 Отправь .session файлы (Telethon) в этот чат.\nИмя: item_id.session",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="back")]]),
        )


# ─── Text input handler ─────────────────────────────────────────────────────

@_admin_only
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.get("awaiting")
    text = update.message.text.strip()

    if awaiting == "buy_count":
        await _handle_buy_count(update, context, text)
    elif awaiting == "groups_input":
        await _handle_groups_input(update, context, text)
    elif awaiting == "message_input":
        await _handle_message_input(update, context, text)
    elif awaiting == "proxy_input":
        await _handle_proxy_input(update, context, text)


async def _handle_buy_count(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    global accounts
    try:
        count = int(text)
        if count < 1 or count > 200:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введи число от 1 до 200")
        return

    context.user_data.pop("awaiting", None)
    config = _load_config()

    if not LZT_TOKEN:
        await update.message.reply_text("❌ LZT_TOKEN не задан в .env")
        return

    chat_id = update.message.chat_id
    progress = ProgressMsg(context.bot, chat_id, f"🛒 Покупка {count} аккаунтов")
    await progress.init()

    try:
        bought = await buy_accounts(count, config, LZT_TOKEN, progress.log)
        accounts = load_accounts()
        save_state(accounts)

        if not bought:
            await progress.done("⚠️ Нет подходящих аккаунтов")
            return

        await progress.log(f"✅ Куплено {len(bought)}. Подключаю сессии параллельно...")

        # Parallel session creation from loginData
        authorized = 0
        lock = asyncio.Lock()

        async def _setup_one(purchase):
            nonlocal authorized
            item_id = purchase["item_id"]
            ok, phone = await _create_session_for_account(item_id, progress)
            if ok:
                async with lock:
                    authorized += 1
                    for acc in accounts:
                        if acc.item_id == item_id:
                            acc.session_file = str(SESSIONS_DIR / f"{item_id}.session")
                            acc.phone = phone
                            break

        await asyncio.gather(*[_setup_one(p) for p in bought], return_exceptions=True)

        save_state(accounts)
        await progress.done(f"\n📊 Итого: куплено {len(bought)}, подключено {authorized}")

    except MarketError as e:
        await progress.done(f"\n❌ Ошибка: {e}")


async def _handle_groups_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    context.user_data.pop("awaiting", None)
    new_links = [line.strip() for line in text.splitlines() if line.strip()]
    if not new_links:
        await update.message.reply_text("❌ Не найдено ссылок")
        return
    all_chats = add_chats(new_links)
    await update.message.reply_text(
        f"✅ +{len(new_links)} ссылок в chats.txt (всего: {len(all_chats)})\nНажми «Распределить» для назначения.",
        reply_markup=main_menu_kb(),
    )


async def _handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    global current_message
    context.user_data.pop("awaiting", None)
    current_message = text
    _save_runtime()
    await update.message.reply_text(f"✅ Сообщение задано:\n\n{current_message}", reply_markup=main_menu_kb())


async def _handle_proxy_input(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    global accounts
    context.user_data.pop("awaiting", None)
    mode = context.user_data.pop("proxy_mode", "replace")
    new_proxies = [line.strip() for line in text.splitlines() if line.strip()]
    if not new_proxies:
        await update.message.reply_text("❌ Не найдено прокси", reply_markup=main_menu_kb())
        return

    if mode == "add":
        pool = proxy_manager.add_to_pool(new_proxies)
    else:
        pool = proxy_manager.save_pool(new_proxies)

    msg = await update.message.reply_text(f"📥 Пул: {len(pool)} прокси. Проверяю...")

    async def _cb(line: str) -> None:
        return None

    health = await proxy_manager.health_check(progress_cb=_cb)
    alive = sum(1 for e in health.values() if e.get("alive"))

    stats = proxy_manager.assign(accounts, force=(mode != "add"))
    save_state(accounts)

    note = ""
    if not is_running():
        note = await _reconnect_clients()
    else:
        note = "\n⚠️ Идёт рассылка — прокси применятся после её остановки/рестарта."

    await msg.edit_text(
        f"✅ Пул: {len(pool)} ({alive} живых)\n"
        f"🔗 Роздано {stats['assigned']}/{stats['accounts']} акк., "
        f"уникальных прокси в работе: {stats.get('unique_in_use', 0)}{note}"
    )
    await update.message.reply_text("Готово.", reply_markup=main_menu_kb())


async def _reconnect_clients(item_ids: list[int] | None = None, force: bool = False) -> str:
    """Reconnect currently-connected Telethon clients using their account's
    current proxy. Safe only when a sending loop is NOT running — pass
    ``force=True`` from the pre-send proxy prep, which runs before the loop
    starts."""
    if is_running() and not force:
        return "\n⚠️ Рассылка активна — переподключение отложено."
    targets = list(telethon_clients.keys()) if item_ids is None else [
        i for i in item_ids if i in telethon_clients
    ]
    if not targets:
        return ""

    RECONNECT_CONCURRENCY = 20
    sem = asyncio.Semaphore(RECONNECT_CONCURRENCY)

    async def _one(item_id: int) -> bool:
        async with sem:
            client = telethon_clients.pop(item_id, None)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            return await _connect_single_client(item_id)

    results = await asyncio.gather(*(_one(i) for i in targets))
    ok = sum(1 for r in results if r)
    return f"\n📱 Переподключено клиентов: {ok}/{len(targets)}"


async def _prep_proxies_for_send(progress_cb=None) -> None:
    """Before every mass-send: health-check the whole proxy pool, delete the
    dead proxies from proxies.txt, spread the surviving proxies across ALL
    active accounts, and reconnect the clients on their new proxy.

    If the check finds zero live proxies the pool is left untouched (that
    normally means the shared upstream gateway is down, not that every proxy
    died) and accounts keep their current assignment.
    """
    async def _say(line: str) -> None:
        if progress_cb:
            await progress_cb(line)
        else:
            log.info(line)

    pool = proxy_manager.load_pool()
    if not pool:
        await _say("🌐 Пул прокси пуст — рассылка пойдёт без прокси")
        return

    await _say(f"🌐 Проверяю {len(pool)} прокси перед рассылкой…")
    health = await proxy_manager.health_check(pool, progress_cb=progress_cb)
    alive = [p for p in pool if health.get(p, {}).get("alive")]

    if not alive:
        await _say("⚠️ Ни один прокси не ответил — пул не трогаю, оставляю текущее распределение")
        return

    pruned = proxy_manager.prune_dead()
    if pruned["removed"]:
        await _say(f"🗑 Удалил {pruned['removed']} мёртвых прокси, живых осталось {pruned['kept']}")

    stats = proxy_manager.assign(accounts, force=True)
    save_state(accounts)
    await _say(
        f"🔗 Прокси распределены: {stats['assigned']}/{stats['accounts']} акк., "
        f"{stats.get('unique_in_use', 0)} уникальных"
    )

    tail = await _reconnect_clients(force=True)
    if tail:
        await _say(tail.strip())


# ─── Scan group sizes (non-blocking) ───────────────────────────────────────

async def _do_scan_sizes(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    chats = load_chats()
    if not chats:
        await query.edit_message_text("❌ chats.txt пуст", reply_markup=main_menu_kb())
        return
    if not telethon_clients:
        await query.edit_message_text("⚠️ Telethon-клиенты не подключены", reply_markup=main_menu_kb())
        return

    stale = sum(1 for c in chats if group_sizes.is_stale(c))
    chat_id = query.message.chat_id
    progress = ProgressMsg(context.bot, chat_id, f"📊 Скан размеров групп ({stale} новых из {len(chats)})")
    await progress.init()

    try:
        await query.edit_message_text(_build_status(), reply_markup=main_menu_kb())
    except Exception:
        pass

    async def _scan_task():
        try:
            summary = await group_sizes.scan_sizes(
                list(telethon_clients.values()), chats, progress.log, only_stale=True
            )
            top = "\n".join(
                f"  • {n:,} — {g}".replace(",", " ") for n, g in summary.get("top", [])
            )
            await progress.done(
                f"\n✅ Просканировано: {summary['scanned']}, "
                f"размер получен: {summary.get('resolved', 0)}\n"
                f"📊 Крупных (≥{group_sizes.PRIORITY_THRESHOLD // 1000}к пдп): "
                f"{summary['priority']} из {summary['total']}\n"
                f"Топ:\n{top}\n\n"
                f"Теперь «🔄 Распределить по аккаунтам» отдаст слоты крупным группам."
            )
        except Exception as e:
            log.error(f"scan_sizes task error: {e}", exc_info=True)
            await progress.done(f"\n❌ Ошибка: {e}")

    asyncio.create_task(_scan_task())


# ─── Join groups (non-blocking) ─────────────────────────────────────────────

async def _do_join(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    global joining_task

    if joining_task and not joining_task.done():
        await query.edit_message_text("⚠️ Вступление уже идёт", reply_markup=main_menu_kb())
        return
    if not accounts:
        await query.edit_message_text("❌ Нет аккаунтов", reply_markup=main_menu_kb())
        return
    if not telethon_clients:
        await query.edit_message_text("⚠️ Telethon-клиенты не подключены", reply_markup=main_menu_kb())
        return

    chat_id = query.message.chat_id
    progress = ProgressMsg(context.bot, chat_id, "🚪 Вступление в группы")
    await progress.init()

    # Return to menu — ignore "not modified" error
    try:
        await query.edit_message_text(_build_status(), reply_markup=main_menu_kb())
    except Exception:
        pass

    async def _join_task():
        try:
            log.info(f"Join task started: {len(accounts)} accounts, {len(telethon_clients)} clients")
            await join_groups_all_accounts(telethon_clients, accounts, progress.log)
            save_state(accounts)
            await progress.done("\n✅ Вступление завершено!")
        except Exception as e:
            log.error(f"Join task error: {e}", exc_info=True)
            await progress.done(f"\n❌ Ошибка: {e}")

    joining_task = asyncio.create_task(_join_task())


# ─── Toggle sending (non-blocking) ──────────────────────────────────────────

async def _toggle_sending(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    global sending_task

    if is_running():
        request_stop()
        if sending_task and not sending_task.done():
            sending_task.cancel()
        sending_task = None
        _save_runtime()
        await query.edit_message_text("🛑 Рассылка остановлена", reply_markup=main_menu_kb())
        return

    if not current_message:
        await query.edit_message_text("❌ Сначала задай сообщение", reply_markup=main_menu_kb())
        return
    if not telethon_clients:
        await query.edit_message_text("⚠️ Telethon-клиенты не подключены", reply_markup=main_menu_kb())
        return

    chat_id = query.message.chat_id
    progress = ProgressMsg(context.bot, chat_id, "📨 Рассылка")
    await progress.init()

    # Return to menu — ignore "not modified" error
    try:
        await query.edit_message_text(_build_status(), reply_markup=main_menu_kb())
    except Exception:
        pass

    async def _send_task():
        try:
            try:
                await _prep_proxies_for_send(progress.log)
            except Exception as e:
                log.error(f"proxy prep failed: {e}", exc_info=True)
                await progress.log(f"⚠️ Проверка прокси не удалась ({e}) — рассылаю с текущими прокси")
            await send_messages_loop(telethon_clients, accounts, current_message, progress.log)
        except asyncio.CancelledError:
            await progress.done("\n🛑 Рассылка остановлена")
        except Exception as e:
            await progress.done(f"\n❌ Ошибка: {e}")

    reset_stop()  # so _save_runtime() below sees is_running() == True immediately
    sending_task = asyncio.create_task(_send_task())
    _save_runtime()


# ─── Telethon client init ───────────────────────────────────────────────────

async def init_telethon_clients() -> None:
    global telethon_clients

    api_id = os.environ.get("API_ID")
    api_hash = os.environ.get("API_HASH")
    if not api_id or not api_hash:
        log.warning("API_ID / API_HASH not set")
        return

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    from telethon import TelegramClient
    import socks

    PROXY_TYPES = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP, "https": socks.HTTP}

    def _parse_proxy(proxy_str: str):
        from urllib.parse import urlparse
        p = urlparse(proxy_str)
        scheme = (p.scheme or "socks5").lower()
        return (PROXY_TYPES.get(scheme, socks.SOCKS5), p.hostname, p.port, True, p.username, p.password)

    CONNECT_CONCURRENCY = 20  # parallel connects, not one-by-one
    CONNECT_TIMEOUT = 30  # seconds — a dead proxy/account must not stall the whole fleet
    connect_sem = asyncio.Semaphore(CONNECT_CONCURRENCY)

    async def _init_one(acc):
        session_file = ensure_session_file(acc.item_id)
        if session_file is None:
            return
        session_path = session_file.with_suffix("")

        proxy = None
        if acc.proxy:
            try:
                proxy = _parse_proxy(acc.proxy)
            except Exception as e:
                log.warning(f"Bad proxy for {acc.item_id}: {e}")

        client = TelegramClient(
            str(session_path), int(api_id), api_hash,
            proxy=proxy,
            receive_updates=False,  # don't listen for updates — saves CPU/network
        )

        async with connect_sem:
            try:
                await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
                if await client.is_user_authorized():
                    telethon_clients[acc.item_id] = client
                    acc.session_file = str(session_file)
                    log.info(f"✅ Telethon client connected: {acc.item_id}")
                else:
                    log.warning(f"⚠️ Not authorized: {acc.item_id}")
                    await client.disconnect()
            except asyncio.TimeoutError:
                log.error(f"❌ Connect timeout for {acc.item_id} (dead proxy?)")
                try:
                    await client.disconnect()
                except Exception:
                    pass
            except Exception as e:
                log.error(f"❌ Failed to connect {acc.item_id}: {e}")

    await asyncio.gather(*(_init_one(acc) for acc in accounts))

    save_state(accounts)
    log.info(f"Telethon: {len(telethon_clients)} clients connected")


async def shutdown_telethon() -> None:
    for client in telethon_clients.values():
        try:
            await client.disconnect()
        except Exception:
            pass
    telethon_clients.clear()


# ─── Upload .session files ───────────────────────────────────────────────────

@_admin_only
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global accounts

    doc = update.message.document
    if not doc or not doc.file_name:
        return

    fname = doc.file_name

    # ─── .txt file → add chats ──────────────────────────────────────────
    if fname.endswith(".txt"):
        tg_file = await doc.get_file()
        raw = await tg_file.download_as_bytearray()
        text = raw.decode("utf-8", errors="ignore")
        new_links = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
        if not new_links:
            await update.message.reply_text("❌ Файл пуст или нет ссылок")
            return
        all_chats = add_chats(new_links)
        await update.message.reply_text(
            f"✅ +{len(new_links)} чатов из {fname} (всего: {len(all_chats)})",
            reply_markup=main_menu_kb(),
        )
        return

    # ─── .session file → connect account ─────────────────────────────────
    if not fname.endswith(".session"):
        await update.message.reply_text("⚠️ Поддерживаемые форматы: .txt (чаты) или .session (Telethon)")
        return

    fname = doc.file_name
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    tg_file = await doc.get_file()
    dest = SESSIONS_DIR / fname
    await tg_file.download_to_drive(str(dest))

    stem = fname.replace(".session", "")
    try:
        item_id = int(stem)
    except ValueError:
        item_id = None

    api_id = os.environ.get("API_ID")
    api_hash = os.environ.get("API_HASH")
    if not api_id or not api_hash:
        await update.message.reply_text(f"✅ Сохранён: {dest}\n⚠️ API_ID/API_HASH не заданы")
        return

    from telethon import TelegramClient
    session_path = SESSIONS_DIR / stem
    client = TelegramClient(str(session_path), int(api_id), api_hash, receive_updates=False)

    try:
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            phone = me.phone if me else None
            uid = item_id if item_id is not None else (me.id if me else hash(fname))
            telethon_clients[uid] = client

            found = False
            for acc in accounts:
                if acc.item_id == uid:
                    acc.session_file = str(dest)
                    acc.phone = phone
                    acc.active = True
                    found = True
                    break
            if not found:
                accounts.append(Account(item_id=uid, phone=phone, session_file=str(dest), active=True))

            save_state(accounts)
            await update.message.reply_text(
                f"✅ Подключён! Тел: +{phone or 'н/д'} | Клиентов: {len(telethon_clients)}",
                reply_markup=main_menu_kb(),
            )
        else:
            await client.disconnect()
            await update.message.reply_text("⚠️ Сессия не авторизована", reply_markup=main_menu_kb())
    except Exception as e:
        try:
            await client.disconnect()
        except Exception:
            pass
        await update.message.reply_text(f"❌ Ошибка: {e}", reply_markup=main_menu_kb())


async def _connect_single_client(item_id: int) -> bool:
    global telethon_clients
    api_id = os.environ.get("API_ID")
    api_hash = os.environ.get("API_HASH")
    if not api_id or not api_hash:
        return False

    session_file = ensure_session_file(item_id)
    if session_file is None:
        return False
    session_path = session_file.with_suffix("")

    from telethon import TelegramClient

    proxy = None
    for acc in accounts:
        if acc.item_id == item_id and acc.proxy:
            try:
                import socks
                from urllib.parse import urlparse
                p = urlparse(acc.proxy)
                scheme = (p.scheme or "socks5").lower()
                types = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP, "https": socks.HTTP}
                proxy = (types.get(scheme, socks.SOCKS5), p.hostname, p.port, True, p.username, p.password)
            except Exception:
                pass
            break

    try:
        client = TelegramClient(str(session_path), int(api_id), api_hash, proxy=proxy, receive_updates=False)
        await asyncio.wait_for(client.connect(), timeout=30)
        if await client.is_user_authorized():
            telethon_clients[item_id] = client
            return True
        await client.disconnect()
    except Exception:
        pass
    return False


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    global accounts

    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не задан в .env")
        return

    accounts = load_accounts()
    save_state(accounts)

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))

    async def _proxy_watch():
        """Every 10 min: re-check the proxy pool, move accounts off dead proxies,
        and (when not sending) reconnect the affected clients."""
        while True:
            await asyncio.sleep(600)
            try:
                if not proxy_manager.load_pool():
                    continue
                await proxy_manager.health_check()
                moved = proxy_manager.reassign_dead(accounts)
                if moved["moved"]:
                    save_state(accounts)
                    log.warning(f"proxy_watch: moved {moved['moved']} accounts off dead proxies")
                    if not is_running():
                        await _reconnect_clients(moved["item_ids"])
            except Exception as e:
                log.error(f"proxy_watch error: {e}")

    async def post_init(application):
        await init_telethon_clients()
        asyncio.create_task(_proxy_watch())
        # Resume sending if it was active before restart
        was_sending = _load_runtime()
        if was_sending and current_message and telethon_clients:
            global sending_task
            log.info("Resuming sending after restart...")

            async def _resumed_send():
                try:
                    try:
                        await _prep_proxies_for_send()
                    except Exception as e:
                        log.error(f"proxy prep failed: {e}", exc_info=True)
                    await send_messages_loop(telethon_clients, accounts, current_message)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    log.error(f"Resumed sending error: {e}")

            reset_stop()  # so _save_runtime() below sees is_running() == True immediately
            sending_task = asyncio.create_task(_resumed_send())
            _save_runtime()
            log.info("Sending resumed")

    async def post_shutdown(application):
        request_stop()
        await shutdown_telethon()

    app.post_init = post_init
    app.post_shutdown = post_shutdown

    log.info("Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
