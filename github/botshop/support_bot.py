import asyncio
import html
import json
import logging
import os
import random
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

TOKEN = (os.getenv("SUPPORT_TOKEN") or "").strip()
SUPPORT_ID = int((os.getenv("support_id") or os.getenv("SUPPORT_ID") or "0").strip())
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data1.db")))

NORMAL_MODE_NOTICE = (
    "Тех поддержка бота @MarketPlaceABot перевёлся в обычный режим без тем, "
    "возможно вы не разобрались в интерфейсе, и ваш вопрос не решён, если это так, "
    "то отпишите ещё раз, всего хорошего"
)
NORMAL_MODE_NOTICE_KEY = "normal_mode_2026_08_17"

FRUITS = ("🍏", "🍎", "🍐", "🍊", "🍋", "🍌", "🍉", "🍇", "🍓", "🫐", "🍒", "🥝", "🍍", "🥭", "🍑")
CATEGORIES = {
    "order": ("📦 Не валидный / Не рабочий товар / Проблемы со входом", True),
    "bug": ("🐞 Баг / Ошибка", False),
    "ads": ("📣 Реклама", False),
    "idea": ("💡 Предложить улучшение сервиса", False),
    "proxy": ("🛡 Проблема с прокси", False),
    "cooperation": ("🤝 Сотрудничество", False),
    "other": ("💬 Другое", False),
}
router = Router()


class ClosingConnection(sqlite3.Connection):
    """Commit/rollback and release the SQLite handle after a with block."""

    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=30, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    return db


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS support_users (
                user_id INTEGER PRIMARY KEY,
                captcha_passed INTEGER NOT NULL DEFAULT 0,
                captcha_target TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS support_sessions (
                user_id INTEGER PRIMARY KEY,
                state TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                order_id INTEGER,
                user_thread_id INTEGER,
                support_thread_id INTEGER,
                status TEXT NOT NULL DEFAULT 'open',
                description TEXT NOT NULL,
                created_at TEXT NOT NULL,
                closed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_support_ticket_user_thread
                ON support_tickets(user_id, user_thread_id, status);
            CREATE INDEX IF NOT EXISTS idx_support_ticket_admin_thread
                ON support_tickets(support_thread_id, status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_support_one_open_ticket_per_user
                ON support_tickets(user_id) WHERE status = 'open';
            CREATE TABLE IF NOT EXISTS support_refunds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL UNIQUE,
                buyer_amount REAL NOT NULL,
                referral_amount REAL NOT NULL DEFAULT 0,
                partner_amount REAL NOT NULL DEFAULT 0,
                operator_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS support_partial_refunds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL,
                order_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL,
                buyer_amount REAL NOT NULL,
                referral_amount REAL NOT NULL DEFAULT 0,
                partner_amount REAL NOT NULL DEFAULT 0,
                operator_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS support_notices (
                notice_key TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (notice_key, user_id)
            );
            """
        )


def support_user_ids() -> list[int]:
    """Return every customer who has ever interacted with the support bot."""
    with connect() as db:
        rows = db.execute(
            "SELECT user_id FROM support_users "
            "UNION SELECT user_id FROM support_tickets "
            "ORDER BY user_id"
        ).fetchall()
    return [int(row[0]) for row in rows if int(row[0]) != SUPPORT_ID]


def pending_notice_user_ids(notice_key: str) -> list[int]:
    with connect() as db:
        rows = db.execute(
            "SELECT user_id FROM ("
            "SELECT user_id FROM support_users UNION SELECT user_id FROM support_tickets"
            ") AS users WHERE user_id != ? AND NOT EXISTS ("
            "SELECT 1 FROM support_notices notices "
            "WHERE notices.notice_key=? AND notices.user_id=users.user_id"
            ") ORDER BY user_id",
            (SUPPORT_ID, notice_key),
        ).fetchall()
    return [int(row[0]) for row in rows]


def mark_notice_sent(notice_key: str, user_id: int) -> None:
    with connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO support_notices(notice_key,user_id,sent_at) VALUES(?,?,?)",
            (notice_key, user_id, now()),
        )


def customer_topic_rows() -> list[tuple[int, int]]:
    """Topics created by the old customer-side threaded implementation."""
    with connect() as db:
        rows = db.execute(
            "SELECT DISTINCT user_id,user_thread_id FROM support_tickets "
            "WHERE user_thread_id IS NOT NULL"
        ).fetchall()
    return [(int(row[0]), int(row[1])) for row in rows]


async def migrate_customers_to_normal_mode(bot: Bot) -> tuple[int, int]:
    """Remove old customer topics and keep existing tickets open in the main chat."""
    removed = 0
    failed = 0
    for user_id, thread_id in customer_topic_rows():
        try:
            await bot.delete_forum_topic(chat_id=user_id, message_thread_id=thread_id)
            removed += 1
        except (TelegramBadRequest, TelegramForbiddenError):
            # The user may have deleted the topic/chat already. Clearing the DB
            # mapping is still safe because routing now relies only on user_id.
            failed += 1
        await asyncio.sleep(0.04)
    with connect() as db:
        db.execute("UPDATE support_tickets SET user_thread_id=NULL WHERE user_thread_id IS NOT NULL")
    return removed, failed


async def broadcast_normal_mode_notice(bot: Bot) -> tuple[int, int]:
    delivered = 0
    failed = 0
    for user_id in pending_notice_user_ids(NORMAL_MODE_NOTICE_KEY):
        while True:
            try:
                await bot.send_message(user_id, NORMAL_MODE_NOTICE)
                mark_notice_sent(NORMAL_MODE_NOTICE_KEY, user_id)
                delivered += 1
                break
            except TelegramRetryAfter as error:
                await asyncio.sleep(error.retry_after + 1)
            except (TelegramBadRequest, TelegramForbiddenError):
                failed += 1
                break
        await asyncio.sleep(0.04)
    return delivered, failed


def set_session(user_id: int, state: str, payload: dict | None = None) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO support_sessions(user_id,state,payload,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET state=excluded.state,payload=excluded.payload,updated_at=excluded.updated_at",
            (user_id, state, json.dumps(payload or {}, ensure_ascii=False), now()),
        )


def get_session(user_id: int) -> tuple[str, dict]:
    with connect() as db:
        row = db.execute("SELECT state,payload FROM support_sessions WHERE user_id=?", (user_id,)).fetchone()
    return (row["state"], json.loads(row["payload"] or "{}")) if row else ("", {})


def menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=CATEGORIES["order"][0], callback_data="support:new:order")],
        [InlineKeyboardButton(text=CATEGORIES["bug"][0], callback_data="support:new:bug")],
        [InlineKeyboardButton(text=CATEGORIES["ads"][0], callback_data="support:new:ads")],
        [InlineKeyboardButton(text=CATEGORIES["idea"][0], callback_data="support:new:idea")],
        [InlineKeyboardButton(text=CATEGORIES["proxy"][0], callback_data="support:new:proxy")],
        [InlineKeyboardButton(text=CATEGORIES["cooperation"][0], callback_data="support:new:cooperation")],
        [InlineKeyboardButton(text=CATEGORIES["other"][0], callback_data="support:new:other")],
    ])


async def show_menu(message: Message) -> None:
    set_session(message.chat.id, "menu")
    await message.answer(
        "✨ <b>Премиум-поддержка</b>\n\n🎯 Выберите цель создания тикета:\n"
        "После создания обращения просто продолжайте писать в этом чате.",
        reply_markup=menu_keyboard(), parse_mode="HTML",
    )


async def show_captcha(message: Message) -> None:
    choices = random.sample(FRUITS, 6)
    target = random.choice(choices)
    with connect() as db:
        db.execute(
            "INSERT INTO support_users(user_id,captcha_target,created_at,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET captcha_target=excluded.captcha_target,updated_at=excluded.updated_at",
            (message.chat.id, target, now(), now()),
        )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=x, callback_data=f"captcha:{x}") for x in choices[:3]],
        [InlineKeyboardButton(text=x, callback_data=f"captcha:{x}") for x in choices[3:]],
    ])
    await message.answer(
        f"🛡 <b>Быстрая проверка</b>\n\nВыберите этот фрукт: <b>{target}</b>\n\n"
        "🍓 Проверка нужна только при первом входе.", reply_markup=keyboard, parse_mode="HTML",
    )


@router.message(CommandStart(), F.chat.type == "private")
async def start(message: Message) -> None:
    if message.from_user.id == SUPPORT_ID:
        await message.answer("🛠 <b>Панель оператора готова.</b>\nНовые обращения появятся отдельными тредами.", parse_mode="HTML")
        return
    with connect() as db:
        row = db.execute("SELECT captcha_passed FROM support_users WHERE user_id=?", (message.from_user.id,)).fetchone()
    if not row or not row["captcha_passed"]:
        await show_captcha(message)
    else:
        await show_menu(message)


@router.callback_query(F.data.startswith("captcha:"))
async def captcha(callback: CallbackQuery) -> None:
    if callback.from_user.id == SUPPORT_ID:
        return
    picked = callback.data.split(":", 1)[1]
    with connect() as db:
        row = db.execute("SELECT captcha_target,captcha_passed FROM support_users WHERE user_id=?", (callback.from_user.id,)).fetchone()
        if row and row["captcha_passed"]:
            await callback.answer("Проверка уже пройдена ✅")
            return
        if not row or picked != row["captcha_target"]:
            await callback.answer("Не тот фрукт. Попробуйте ещё раз 🍋", show_alert=True)
            return
        db.execute("UPDATE support_users SET captcha_passed=1,captcha_target=NULL,updated_at=? WHERE user_id=?", (now(), callback.from_user.id))
    await callback.answer("Проверка пройдена ✅")
    await callback.message.edit_text("✅ <b>Готово!</b> Вы подтвердили, что вы человек.", parse_mode="HTML")
    await show_menu(callback.message)


@router.callback_query(F.data.startswith("support:new:"))
async def choose_category(callback: CallbackQuery) -> None:
    category = callback.data.rsplit(":", 1)[1]
    if category not in CATEGORIES or callback.from_user.id == SUPPORT_ID:
        await callback.answer()
        return
    set_session(callback.from_user.id, "description", {"category": category})
    prompts = {
        "order": "📦 <b>Проблема с товаром</b>\n\nОтправьте одним сообщением:\n• номер заказа из раздела «Мои заказы»;\n• скриншот или файл (можно следующим сообщением);\n• что именно не работает или почему товар невалидный.",
        "bug": "🐞 <b>Баг / ошибка</b>\n\nОпишите, что произошло, что ожидалось и как повторить ошибку. Скриншот приветствуется.",
        "ads": "📣 <b>Реклама</b>\n\nЧто рекламируете и где хотите разместить рекламу? Добавьте ссылку и формат.",
        "idea": "💡 <b>Улучшение сервиса</b>\n\nРасскажите, что предлагаете изменить и какую проблему это решит.",
        "proxy": "🛡 <b>Проблема с прокси</b>\n\nУкажите user ID/номер заказа, тип прокси и подробно опишите проблему. Приложите скриншот или тестовую строку без пароля.",
        "cooperation": "🤝 <b>Сотрудничество</b>\n\nОпишите ваше предложение, формат сотрудничества и оставьте контакт для связи.",
        "other": "💬 <b>Другое</b>\n\nОпишите ваш вопрос или проблему. При необходимости приложите скриншот или файл.",
    }
    await callback.answer()
    await callback.message.edit_text(prompts[category], parse_mode="HTML")


def find_order(user_id: int, text: str) -> dict | None:
    tokens = set(re.findall(r"[A-Za-z0-9_-]{3,}", text))
    with connect() as db:
        for token in sorted(tokens, key=len, reverse=True):
            row = db.execute(
                "SELECT * FROM orders WHERE user_id=? AND (supplier_order_number=? OR supplier_order_uuid=? OR CAST(id AS TEXT)=?) ORDER BY id DESC LIMIT 1",
                (user_id, token, token, token.lstrip("#")),
            ).fetchone()
            if row:
                return dict(row)
    return None


def order_number(order: dict | None) -> str:
    if not order:
        return "—"
    supplier_number = str(order.get("supplier_order_number") or "").strip()
    return supplier_number or f"#{int(order['id'])}"


async def create_topic(bot: Bot, chat_id: int, name: str) -> int | None:
    try:
        topic = await bot.create_forum_topic(chat_id=chat_id, name=name[:128])
        return topic.message_thread_id
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        logging.warning("Cannot create topic in chat %s: %s", chat_id, error)
        return None


async def copy_into(bot: Bot, chat_id: int, source: Message, thread_id: int | None = None, markup=None) -> None:
    try:
        await bot.copy_message(chat_id=chat_id, from_chat_id=source.chat.id, message_id=source.message_id,
                               message_thread_id=thread_id, reply_markup=markup)
    except TelegramBadRequest:
        await bot.send_message(chat_id, source.text or source.caption or "📎 Вложение", message_thread_id=thread_id, reply_markup=markup)


@router.message(F.chat.type == "private")
async def private_message(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    if user_id == SUPPORT_ID:
        state, payload = get_session(user_id)
        if state == "partial_refund_quantity" and int(payload.get("thread_id") or 0) == int(message.message_thread_id or 0):
            await process_partial_refund_quantity(message, bot, payload)
            return
        ticket = ticket_for_admin_thread(message.message_thread_id)
        if ticket:
            await relay_to_user(bot, ticket, message)
        return

    # Customers use one ordinary private chat. The operator side keeps a
    # separate Telegram topic for every ticket.
    ticket = ticket_for_user(user_id)
    if ticket:
        await relay_to_admin(bot, ticket, message)
        return

    state, payload = get_session(user_id)
    if state != "description":
        await show_menu(message)
        return
    category = payload.get("category")
    order = find_order(user_id, message.text or message.caption or "") if category == "order" else None
    if category == "order" and not order:
        await message.answer("⚠️ Не нашёл этот заказ в вашем профиле. Проверьте номер в разделе «Мои заказы» и отправьте описание ещё раз.")
        return
    await open_ticket(bot, message, category, order)


async def open_ticket(bot: Bot, source: Message, category: str, order: dict | None) -> None:
    with connect() as db:
        existing = db.execute(
            "SELECT * FROM support_tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (source.from_user.id,),
        ).fetchone()
        if existing:
            await source.answer(
                f"⚠️ У вас уже открыт тикет #{existing['id']}. Закройте его перед созданием нового.",
            )
            return
        ticket_id = db.execute(
            "INSERT INTO support_tickets(user_id,category,order_id,description,created_at) VALUES(?,?,?,?,?)",
            (source.from_user.id, category, order.get("id") if order else None, source.text or source.caption or "Вложение", now()),
        ).lastrowid
    support_thread = await create_topic(bot, SUPPORT_ID, f"#{ticket_id} · {source.from_user.full_name} · {CATEGORIES[category][0]}")
    with connect() as db:
        db.execute("UPDATE support_tickets SET support_thread_id=? WHERE id=?", (support_thread, ticket_id))
    set_session(source.from_user.id, "menu")
    admin_buttons = [[InlineKeyboardButton(text="✅ Закрыть тикет", callback_data=f"support:close:{ticket_id}")]]
    if order:
        admin_buttons.insert(0, [InlineKeyboardButton(text=f"💸 Вернуть {float(order['total_price']):.2f} $", callback_data=f"support:refund:{ticket_id}")])
        if int(order.get("quantity") or 0) > 1:
            admin_buttons.insert(1, [InlineKeyboardButton(text="↩️ Частичный возврат", callback_data=f"support:partial_refund:{ticket_id}")])
    header = (
        f"🎫 <b>Тикет #{ticket_id}</b>\n{CATEGORIES[category][0]}\n\n"
        f"👤 <code>{source.from_user.id}</code> · {html.escape(source.from_user.full_name)}\n"
        f"🧾 Заказ: <code>{order_number(order)}</code>\n"
        f"💰 Сумма: {float(order['total_price']):.2f} $" if order else
        f"🎫 <b>Тикет #{ticket_id}</b>\n{CATEGORIES[category][0]}\n\n👤 <code>{source.from_user.id}</code> · {html.escape(source.from_user.full_name)}"
    )
    await bot.send_message(SUPPORT_ID, header, message_thread_id=support_thread, parse_mode="HTML",
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=admin_buttons))
    await copy_into(bot, SUPPORT_ID, source, support_thread)
    await bot.send_message(source.from_user.id,
                           f"✨ <b>Тикет #{ticket_id} создан</b>\n\nПродолжайте писать в этом чате — оператор увидит все сообщения и вложения.",
                           parse_mode="HTML")


def ticket_for_user(user_id: int) -> dict | None:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM support_tickets WHERE user_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def closed_ticket_for_user_thread(user_id: int, thread_id: int | None) -> dict | None:
    if not thread_id:
        return None
    with connect() as db:
        row = db.execute(
            "SELECT * FROM support_tickets WHERE user_id=? AND user_thread_id=? AND status='closed' ORDER BY id DESC LIMIT 1",
            (user_id, thread_id),
        ).fetchone()
    return dict(row) if row else None


def ticket_for_admin_thread(thread_id: int | None) -> dict | None:
    if not thread_id:
        return None
    with connect() as db:
        row = db.execute("SELECT * FROM support_tickets WHERE support_thread_id=? AND status='open'", (thread_id,)).fetchone()
    return dict(row) if row else None


async def relay_to_admin(bot: Bot, ticket: dict, message: Message) -> None:
    await copy_into(bot, SUPPORT_ID, message, ticket.get("support_thread_id"))


async def relay_to_user(bot: Bot, ticket: dict, message: Message) -> None:
    await copy_into(bot, ticket["user_id"], message)


def calculate_referral(cursor: sqlite3.Cursor, order: sqlite3.Row) -> tuple[int | None, float]:
    ref = cursor.execute("SELECT referred_by FROM users WHERE user_id=?", (order["user_id"],)).fetchone()
    if not ref or ref["referred_by"] is None:
        return None, 0.0
    percent = 25.0
    if order["partner_bot_id"]:
        partner = cursor.execute("SELECT referral_enabled,referral_percent FROM partners_bots WHERE id=?", (order["partner_bot_id"],)).fetchone()
        if not partner or not partner["referral_enabled"]:
            return None, 0.0
        override = cursor.execute("SELECT referral_percent FROM partner_referral_whitelist WHERE partner_bot_id=? AND user_id=?", (order["partner_bot_id"], ref["referred_by"])).fetchone()
        percent = float(override[0] if override else partner["referral_percent"] or 0)
        base = max(float(order["partner_profit_amount"] or 0), 0)
    else:
        base = max(float(order["total_price"] or 0) - float(order["purchase_unit_price"] or 0) * int(order["quantity"] or 0), 0)
    return int(ref["referred_by"]), round(base * max(percent, 0) / 100, 8)


def refund_order(ticket_id: int, operator_id: int) -> tuple[dict | None, str]:
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        ticket = db.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()
        if not ticket or not ticket["order_id"]:
            return None, "К тикету не привязан заказ"
        order = db.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (ticket["order_id"], ticket["user_id"])).fetchone()
        if not order:
            return None, "Заказ не найден"
        if db.execute("SELECT 1 FROM support_refunds WHERE order_id=?", (order["id"],)).fetchone():
            return dict(order), "Возврат по этому заказу уже выполнен"
        if order["status"] != "delivered":
            return None, f"Возврат разрешён только для выданного заказа (сейчас: {order['status']})"
        partial = db.execute(
            "SELECT COALESCE(SUM(quantity),0),COALESCE(SUM(buyer_amount),0),"
            "COALESCE(SUM(referral_amount),0),COALESCE(SUM(partner_amount),0) "
            "FROM support_partial_refunds WHERE order_id=?", (order["id"],)
        ).fetchone()
        buyer_amount = max(round(float(order["total_price"] or 0) - float(partial[1] or 0), 2), 0.0)
        referrer_id, full_referral_amount = calculate_referral(db.cursor(), order)
        referral_amount = max(round(full_referral_amount - float(partial[2] or 0), 8), 0.0)
        full_partner_amount = float(order["partner_profit_amount"] or 0) if order["partner_bot_id"] and order["partner_profit_accrued"] else 0.0
        partner_amount = max(round(full_partner_amount - float(partial[3] or 0), 8), 0.0)
        db.execute("UPDATE users SET balance=balance+?,purchases_count=MAX(purchases_count-1,0),purchases_total=MAX(purchases_total-?,0) WHERE user_id=?",
                   (buyer_amount, buyer_amount, order["user_id"]))
        if referrer_id and referral_amount:
            db.execute("UPDATE users SET balance=balance-?,referral_earnings=MAX(referral_earnings-?,0) WHERE user_id=?",
                       (referral_amount, referral_amount, referrer_id))
        if partner_amount:
            db.execute("UPDATE partners_bots SET partner_earnings=partner_earnings-?,updated_at=? WHERE id=?",
                       (partner_amount, now(), order["partner_bot_id"]))
        db.execute("UPDATE orders SET status='refunded',updated_at=? WHERE id=?", (now(), order["id"]))
        db.execute("INSERT INTO support_refunds(ticket_id,order_id,buyer_amount,referral_amount,partner_amount,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
                   (ticket_id, order["id"], buyer_amount, referral_amount, partner_amount, operator_id, now()))
        db.commit()
        result = dict(order)
        result.update(referral_amount=referral_amount, partner_amount=partner_amount, buyer_amount=buyer_amount)
        return result, "ok"
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def refund_order_quantity(ticket_id: int, operator_id: int, refund_quantity: int) -> tuple[dict | None, str]:
    db = connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        ticket = db.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()
        if not ticket or not ticket["order_id"]:
            return None, "К тикету не привязан заказ"
        order = db.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (ticket["order_id"], ticket["user_id"])).fetchone()
        if not order or order["status"] != "delivered":
            return None, "Возврат доступен только для выданного заказа"
        total_quantity = max(int(order["quantity"] or 0), 0)
        refunded_quantity = int(db.execute(
            "SELECT COALESCE(SUM(quantity),0) FROM support_partial_refunds WHERE order_id=?", (order["id"],)
        ).fetchone()[0] or 0)
        remaining_quantity = total_quantity - refunded_quantity
        quantity = int(refund_quantity)
        if quantity <= 0 or quantity > remaining_quantity:
            return None, f"Можно вернуть от 1 до {remaining_quantity} шт."
        ratio = quantity / max(total_quantity, 1)
        buyer_amount = round(float(order["total_price"] or 0) * ratio, 2)
        referrer_id, full_referral = calculate_referral(db.cursor(), order)
        referral_amount = round(full_referral * ratio, 8)
        full_partner = float(order["partner_profit_amount"] or 0) if order["partner_bot_id"] and order["partner_profit_accrued"] else 0.0
        partner_amount = round(full_partner * ratio, 8)
        fully_refunded = quantity == remaining_quantity
        db.execute(
            "UPDATE users SET balance=balance+?,purchases_total=MAX(purchases_total-?,0),"
            "purchases_count=MAX(purchases_count-?,0) WHERE user_id=?",
            (buyer_amount, buyer_amount, 1 if fully_refunded else 0, order["user_id"]),
        )
        if referrer_id and referral_amount:
            db.execute("UPDATE users SET balance=balance-?,referral_earnings=MAX(referral_earnings-?,0) WHERE user_id=?",
                       (referral_amount, referral_amount, referrer_id))
        if partner_amount:
            db.execute("UPDATE partners_bots SET partner_earnings=partner_earnings-?,updated_at=? WHERE id=?",
                       (partner_amount, now(), order["partner_bot_id"]))
        if fully_refunded:
            db.execute("UPDATE orders SET status='refunded',updated_at=? WHERE id=?", (now(), order["id"]))
        db.execute(
            "INSERT INTO support_partial_refunds(ticket_id,order_id,quantity,buyer_amount,referral_amount,partner_amount,operator_id,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (ticket_id, order["id"], quantity, buyer_amount, referral_amount, partner_amount, operator_id, now()),
        )
        db.commit()
        result = dict(order)
        result.update(refund_quantity=quantity, buyer_amount=buyer_amount, referral_amount=referral_amount,
                      partner_amount=partner_amount, remaining_quantity=remaining_quantity-quantity)
        return result, "ok"
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.callback_query(F.data.startswith("support:partial_refund:"))
async def partial_refund_callback(callback: CallbackQuery) -> None:
    if callback.from_user.id != SUPPORT_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    ticket_id = int(callback.data.rsplit(":", 1)[1])
    set_session(SUPPORT_ID, "partial_refund_quantity", {
        "ticket_id": ticket_id,
        "thread_id": callback.message.message_thread_id,
    })
    await callback.answer()
    await callback.message.answer("Введите количество единиц для возврата целым числом:")


async def process_partial_refund_quantity(message: Message, bot: Bot, payload: dict) -> None:
    value = (message.text or "").strip()
    if not value.isdigit() or int(value) <= 0:
        await message.answer("Введите количество целым положительным числом.")
        return
    ticket_id = int(payload["ticket_id"])
    order, status = refund_order_quantity(ticket_id, message.from_user.id, int(value))
    if status != "ok":
        await message.answer(status)
        return
    set_session(SUPPORT_ID, "menu")
    with connect() as db:
        ticket = dict(db.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone())
    text = (f"↩️ <b>Частичный возврат выполнен</b>\n\n🧾 Заказ: <code>{order_number(order)}</code>\n"
            f"📦 Возвращено: {order['refund_quantity']} шт.\n👤 На баланс: +{order['buyer_amount']:.2f} $\n"
            f"Осталось доступно для возврата: {order['remaining_quantity']} шт.")
    await message.answer(text, parse_mode="HTML")
    await bot.send_message(ticket["user_id"], text, parse_mode="HTML")


@router.callback_query(F.data.startswith("support:refund:"))
async def refund_callback(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user.id != SUPPORT_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    ticket_id = int(callback.data.rsplit(":", 1)[1])
    order, status = refund_order(ticket_id, callback.from_user.id)
    if status != "ok":
        await callback.answer(status, show_alert=True)
        return
    with connect() as db:
        ticket = dict(db.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone())
    text = (f"💸 <b>Возврат выполнен</b>\n\n🧾 Заказ: <code>{order_number(order)}</code>\n"
            f"👤 Покупателю: +{order['buyer_amount']:.2f} $\n"
            f"🤝 С реферала: −{order['referral_amount']:.2f} $\n"
            f"🏪 С франшизы: −{order['partner_amount']:.2f} $")
    await callback.answer("Деньги возвращены ✅")
    await bot.send_message(SUPPORT_ID, text, message_thread_id=ticket.get("support_thread_id"), parse_mode="HTML")
    await bot.send_message(ticket["user_id"], f"💸 <b>Возврат по заказу {order_number(order)} выполнен.</b>\nНа баланс зачислено {order['buyer_amount']:.2f} $.",
                           parse_mode="HTML")


@router.callback_query(F.data.startswith("support:close:"))
async def close_callback(callback: CallbackQuery, bot: Bot) -> None:
    if callback.from_user.id != SUPPORT_ID:
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    ticket_id = int(callback.data.rsplit(":", 1)[1])
    with connect() as db:
        row = db.execute("SELECT * FROM support_tickets WHERE id=?", (ticket_id,)).fetchone()
        if not row or row["status"] != "open":
            await callback.answer("Тикет уже закрыт", show_alert=True)
            return
        ticket = dict(row)
        db.execute("UPDATE support_tickets SET status='closed',closed_at=? WHERE id=?", (now(), ticket_id))
    await callback.answer("Тикет закрыт ✅")
    await bot.send_message(
        ticket["user_id"],
        f"✅ <b>Тикет #{ticket_id} закрыт</b>\n\nДля нового обращения нажмите /start.",
        parse_mode="HTML",
    )
    support_thread_id = ticket.get("support_thread_id")
    if support_thread_id:
        try:
            await bot.delete_forum_topic(chat_id=SUPPORT_ID, message_thread_id=int(support_thread_id))
        except (TelegramBadRequest, TelegramForbiddenError):
            logging.warning("Cannot delete support topic %s in chat %s", support_thread_id, SUPPORT_ID)


async def main() -> None:
    if not TOKEN or not SUPPORT_ID:
        raise RuntimeError("Set SUPPORT_TOKEN and support_id in .env")
    init_db()
    bot = Bot(TOKEN)
    me = await bot.get_me()
    if not me.has_topics_enabled:
        raise RuntimeError("Enable Threaded Mode for the support bot in @BotFather")
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=False)
    logging.info("Starting @%s with operator topics and ordinary customer chats", me.username)
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(main())
