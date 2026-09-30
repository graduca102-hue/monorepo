"""Bot handlers: supplier application, admin moderation, product cards, stock upload."""
from __future__ import annotations

import html
import io
import logging

import aiohttp
from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from . import keyboards as kb
from .catalog import Catalog
from .config import Config
from .db import Database, sale_price

log = logging.getLogger("supplier-bot")
router = Router()

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_LINES = 10_000


class Apply(StatesGroup):
    about = State()
    contact = State()


class NewCard(StatesGroup):
    title = State()
    description = State()
    price = State()
    confirm = State()


class Upload(StatesGroup):
    waiting = State()


def esc(value: object) -> str:
    return html.escape(str(value or ""))


def money(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


async def render(event: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    """Edit the callback's message in place, or send a new message."""
    if isinstance(event, CallbackQuery):
        message = event.message
        if isinstance(message, Message):
            try:
                await message.edit_text(text, reply_markup=markup)
                return
            except TelegramBadRequest as error:
                if "message is not modified" in str(error):
                    return
            await message.answer(text, reply_markup=markup)
        else:
            await event.bot.send_message(event.from_user.id, text, reply_markup=markup)
        return
    await event.answer(text, reply_markup=markup)


# --- texts ---------------------------------------------------------------


def terms_text(cfg: Config) -> str:
    example = 2.0
    return (
        "📋 <b>Условия</b>\n\n"
        f"• К вашей цене на витрине добавляется наценка {money(cfg.markup_percent)}%: "
        f"вы ставите {money(example)} $ → покупатель видит {money(sale_price(example, cfg.markup_percent))} $, "
        f"вам — {money(example)} $.\n"
        f"• В одной категории — не больше {cfg.max_cards_per_category} карточек.\n"
        "• Одна строка загруженного товара — одна единица.\n"
        "• Карточка с товаром в наличии сразу появляется на витрине SOUS MARKET; "
        "о каждой продаже придёт уведомление.\n"
        "• Выплаты — через тех поддержку.\n\n"
        f"Тех поддержка: @{esc(cfg.support_username)}"
    )


def application_text(supplier: dict) -> str:
    username = f"@{supplier['username']}" if supplier.get("username") else "—"
    return (
        "🆕 <b>Заявка поставщика</b>\n\n"
        f"👤 {esc(supplier.get('full_name'))} · {esc(username)}\n"
        f"🆔 <code>{supplier['user_id']}</code>\n\n"
        f"<b>Что продаёт:</b>\n{esc(supplier.get('about') or '—')}\n\n"
        f"<b>Контакт:</b> {esc(supplier.get('contact') or '—')}"
    )


def card_text(product: dict, prefix: str = "") -> str:
    return (
        f"{prefix}🏷 <b>{esc(product['title'])}</b>\n"
        f"📁 {esc(product['category_name'])}\n\n"
        f"{esc(truncate(product['description'], 1500))}\n\n"
        f"💵 Ваша цена: {money(product['price_usd'])} $\n"
        f"🛒 На витрине: {money(sale_price(product['price_usd'], product['markup_percent']))} $ "
        f"(+{money(product['markup_percent'])}%)\n"
        f"📦 В наличии: {product['available']} шт · продано: {product['sold']}"
    )


# --- helpers -------------------------------------------------------------


async def notify_admins(bot: Bot, cfg: Config, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
    delivered = False
    for admin_id in cfg.admin_ids:
        try:
            await bot.send_message(admin_id, text, reply_markup=markup)
            delivered = True
        except Exception as error:
            log.warning("admin %s notify failed: %s", admin_id, error)
    if delivered or not cfg.notify_bot_token:
        return
    # The admin has not started this bot yet: ping them through the shop bot.
    me = await bot.me()
    fallback = f"{text}\n\nОткройте @{me.username} и отправьте /admin, чтобы принять решение."
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        for admin_id in cfg.admin_ids:
            try:
                await session.post(
                    f"https://api.telegram.org/bot{cfg.notify_bot_token}/sendMessage",
                    json={"chat_id": admin_id, "text": fallback, "parse_mode": "HTML"},
                )
            except Exception as error:
                log.warning("fallback notify to %s failed: %s", admin_id, error)


async def require_supplier(callback: CallbackQuery, db: Database) -> bool:
    supplier = db.get_supplier(callback.from_user.id)
    if supplier and supplier["status"] == "approved":
        return True
    await callback.answer("Меню доступно после одобрения заявки.", show_alert=True)
    return False


async def show_home(event: Message | CallbackQuery, db: Database, cfg: Config) -> None:
    supplier = db.get_supplier(event.from_user.id)
    status = supplier["status"] if supplier else None
    if status == "approved":
        summary = db.supplier_summary(event.from_user.id)
        text = (
            "📦 <b>Меню поставщика</b>\n\n"
            f"Карточек: {summary['cards']} · в наличии: {summary['available']} шт · продано: {summary['sold']}\n"
            f"💰 Заработано: {money(summary['earned'])} $ · выплаты — через @{esc(cfg.support_username)}\n\n"
            f"Наценка витрины: +{money(cfg.markup_percent)}% к вашей цене.\n"
            f"Лимит: {cfg.max_cards_per_category} карточек в одной категории."
        )
        await render(event, text, kb.supplier_menu(cfg))
        return
    if status == "pending":
        text = "⏳ <b>Заявка на рассмотрении</b>\n\nОтвет придёт сюда."
        await render(event, text, kb.guest(cfg, can_apply=False))
        return
    if status == "blocked":
        await render(event, "Доступ к меню поставщика закрыт.", kb.guest(cfg, can_apply=False))
        return
    text = (
        "📦 <b>Поставщикам SOUS MARKET</b>\n\n"
        "Продавайте свои товары на витрине SOUS MARKET. Подайте заявку — после одобрения "
        "откроется меню поставщика: карточки товаров и загрузка товара.\n\n"
        f"К вашей цене на витрине добавляется наценка {money(cfg.markup_percent)}%."
    )
    if status == "rejected":
        text = "❌ Прошлая заявка отклонена. Можно подать новую.\n\n" + text
    await render(event, text, kb.guest(cfg, can_apply=True))


async def show_cards(event: Message | CallbackQuery, db: Database) -> None:
    products = db.list_products(event.from_user.id)
    if not products:
        text = "🗂 <b>Мои товары</b>\n\nКарточек пока нет."
    else:
        text = f"🗂 <b>Мои товары</b> — {len(products)}"
    await render(event, text, kb.cards_list(products))


# --- common --------------------------------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: Database, cfg: Config) -> None:
    await state.clear()
    await show_home(message, db, cfg)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, db: Database, cfg: Config) -> None:
    await state.clear()
    await show_home(message, db, cfg)


@router.callback_query(F.data == "home")
async def cb_home(callback: CallbackQuery, state: FSMContext, db: Database, cfg: Config) -> None:
    await callback.answer()
    await state.clear()
    await show_home(callback, db, cfg)


@router.callback_query(F.data == "terms")
async def cb_terms(callback: CallbackQuery, cfg: Config) -> None:
    await callback.answer()
    await render(callback, terms_text(cfg), kb.home_and_support(cfg))


# --- application ---------------------------------------------------------


@router.callback_query(F.data == "apply")
async def cb_apply(callback: CallbackQuery, state: FSMContext, db: Database, cfg: Config) -> None:
    await callback.answer()
    supplier = db.get_supplier(callback.from_user.id)
    if supplier and supplier["status"] in ("approved", "pending", "blocked"):
        await show_home(callback, db, cfg)
        return
    await state.set_state(Apply.about)
    await render(
        callback,
        "📝 <b>Заявка поставщика</b> · шаг 1/2\n\nЧто вы продаёте? Категории, объёмы, откуда товар.",
        kb.cancel(),
    )


@router.message(Apply.about, F.text)
async def apply_about(message: Message, state: FSMContext) -> None:
    about = message.text.strip()
    if len(about) < 10:
        await message.answer("Слишком коротко — опишите подробнее (от 10 символов).", reply_markup=kb.cancel())
        return
    await state.update_data(about=about[:1500])
    await state.set_state(Apply.contact)
    await message.answer(
        "📝 <b>Заявка поставщика</b> · шаг 2/2\n\nКонтакт для связи: @username, сайт или другой способ.",
        reply_markup=kb.skip_contact(),
    )


async def finish_application(event: Message | CallbackQuery, state: FSMContext, db: Database,
                             cfg: Config, bot: Bot, contact: str) -> None:
    data = await state.get_data()
    await state.clear()
    user = event.from_user
    db.submit_application(user.id, user.username, user.full_name, data.get("about", ""), contact)
    await render(event, "✅ <b>Заявка отправлена</b>\n\nОтвет придёт сюда.", kb.home_and_support(cfg))
    supplier = db.get_supplier(user.id)
    if supplier and supplier["status"] == "pending":
        await notify_admins(bot, cfg, application_text(supplier), kb.decide(user.id))


@router.message(Apply.contact, F.text)
async def apply_contact(message: Message, state: FSMContext, db: Database, cfg: Config, bot: Bot) -> None:
    await finish_application(message, state, db, cfg, bot, message.text.strip()[:200])


@router.callback_query(Apply.contact, F.data == "apply_skip")
async def apply_skip(callback: CallbackQuery, state: FSMContext, db: Database, cfg: Config, bot: Bot) -> None:
    await callback.answer()
    await finish_application(callback, state, db, cfg, bot, "")


# --- admin ---------------------------------------------------------------


@router.message(Command("admin"))
async def cmd_admin(message: Message, db: Database, cfg: Config) -> None:
    if message.from_user.id not in cfg.admin_ids:
        return
    stats = db.stats()
    pending = db.pending_applications()
    await message.answer(
        "🛠 <b>Поставщики</b>\n\n"
        f"Одобрено: {stats['approved']} · на рассмотрении: {stats['pending']} · "
        f"отклонено: {stats['rejected']}\n"
        f"Карточек: {stats['cards']} · товара в наличии: {stats['stock']} шт"
        + ("" if pending else "\n\nНовых заявок нет.")
    )
    for supplier in pending:
        await message.answer(application_text(supplier), reply_markup=kb.decide(supplier["user_id"]))


@router.callback_query(F.data.startswith("sup_ok:") | F.data.startswith("sup_no:"))
async def cb_decide(callback: CallbackQuery, db: Database, cfg: Config, bot: Bot) -> None:
    if callback.from_user.id not in cfg.admin_ids:
        await callback.answer("Нет доступа", show_alert=True)
        return
    action, raw_id = callback.data.split(":", 1)
    user_id = int(raw_id)
    if not db.get_supplier(user_id):
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    approved = action == "sup_ok"
    db.set_supplier_status(user_id, "approved" if approved else "rejected", callback.from_user.id)
    await callback.answer("Принято" if approved else "Отклонено")
    if isinstance(callback.message, Message):
        mark = "✅ Принято" if approved else "❌ Отклонено"
        try:
            await callback.message.edit_text(f"{callback.message.html_text}\n\n<b>{mark}</b>", reply_markup=None)
        except TelegramBadRequest:
            pass
    try:
        if approved:
            await bot.send_message(
                user_id, "🎉 <b>Заявка одобрена</b> — вы поставщик SOUS MARKET.", reply_markup=kb.open_menu()
            )
        else:
            await bot.send_message(
                user_id,
                f"❌ Заявка отклонена. Вопросы — @{esc(cfg.support_username)}.",
                reply_markup=kb.home_and_support(cfg),
            )
    except Exception as error:
        log.warning("decision notify to %s failed: %s", user_id, error)


# --- new card ------------------------------------------------------------


@router.callback_query(F.data == "card_new")
async def cb_card_new(callback: CallbackQuery, state: FSMContext, db: Database, catalog: Catalog) -> None:
    if not await require_supplier(callback, db):
        return
    await callback.answer()
    await state.clear()
    await render(
        callback,
        "➕ <b>Новая карточка</b>\n\nВыберите категорию.",
        kb.categories(await catalog.categories(), back="home"),
    )


@router.callback_query(F.data.startswith("nc_cat:"))
async def cb_card_category(callback: CallbackQuery, state: FSMContext, db: Database, cfg: Config,
                           catalog: Catalog) -> None:
    if not await require_supplier(callback, db):
        return
    category_id = int(callback.data.split(":", 1)[1])
    node, parent = await catalog.find(category_id)
    if node is None:
        await callback.answer("Категория недоступна", show_alert=True)
        return
    if node["children"]:
        await callback.answer()
        await render(
            callback,
            f"➕ <b>Новая карточка</b>\n\n{esc(node['name'])} — выберите подкатегорию.",
            kb.categories(node["children"], back="card_new"),
        )
        return
    used = db.count_active_cards(callback.from_user.id, category_id)
    if used >= cfg.max_cards_per_category:
        await callback.answer(
            f"В этой категории уже {used} карточек — это максимум. "
            "Удалите старую или выберите другую категорию.",
            show_alert=True,
        )
        return
    await callback.answer()
    name = f"{parent['name']} / {node['name']}" if parent else node["name"]
    await state.set_state(NewCard.title)
    await state.update_data(category_id=category_id, category_name=name)
    await render(
        callback,
        f"➕ <b>Новая карточка</b> · шаг 1/3\n\n📁 {esc(name)} ({used}/{cfg.max_cards_per_category})\n\n"
        "Название товара — до 100 символов.",
        kb.cancel(),
    )


@router.message(NewCard.title, F.text)
async def card_title(message: Message, state: FSMContext) -> None:
    title = " ".join(message.text.split())
    if not 3 <= len(title) <= 100:
        await message.answer("Название — от 3 до 100 символов.", reply_markup=kb.cancel())
        return
    await state.update_data(title=title)
    await state.set_state(NewCard.description)
    await message.answer(
        "➕ <b>Новая карточка</b> · шаг 2/3\n\nОписание товара — до 2000 символов.", reply_markup=kb.cancel()
    )


@router.message(NewCard.description, F.text)
async def card_description(message: Message, state: FSMContext, cfg: Config) -> None:
    description = message.text.strip()
    if not 10 <= len(description) <= 2000:
        await message.answer("Описание — от 10 до 2000 символов.", reply_markup=kb.cancel())
        return
    await state.update_data(description=description)
    await state.set_state(NewCard.price)
    example = 2.0
    await message.answer(
        "➕ <b>Новая карточка</b> · шаг 3/3\n\nЦена за 1 шт в $.\n\n"
        f"ℹ️ К вашей цене на витрине добавляется наценка {money(cfg.markup_percent)}%: "
        f"вы ставите {money(example)} $ → покупатель видит "
        f"{money(sale_price(example, cfg.markup_percent))} $, вам — {money(example)} $.",
        reply_markup=kb.cancel(),
    )


@router.message(NewCard.price, F.text)
async def card_price(message: Message, state: FSMContext, cfg: Config) -> None:
    try:
        price = round(float(message.text.replace("$", "").replace(",", ".").replace(" ", "")), 2)
    except ValueError:
        price = 0.0
    if not 0.01 <= price <= 100_000:
        await message.answer("Укажите цену числом, например 2 или 0.5.", reply_markup=kb.cancel())
        return
    await state.update_data(price=price)
    await state.set_state(NewCard.confirm)
    data = await state.get_data()
    preview = {
        "title": data["title"], "category_name": data["category_name"], "description": data["description"],
        "price_usd": price, "markup_percent": cfg.markup_percent, "available": 0, "sold": 0,
    }
    await message.answer(card_text(preview, prefix="👀 <b>Проверьте карточку</b>\n\n"), reply_markup=kb.confirm_card())


@router.callback_query(NewCard.confirm, F.data == "nc_save")
async def cb_card_save(callback: CallbackQuery, state: FSMContext, db: Database, cfg: Config, bot: Bot) -> None:
    if not await require_supplier(callback, db):
        return
    data = await state.get_data()
    product_id = db.create_product(
        callback.from_user.id, data["category_id"], data["category_name"], data["title"],
        data["description"], data["price"], cfg.markup_percent, cfg.max_cards_per_category,
    )
    if product_id is None:
        await callback.answer(
            f"В этой категории уже {cfg.max_cards_per_category} карточек — это максимум.", show_alert=True
        )
        return
    await callback.answer("Сохранено")
    await state.clear()
    product = db.get_product(product_id, callback.from_user.id)
    await render(
        callback,
        card_text(product, prefix="✅ <b>Карточка создана.</b> Теперь загрузите товар.\n\n"),
        kb.card(product_id),
    )
    user = callback.from_user
    await notify_admins(
        bot, cfg,
        f"🆕 <b>Новая карточка</b> от {esc(user.full_name)}"
        f"{' · @' + esc(user.username) if user.username else ''} (<code>{user.id}</code>)\n\n"
        + card_text(product),
    )


# --- my cards ------------------------------------------------------------


@router.callback_query(F.data == "cards")
async def cb_cards(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not await require_supplier(callback, db):
        return
    await callback.answer()
    await state.clear()
    await show_cards(callback, db)


@router.callback_query(F.data.startswith("card:"))
async def cb_card(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not await require_supplier(callback, db):
        return
    await state.clear()
    product = db.get_product(int(callback.data.split(":", 1)[1]), callback.from_user.id)
    if not product:
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    await callback.answer()
    await render(callback, card_text(product), kb.card(product["id"]))


@router.callback_query(F.data.startswith("card_del:"))
async def cb_card_delete(callback: CallbackQuery, db: Database) -> None:
    if not await require_supplier(callback, db):
        return
    product = db.get_product(int(callback.data.split(":", 1)[1]), callback.from_user.id)
    if not product:
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    await callback.answer()
    await render(
        callback,
        f"🗑 Удалить карточку «{esc(product['title'])}»?\n\n"
        f"Непроданный товар ({product['available']} шт) уйдёт из продажи вместе с ней.",
        kb.confirm_delete(product["id"]),
    )


@router.callback_query(F.data.startswith("card_delok:"))
async def cb_card_delete_confirm(callback: CallbackQuery, db: Database) -> None:
    if not await require_supplier(callback, db):
        return
    deleted = db.delete_product(int(callback.data.split(":", 1)[1]), callback.from_user.id)
    await callback.answer("Удалено" if deleted else "Карточка не найдена")
    await show_cards(callback, db)


# --- stock upload --------------------------------------------------------


@router.callback_query(F.data.startswith("card_up:"))
async def cb_upload(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if not await require_supplier(callback, db):
        return
    product = db.get_product(int(callback.data.split(":", 1)[1]), callback.from_user.id)
    if not product:
        await callback.answer("Карточка не найдена", show_alert=True)
        return
    await callback.answer()
    await state.set_state(Upload.waiting)
    await state.update_data(product_id=product["id"])
    await render(
        callback,
        f"📤 <b>Загрузка товара</b> — «{esc(product['title'])}»\n\n"
        "Отправьте файл .txt или текст сообщением. Одна строка — одна единица товара.\n"
        f"За раз — до {MAX_UPLOAD_LINES:,} строк.".replace(",", " "),
        kb.upload_cancel(product["id"]),
    )


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


async def _save_upload(message: Message, state: FSMContext, db: Database, text: str) -> None:
    data = await state.get_data()
    product_id = data.get("product_id")
    product = db.get_product(product_id, message.from_user.id) if product_id else None
    if not product:
        await state.clear()
        await message.answer("Карточка не найдена.", reply_markup=kb.open_menu())
        return
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        await message.answer("Пусто — нет ни одной строки товара.", reply_markup=kb.upload_cancel(product_id))
        return
    if len(lines) > MAX_UPLOAD_LINES:
        await message.answer(
            f"Слишком много строк: {len(lines)}. За раз — до {MAX_UPLOAD_LINES}.",
            reply_markup=kb.upload_cancel(product_id),
        )
        return
    added, skipped = db.add_stock(product_id, lines)
    await state.clear()
    product = db.get_product(product_id, message.from_user.id)
    text_out = f"✅ Загружено: {added} шт"
    if skipped:
        text_out += f" · пропущено повторов: {skipped}"
    text_out += f"\n📦 В наличии: {product['available']} шт"
    await message.answer(text_out, reply_markup=kb.after_upload(product_id))


@router.message(Upload.waiting, F.document)
async def upload_document(message: Message, state: FSMContext, db: Database, bot: Bot) -> None:
    document = message.document
    data = await state.get_data()
    product_id = int(data.get("product_id") or 0)
    name = (document.file_name or "").lower()
    if not (name.endswith(".txt") or (document.mime_type or "").startswith("text/")):
        await message.answer("Нужен файл .txt.", reply_markup=kb.upload_cancel(product_id))
        return
    if (document.file_size or 0) > MAX_UPLOAD_BYTES:
        await message.answer("Файл больше 5 МБ — разбейте на части.", reply_markup=kb.upload_cancel(product_id))
        return
    buffer = io.BytesIO()
    await bot.download(document, destination=buffer)
    await _save_upload(message, state, db, _decode(buffer.getvalue()))


@router.message(Upload.waiting, F.text)
async def upload_text(message: Message, state: FSMContext, db: Database) -> None:
    await _save_upload(message, state, db, message.text)


# --- fallback ------------------------------------------------------------


@router.message()
async def fallback(message: Message, state: FSMContext, db: Database, cfg: Config) -> None:
    if await state.get_state():
        await message.answer("Отправьте текстом или нажмите «Отмена».", reply_markup=kb.cancel())
        return
    await show_home(message, db, cfg)
