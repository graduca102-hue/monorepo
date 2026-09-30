from __future__ import annotations

import asyncio
import html
import json
import logging
import math
import secrets
import time
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Iterable

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from .clients import (
    ApiError,
    HeleketClient,
    SousClient,
    external_order_id,
    external_order_number,
    extract_delivery,
    sale_price_kopecks,
)
from .config import Config
from .dataimpulse import (
    DataImpulseClient,
    POOL_TYPES,
    PROXY_FORMATS as DI_PROXY_FORMATS,
    SESSION_TTL_CAP as DI_SESSION_TTL_CAP,
    build_lines as di_build_lines,
    cost_setting_key,
    filter_countries as di_filter_countries,
    format_label as di_format_label,
    pool_label,
    pool_parameters as di_pool_parameters,
)
from .db import Database, InsufficientFunds
from .strikeproxy import (
    StrikeProxyClient,
    PLAN_TYPES as SP_PLAN_TYPES,
    PROXY_FORMATS as SP_PROXY_FORMATS,
    SESSION_MINUTES_CAP as SP_SESSION_MINUTES_CAP,
    cost_setting_key as sp_cost_setting_key,
    topup_cost_setting_key as sp_topup_cost_setting_key,
    filter_countries as sp_filter_countries,
    format_label as sp_format_label,
    plan_label as sp_plan_label,
    rewrite_lines as sp_rewrite_lines,
    section_code as sp_section_code,
)
from .keyboards import (
    back,
    main_menu,
    market_payment_menu,
    market_quantity_menu,
    market_categories,
    order_menu,
    payment_menu,
    product_detail,
    product_list,
    profile_menu,
    proxy_menu,
    di_count_menu,
    di_country_label,
    di_country_menu,
    di_country_results_menu,
    di_format_menu,
    di_manage_menu,
    di_password_confirm,
    di_rotation_menu,
    di_session_menu,
    di_topup_menu,
    DI_TOPUP_GB,
    DI_TOPUP_MIN_GB,
    sp_count_menu,
    sp_country_label,
    sp_country_menu,
    sp_country_results_menu,
    sp_format_menu,
    sp_manage_menu,
    sp_password_confirm,
    sp_rotation_menu,
    sp_session_menu,
    sp_topup_menu,
    SP_TOPUP_GB,
    SP_TOPUP_MIN_GB,
    service_display_label,
    settings_menu,
)
from .states import (
    DataImpulseState,
    PaymentState,
    PromoState,
    PurchaseState,
    StrikeProxyState,
)
from .ui import is_auto_delivery_product, product_display_title


logger = logging.getLogger(__name__)
router = Router(name="user")


def rub(kopecks: int) -> str:
    return f"{kopecks / 100:,.2f}".replace(",", " ")


def cut(value: Any, limit: int = 3400) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def safe_edit(callback: CallbackQuery, text: str, reply_markup=None) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            await callback.message.answer(text, reply_markup=reply_markup)


def message_user_id(message: Message) -> int:
    if message.from_user is None:
        raise RuntimeError("The update has no user")
    return message.from_user.id


async def ensure_user(message_or_callback: Message | CallbackQuery, db: Database) -> None:
    user = message_or_callback.from_user
    if user:
        await db.upsert_user(user.id, user.username or "", user.full_name)


async def proxy_section_enabled(db: Database) -> bool:
    """Whether the Proxy category is offered at all.

    Kept as a single switch so the section can be pulled from the storefront
    (e.g. while supplier prices are unknown) without touching per-pool
    visibility, and put back with one button in the admin panel.
    """
    return (await db.get_setting("proxy_section_enabled") or "1") != "0"


async def show_home(message: Message, config: Config, db: Database) -> None:
    await ensure_user(message, db)
    name = html.escape(message.from_user.full_name if message.from_user else "покупатель")
    await message.answer(
        f"Добро пожаловать, <b>{name}</b>!\n\nВыберите раздел в меню ниже.",
        reply_markup=main_menu(
            bool(message.from_user and message.from_user.id in config.admin_ids),
            show_proxy=await proxy_section_enabled(db),
        ),
    )


@router.message(CommandStart())
async def start(message: Message, command: CommandObject, config: Config, db: Database) -> None:
    referrer_id = None
    if command.args:
        match = re.fullmatch(r"ref_(\d+)", command.args.strip())
        if match:
            referrer_id = int(match.group(1))
    user = message.from_user
    if user:
        await db.upsert_user(user.id, user.username or "", user.full_name, referrer_id)
    await show_home(message, config, db)


@router.message(Command("menu"))
async def menu_command(message: Message, config: Config, db: Database, state: FSMContext) -> None:
    await state.clear()
    await show_home(message, config, db)


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data == "menu:home")
async def menu_home(callback: CallbackQuery, config: Config, db: Database, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await ensure_user(callback, db)
    name = html.escape(callback.from_user.full_name)
    await safe_edit(
        callback,
        f"Добро пожаловать, <b>{name}</b>!\n\nВыберите раздел в меню ниже.",
        main_menu(
            callback.from_user.id in config.admin_ids,
            show_proxy=await proxy_section_enabled(db),
        ),
    )


@router.callback_query(F.data == "menu:catalog")
async def catalog(
    callback: CallbackQuery, state: FSMContext, config: Config, db: Database
) -> None:
    await callback.answer()
    await state.clear()
    await safe_edit(
        callback,
        "Выберите раздел:",
        main_menu(
            callback.from_user.id in config.admin_ids,
            show_proxy=await proxy_section_enabled(db),
        ),
    )


async def relevant_categories(sous: SousClient, platform: str) -> list[dict[str, Any]]:
    data = await sous.market_categories()
    sections = data.get("sections") or []
    if platform == "instagram":
        for section in sections:
            if str(section.get("name", "")).strip().lower() == "instagram":
                return list(section.get("items") or [])
    if platform == "youtube":
        for section in sections:
            if "youtube" in str(section.get("name", "")).lower():
                items = section.get("items") or []
                return [
                    item
                    for item in items
                    if "youtube" in str(item.get("name", "")).lower()
                    or "по годам" in str(item.get("name", "")).lower()
                ]
    return []


@router.callback_query(F.data.in_({"cat:instagram", "cat:youtube"}))
async def market_platform(callback: CallbackQuery, sous: SousClient) -> None:
    await callback.answer()
    platform = (callback.data or "").split(":", 1)[1]
    try:
        items = await relevant_categories(sous, platform)
    except ApiError as exc:
        await safe_edit(callback, html.escape(str(exc)), back("menu:home"))
        return
    title = "Instagram" if platform == "instagram" else "YouTube"
    if not items:
        await safe_edit(callback, "Спросите у поддержки", back("menu:home"))
        return
    await safe_edit(callback, f"<b>{title}</b>\n\nВыберите категорию:", market_categories(items, platform))


async def product_page_data(
    sous: SousClient, db: Database, category_id: int, page: int
) -> tuple[dict[str, Any], dict[int, int]]:
    data = await sous.market_products(category_id, page)
    visible_items = [
        item for item in data.get("items") or [] if not is_auto_delivery_product(item)
    ]
    overrides = await db.get_product_overrides([int(item["id"]) for item in visible_items])
    resolved_items = []
    for item in visible_items:
        override = overrides.get(int(item["id"]))
        if override:
            if override["hidden"]:
                continue
            if override["custom_title"]:
                item = {**item, "_custom_title": override["custom_title"]}
        resolved_items.append(item)
    rate, markup = await db.pricing("market")
    min_price = await db.market_min_price_kopecks()
    priced_items: list[dict[str, Any]] = []
    prices: dict[int, int] = {}
    for item in resolved_items:
        price = sale_price_kopecks(item.get("price_usdt", 0), rate, markup)
        if price < min_price:
            continue
        priced_items.append(item)
        prices[int(item["id"])] = price
    data = {**data, "items": priced_items}
    return data, prices


@router.callback_query(F.data.startswith("mc:"))
async def market_products(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
    await callback.answer()
    try:
        _, category_raw, page_raw = (callback.data or "").split(":")
        category_id, page = int(category_raw), max(1, int(page_raw))
        data, prices = await product_page_data(sous, db, category_id, page)
    except (ValueError, ApiError) as exc:
        await safe_edit(callback, f"Не удалось загрузить товары: {html.escape(str(exc))}", back("menu:home"))
        return
    items = data.get("items") or []
    if not items:
        await safe_edit(callback, "Спросите у поддержки", back("menu:home"))
        return
    last_page = int(data.get("last_page", 1))
    await safe_edit(
        callback,
        f"🛍 <b>Товары</b>\n\nСтраница {page} из {last_page}. Выберите позицию:",
        product_list(items, category_id, page, last_page, prices),
    )


async def find_product(
    sous: SousClient, db: Database, category_id: int, page: int, product_id: int
) -> dict[str, Any] | None:
    data = await sous.market_products(category_id, page)
    product = next(
        (item for item in data.get("items") or [] if int(item.get("id", 0)) == product_id),
        None,
    )
    if not product or is_auto_delivery_product(product):
        return None
    rate, markup = await db.pricing("market")
    if sale_price_kopecks(product.get("price_usdt", 0), rate, markup) < await db.market_min_price_kopecks():
        return None
    override = await db.get_product_override(product_id)
    if override["hidden"]:
        return None
    if override["custom_title"]:
        product = {**product, "_custom_title": override["custom_title"]}
    return product


@router.callback_query(F.data.startswith("prod:"))
async def market_product_detail(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
    await callback.answer()
    try:
        _, category_raw, page_raw, product_raw = (callback.data or "").split(":")
        category_id, page, product_id = int(category_raw), int(page_raw), int(product_raw)
        product = await find_product(sous, db, category_id, page, product_id)
        if not product:
            raise ValueError("Товар больше не найден")
        rate, markup = await db.pricing("market")
        price = sale_price_kopecks(product.get("price_usdt", 0), rate, markup)
    except (ValueError, ApiError) as exc:
        await safe_edit(callback, html.escape(str(exc)), back("menu:home"))
        return
    display_title = product_display_title(product, category_id)
    text = (
        f"<b>{html.escape(display_title)}</b>\n\n"
        f"Цена: <b>{rub(price)} $</b>\n"
        f"В наличии: <b>{int(product.get('quantity', 0))}</b>"
    )
    await safe_edit(callback, text, product_detail(category_id, page, product_id))


@router.callback_query(F.data.startswith("buyprod:"))
async def ask_market_quantity(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    try:
        _, category_raw, page_raw, product_raw = (callback.data or "").split(":")
        await state.update_data(
            category_id=int(category_raw), page=int(page_raw), product_id=int(product_raw)
        )
    except ValueError:
        await safe_edit(callback, "Некорректный товар.", back("menu:home"))
        return
    await safe_edit(
        callback,
        "Выберите количество товара:",
        market_quantity_menu(int(category_raw), int(page_raw), int(product_raw)),
    )


async def show_market_payment(callback: CallbackQuery, state: FSMContext, quantity: int) -> None:
    data = await state.get_data()
    await state.update_data(quantity=quantity)
    await safe_edit(callback, f"Количество: <b>{quantity}</b>\n\nВыберите способ оплаты:", market_payment_menu())


@router.callback_query(F.data.startswith("mq:"))
async def market_quantity_button(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    try:
        _, category, page, product, quantity = (callback.data or "").split(":")
        await state.update_data(category_id=int(category), page=int(page), product_id=int(product))
        await show_market_payment(callback, state, int(quantity))
    except ValueError:
        await safe_edit(callback, "Некорректное количество.", back())


@router.callback_query(F.data.startswith("mqcustom:"))
async def market_quantity_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    _, category, page, product = (callback.data or "").split(":")
    await state.set_state(PurchaseState.market_quantity)
    await state.update_data(category_id=int(category), page=int(page), product_id=int(product))
    await safe_edit(callback, "Введите количество товара целым числом.", back(f"prod:{category}:{page}:{product}", "Отмена"))


@router.message(Command("cancel"))
async def cancel_state(
    message: Message, state: FSMContext, config: Config, db: Database
) -> None:
    await state.clear()
    await message.answer(
        "Действие отменено.",
        reply_markup=main_menu(
            message_user_id(message) in config.admin_ids,
            show_proxy=await proxy_section_enabled(db),
        ),
    )


def order_ref(order: dict[str, Any] | None, order_id: int, order_number: str = "") -> str:
    """Number to show the customer: the real SOUS order number when we have it,
    otherwise the local order id as a fallback."""
    number = order_number or (order.get("order_number") if order else "") or ""
    return str(number) if number else str(order_id)


async def send_delivery(message: Message, order_id: int, delivery: str, ref: str | None = None) -> None:
    if not delivery:
        return
    label = ref or str(order_id)
    if len(delivery) > 3000:
        file = BufferedInputFile(delivery.encode(), filename=f"order_{order_id}.txt")
        await message.answer_document(file, caption=f"📄 Данные заказа №{label}")
    else:
        await message.answer(f"<b>Данные товара:</b>\n<pre>{html.escape(delivery)}</pre>")


async def send_delivery_to_user(bot, user_id: int, order_id: int, delivery: str, ref: str | None = None) -> None:
    label = ref or str(order_id)
    if len(delivery) > 3000:
        file = BufferedInputFile(delivery.encode(), filename=f"order_{order_id}.txt")
        await bot.send_document(user_id, file, caption=f"📄 Данные заказа №{label}")
    else:
        await bot.send_message(user_id, f"<b>Данные товара:</b>\n<pre>{html.escape(delivery)}</pre>")


# Upstream states that mean the order will never be delivered and the money is
# already back on our SOUS balance.  Anything else is still in flight.
MARKET_REFUNDED_STATUSES = {"credited", "proxy_balance_error", "delivery_error", "cancelled", "canceled"}
MARKET_DELIVERY_POLL_ATTEMPTS = 120
MARKET_DELIVERY_POLL_INTERVAL = 5
MARKET_RECOVERY_INTERVAL_SECONDS = 120


async def settle_refunded_market_order(
    *,
    db: Database,
    bot,
    user_id: int,
    order_id: int,
    order: dict[str, Any] | None,
    result: dict[str, Any],
) -> bool:
    """Return the buyer's money when the upstream shop refunded the order."""
    if not await db.refund_pending_order(order_id, result):
        return False
    ref = order_ref(order, order_id)
    total = int((order or {}).get("total_kopecks") or 0)
    try:
        await bot.send_message(
            user_id,
            f"❌ Заказ №{ref} не выдан поставщиком.\n"
            f"Возврат <b>{rub(total)} $</b> зачислен на баланс — попробуйте оформить заказ ещё раз.",
        )
    except Exception:
        logger.exception("Could not notify buyer about refunded order %s", order_id)
    return True


async def poll_market_delivery(
    *,
    db: Database,
    bot,
    user_id: int,
    order_id: int,
    external_id: str,
    delivery_check: Callable[[str], Awaitable[dict[str, Any]]],
) -> bool:
    """One upstream check: deliver, refund or leave the order pending.

    Returns ``True`` once the order reached a final state.
    """
    order = await db.get_order(order_id)
    if not order or order["status"] != "delivery_pending":
        return True
    result = await delivery_check(external_id)
    delivery = extract_delivery(result)
    if delivery:
        number = external_order_number(result)
        await db.complete_order(
            order_id,
            external_id=external_id,
            response=result,
            delivery=delivery,
            status="completed",
            order_number=number,
        )
        ref = order_ref(order, order_id, number)
        await send_delivery_to_user(bot, user_id, order_id, delivery, ref)
        return True
    status = str((result.get("order") or result).get("status") or result.get("status") or "").lower()
    if status in MARKET_REFUNDED_STATUSES:
        await settle_refunded_market_order(
            db=db, bot=bot, user_id=user_id, order_id=order_id, order=order, result=result
        )
        return True
    return False


async def wait_for_market_delivery(
    *,
    db: Database,
    bot,
    user_id: int,
    order_id: int,
    external_id: str,
    delivery_check: Callable[[str], Awaitable[dict[str, Any]]],
) -> None:
    """Poll a newly created market order, without making the buyer retry manually."""
    for _ in range(MARKET_DELIVERY_POLL_ATTEMPTS):
        await asyncio.sleep(MARKET_DELIVERY_POLL_INTERVAL)
        try:
            if await poll_market_delivery(
                db=db,
                bot=bot,
                user_id=user_id,
                order_id=order_id,
                external_id=external_id,
                delivery_check=delivery_check,
            ):
                return
        except Exception:
            # A single failed check (network blip, upstream 502) must not strand
            # the order: the recovery loop keeps watching it either way.
            logger.exception("Unable to check delivery for market order %s", order_id)


async def market_delivery_recovery_loop(db: Database, bot, sous: SousClient) -> None:
    """Keep watching orders whose delivery outlived their purchase-time poller.

    Without this a bot restart, or a supplier slower than the polling window,
    leaves a paid order pending forever with nobody checking on it.
    """
    while True:
        await asyncio.sleep(MARKET_RECOVERY_INTERVAL_SECONDS)
        try:
            pending = await db.pending_delivery_orders()
        except Exception:
            logger.exception("Could not list pending market orders")
            continue
        for order in pending:
            try:
                await poll_market_delivery(
                    db=db,
                    bot=bot,
                    user_id=int(order["user_id"]),
                    order_id=int(order["id"]),
                    external_id=str(order["external_id"]),
                    delivery_check=sous.market_order_status,
                )
            except Exception:
                logger.exception("Delivery recovery failed for order %s", order.get("id"))
            await asyncio.sleep(1)


async def prepare_market_purchase(
    *,
    sous: SousClient,
    db: Database,
    category_id: int,
    page: int,
    product_id: int,
    quantity: int,
) -> dict[str, Any]:
    product = await find_product(sous, db, category_id, page, product_id)
    if not product:
        raise ValueError("Товар больше не доступен")
    if quantity > int(product.get("quantity", 0)):
        raise ValueError("Недостаточно товара в наличии")
    rate, markup = await db.pricing("market")
    unit_price = sale_price_kopecks(product.get("price_usdt", 0), rate, markup)
    return {
        "kind": "market",
        "title": product_display_title(product, category_id),
        "category_id": category_id,
        "page": page,
        "product_id": product_id,
        "quantity": quantity,
        "unit_price": unit_price,
        "total": unit_price * quantity,
        "request_payload": {
            "category_id": category_id,
            "product_id": product_id,
            "quantity": quantity,
        },
    }


async def run_purchase(
    *,
    message: Message,
    db: Database,
    request_id: str,
    kind: str,
    title: str,
    quantity: float,
    unit_price: int,
    total: int,
    request_payload: dict[str, Any],
    api_call: Callable[[], Awaitable[dict[str, Any]]],
    delivery_check: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    actor_id: int | None = None,
) -> None:
    user_id = actor_id or message_user_id(message)
    try:
        order_id = await db.reserve_order(
            request_id=request_id,
            user_id=user_id,
            kind=kind,
            title=title,
            quantity=quantity,
            unit_price_kopecks=unit_price,
            total_kopecks=total,
            request=request_payload,
        )
    except InsufficientFunds:
        await message.answer(
            f"Недостаточно средств. Для заказа нужно <b>{rub(total)} $</b>.",
            reply_markup=back("menu:topup", "Пополнить баланс"),
        )
        return
    await message.answer(f"⏳ Заказ №{order_id} создаётся…")
    try:
        result = await api_call()
        external_id = external_order_id(result)
        order_number = external_order_number(result)
        ref = order_number or str(order_id)
        delivery = extract_delivery(result)
        # A provider can acknowledge a purchase before it has generated the
        # actual credentials (for example, market orders use
        # ``orders[].delivery_text``).  Never present such an order as
        # completed: without delivery data it is still awaiting issuance.
        pending = not delivery
        status = "delivery_pending" if pending else "completed"
        await db.complete_order(
            order_id,
            external_id=external_id,
            response=result,
            delivery=delivery,
            status=status,
            order_number=order_number,
        )
        if pending:
            await message.answer(
                f"✅ Заказ №{ref} принят. Поставщик ещё формирует выдачу.",
                reply_markup=order_menu(order_id, kind in {"market", "proxy_service"} and bool(external_id)),
            )
            if kind == "market" and external_id and delivery_check:
                asyncio.create_task(
                    wait_for_market_delivery(
                        db=db,
                        bot=message.bot,
                        user_id=user_id,
                        order_id=order_id,
                        external_id=external_id,
                        delivery_check=delivery_check,
                    ),
                    name=f"market-delivery-{order_id}",
                )
        else:
            await message.answer(
                f"✅ Заказ №{ref} выполнен на сумму <b>{rub(total)} $</b>.",
                reply_markup=order_menu(order_id),
            )
            await send_delivery(message, order_id, delivery, ref)
    except ApiError as exc:
        await db.fail_order(order_id, {"error": str(exc), "status": exc.status}, uncertain=exc.uncertain)
        if exc.uncertain:
            await message.answer(
                f"⚠️ Статус заказа №{order_id} неясен из-за обрыва связи. Средства зарезервированы, заказ передан администратору на проверку."
            )
        else:
            await message.answer(
                f"❌ Заказ №{order_id} не создан: {html.escape(str(exc))}. Сумма возвращена на баланс."
            )
    except Exception as exc:
        logger.exception("Unexpected purchase error")
        await db.fail_order(order_id, {"error": str(exc)}, uncertain=True)
        await message.answer(
            f"⚠️ Заказ №{order_id} требует ручной проверки. Средства остаются зарезервированы."
        )


async def run_market_purchase(
    *,
    message: Message,
    db: Database,
    sous: SousClient,
    purchase: dict[str, Any],
    actor_id: int | None = None,
) -> None:
    request_id = f"market_{actor_id or message_user_id(message)}_{uuid.uuid4().hex}"
    await run_purchase(
        message=message,
        db=db,
        request_id=request_id,
        kind="market",
        title=str(purchase["title"]),
        quantity=int(purchase["quantity"]),
        unit_price=int(purchase["unit_price"]),
        total=int(purchase["total"]),
        request_payload=dict(purchase["request_payload"]),
        api_call=lambda: sous.market_order(
            int(purchase["category_id"]),
            int(purchase["product_id"]),
            int(purchase["quantity"]),
        ),
        delivery_check=sous.market_order_status,
        actor_id=actor_id,
    )


async def finalize_paid_payment(
    *,
    db: Database,
    sous: SousClient,
    bot,
    payment: dict[str, Any],
    notify_user: bool = True,
) -> None:
    user_id = int(payment["user_id"])
    amount = int(payment["amount_kopecks"])
    purpose = str(payment.get("purpose") or "topup")
    if purpose == "market_purchase":
        try:
            purchase = json.loads(str(payment.get("purchase_payload") or "{}"))
            if not isinstance(purchase, dict):
                raise ValueError("invalid purchase payload")

            class DeferredPurchaseMessage:
                def __init__(self, *, chat_id: int, bot) -> None:
                    self.chat_id = chat_id
                    self.bot = bot
                    self.from_user = None

                async def answer(self, text: str, reply_markup=None) -> None:
                    await self.bot.send_message(self.chat_id, text, reply_markup=reply_markup)

            if notify_user:
                await bot.send_message(
                    user_id,
                    f"✅ Оплата подтверждена. Заказ на <b>{rub(amount)} $</b> создаётся автоматически.",
                )
            await run_market_purchase(
                message=DeferredPurchaseMessage(chat_id=user_id, bot=bot),
                db=db,
                sous=sous,
                purchase=purchase,
                actor_id=user_id,
            )
            return
        except Exception:
            logger.exception("Could not finalize crypto market purchase for payment %s", payment["order_id"])
            if notify_user:
                await bot.send_message(
                    user_id,
                    "✅ Оплата подтверждена и зачислена на баланс. Автосоздать заказ не удалось, попробуйте купить товар ещё раз вручную.",
                )
            return
    if notify_user:
        await bot.send_message(
            user_id,
            f"✅ Оплата подтверждена. На баланс зачислено <b>{rub(amount)} $</b>.",
        )


@router.message(PurchaseState.market_quantity)
async def buy_market(message: Message, state: FSMContext, sous: SousClient, db: Database) -> None:
    try:
        quantity = int((message.text or "").strip())
        if quantity < 1 or quantity > 1000:
            raise ValueError
    except ValueError:
        await message.answer("Введите целое количество от 1 до 1000.")
        return
    data = await state.get_data()
    try:
        await prepare_market_purchase(
            sous=sous,
            db=db,
            category_id=int(data["category_id"]),
            page=int(data["page"]),
            product_id=int(data["product_id"]),
            quantity=quantity,
        )
    except (ApiError, ValueError) as exc:
        await state.clear()
        await message.answer(html.escape(str(exc)), reply_markup=back("menu:home"))
        return
    await state.update_data(quantity=quantity)
    await message.answer(
        f"Количество: <b>{quantity}</b>\n\nВыберите способ оплаты:",
        reply_markup=market_payment_menu(),
    )


@router.callback_query(F.data == "mp:balance")
async def buy_market_with_balance(callback: CallbackQuery, state: FSMContext, sous: SousClient, db: Database) -> None:
    await callback.answer()
    data = await state.get_data()
    if not isinstance(callback.message, Message):
        return
    try:
        purchase = await prepare_market_purchase(
            sous=sous,
            db=db,
            category_id=int(data["category_id"]),
            page=int(data["page"]),
            product_id=int(data["product_id"]),
            quantity=int(data["quantity"]),
        )
    except (ApiError, KeyError, TypeError, ValueError) as exc:
        await state.clear()
        await safe_edit(callback, f"⚠️ {html.escape(str(exc))}", back("menu:home"))
        return
    await state.clear()
    await run_market_purchase(
        message=callback.message,
        db=db,
        sous=sous,
        purchase=purchase,
        actor_id=callback.from_user.id,
    )


@router.callback_query(F.data == "mp:crypto")
async def buy_market_with_crypto(
    callback: CallbackQuery,
    state: FSMContext,
    sous: SousClient,
    db: Database,
    heleket: HeleketClient,
) -> None:
    await callback.answer()
    data = await state.get_data()
    try:
        purchase = await prepare_market_purchase(
            sous=sous,
            db=db,
            category_id=int(data["category_id"]),
            page=int(data["page"]),
            product_id=int(data["product_id"]),
            quantity=int(data["quantity"]),
        )
    except (ApiError, KeyError, TypeError, ValueError) as exc:
        await state.clear()
        await safe_edit(callback, f"⚠️ {html.escape(str(exc))}", back("menu:home"))
        return
    await state.clear()
    order_id = f"buy_{callback.from_user.id}_{uuid.uuid4().hex}"
    amount = Decimal(purchase["total"]) / Decimal("100")
    await db.create_payment(
        order_id,
        callback.from_user.id,
        int(purchase["total"]),
        purpose="market_purchase",
        purchase_payload=purchase,
    )
    public_url = (await db.get_setting("public_base_url")).rstrip("/")
    callback_url = f"{public_url}/webhooks/heleket" if public_url else ""
    try:
        invoice = await heleket.create_invoice(
            amount_rub=amount,
            order_id=order_id,
            user_id=callback.from_user.id,
            callback_url=callback_url,
        )
        url = str(invoice.get("url") or "")
        if not url:
            raise ApiError("Heleket не вернул ссылку на оплату")
        await db.update_payment_invoice(
            order_id,
            str(invoice.get("payment_status") or "check"),
            str(invoice.get("uuid") or ""),
            url,
        )
        await safe_edit(
            callback,
            "₿ <b>Оплата криптовалютой</b>\n\n"
            f"Счёт на <b>{amount:.2f} $</b> создан. После оплаты заказ будет создан автоматически.",
            payment_menu(url, order_id),
        )
    except ApiError as exc:
        await db.update_payment_invoice(order_id, "failed")
        await safe_edit(callback, f"❌ Не удалось создать счёт: {html.escape(str(exc))}", back("menu:home"))

# --- DataImpulse proxies ------------------------------------------------------

GIB = 1024 ** 3
DI_COUNT_CAP = 1000


PROXY_SECTION_OFF_NOTICE = "🌐 Раздел прокси временно закрыт."

PRICE_UNKNOWN_NOTICE = (
    "💵 Цена уточняется — закупочная стоимость этого пула ещё не получена "
    "от поставщика, покупка временно недоступна."
)


async def _di_cost_per_gb(db: Database, pool: str) -> Decimal | None:
    """The provider's price of one GB, or ``None`` while it is still unknown.

    There is deliberately no fallback figure: the shop marks up the real buying
    price, so an unknown cost must block the sale rather than invent one.
    """
    raw = await db.get_setting(cost_setting_key(pool))
    if not raw:
        return None
    try:
        cost = Decimal(str(raw).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return cost if cost > 0 else None


async def _di_price_kopecks(db: Database, pool: str) -> int:
    """Customer-facing price of one GB in cents; ``0`` means "not for sale yet"."""
    cost = await _di_cost_per_gb(db, pool)
    if cost is None:
        return 0
    rate, markup = await db.proxy_pricing(pool)
    return sale_price_kopecks(cost, rate, markup)


async def _di_prices(db: Database) -> dict[str, int]:
    return {pool: await _di_price_kopecks(db, pool) for pool in POOL_TYPES}


async def _ensure_di_subuser(
    di: DataImpulseClient, db: Database, user_id: int, pool: str
) -> dict[str, Any]:
    """The customer's own provider sub-user for this pool, created on demand."""
    row = await db.get_di_subuser(user_id, pool)
    if row:
        return row
    settings = await db.get_di_settings(user_id, pool)
    created = await di.subuser_create(
        label=f"asf{user_id} {pool}",
        pool_type=pool,
        pool_parameters=di_pool_parameters(
            str(settings.get("country") or ""),
            rotation=str(settings.get("rotation") or "rotating"),
            session_ttl=int(settings.get("session_ttl") or 0),
        ),
    )
    payload = created.get("subuser") if isinstance(created.get("subuser"), dict) else created
    try:
        subuser_id = int(payload.get("id"))
    except (TypeError, ValueError) as exc:
        raise ApiError("Сервис не создал прокси-аккаунт") from exc
    await db.save_di_subuser(
        user_id,
        pool,
        subuser_id=subuser_id,
        login=str(payload.get("login") or ""),
        password=str(payload.get("password") or ""),
    )
    row = await db.get_di_subuser(user_id, pool)
    return row or {}


async def _di_usage(di: DataImpulseClient, row: dict[str, Any]) -> tuple[float, float]:
    """(GB the customer paid for and has left, GB used) — the paid figure is
    ours, the used figure is the provider's."""
    paid = float(row.get("paid_gb") or 0)
    used = 0.0
    try:
        data = await di.subuser_balance(int(row.get("subuser_id") or 0))
        used = float(data.get("balance_used") or 0) / GIB
    except (ApiError, TypeError, ValueError):
        pass
    return max(round(paid - used, 3), 0.0), round(used, 3)


async def _di_provision(
    di: DataImpulseClient, db: Database, user_id: int, pool: str
) -> None:
    """Push whole GB to the provider so the sub-user always holds at least what
    the customer paid for.

    The provider only accepts an integer amount of traffic, so a customer who
    buys a sliver still gets a full GB provisioned — the shop must never gate
    generation behind a 1 GB minimum.
    """
    row = await db.get_di_subuser(user_id, pool)
    if not row:
        return
    paid = float(row.get("paid_gb") or 0)
    provisioned = int(row.get("provisioned_gb") or 0)
    target = max(1, math.ceil(paid - 1e-9)) if paid > 0 else 0
    if target <= provisioned:
        return
    await di.subuser_balance_add(int(row["subuser_id"]), target - provisioned)
    await db.set_di_provisioned_gb(user_id, pool, target)
    await _di_calibrate_cost(di, db, pool, int(row["subuser_id"]))


async def _di_calibrate_cost(
    di: DataImpulseClient, db: Database, pool: str, subuser_id: int
) -> None:
    """Keep ``di_cost_<pool>`` equal to what the provider actually charges.

    The reseller API has no price list, but the top-up log states the cost of
    every addition, so the shop can read its own buying price instead of
    trusting a hand-entered number — and the markup then applies to the real
    price.
    """
    try:
        rate = await di.observed_rate(subuser_id)
    except ApiError:
        return
    if not 0 < rate < 100:
        return
    current = await db.get_setting(cost_setting_key(pool))
    try:
        if current and abs(float(current) - rate) < 1e-4:
            return
    except ValueError:
        pass
    await db.set_setting(cost_setting_key(pool), f"{rate:g}")
    logger.info("dataimpulse: %s cost per GB observed = %s", pool, rate)


def _di_parse(data: str) -> tuple[str, str, str]:
    """``dm:<pool>:<action>[:<arg>]``"""
    parts = (data or "").split(":", 3)
    pool = parts[1] if len(parts) > 1 else ""
    action = parts[2] if len(parts) > 2 else "home"
    arg = parts[3] if len(parts) > 3 else ""
    return pool, action, arg


def _di_valid_pool(pool: str) -> bool:
    return pool in POOL_TYPES


async def _di_countries(di: DataImpulseClient, pool: str) -> list[dict[str, Any]]:
    cached = _DI_COUNTRY_CACHE.get(pool)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    try:
        rows = await di.countries(pool)
    except ApiError:
        rows = []
    if rows:
        _DI_COUNTRY_CACHE[pool] = (time.time(), rows)
    return rows


_DI_COUNTRY_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


async def render_di_manage(
    event: CallbackQuery | Message,
    db: Database,
    di: DataImpulseClient,
    user_id: int,
    pool: str,
    *,
    notice: str = "",
) -> None:
    settings = await db.get_di_settings(user_id, pool)
    overrides = await db.service_overrides()
    title = service_display_label(pool, overrides)
    price = await _di_price_kopecks(db, pool)
    remaining = used = 0.0
    error_text = ""
    row = await db.get_di_subuser(user_id, pool)
    if row:
        try:
            remaining, used = await _di_usage(di, row)
        except ApiError as exc:
            error_text = str(exc)

    lines = [f"<b>{html.escape(title)}</b>", ""]
    if notice:
        lines += [notice, ""]
    if error_text:
        lines += [f"⚠️ {html.escape(error_text)}", ""]
    lines.append(f"📦 Доступный трафик: <b>{remaining:.3f} GB</b>")
    if used > 0:
        lines.append(f"📉 Израсходовано: {used:.3f} GB")
    if price > 0:
        lines.append(f"💵 Пополнение: {price / 100:.2f} $ / GB")
    else:
        lines.append(PRICE_UNKNOWN_NOTICE)
    if str(settings.get("rotation")) == "sticky":
        mode = f"фикс. IP · сессия {int(settings.get('session_ttl') or 0) // 60} мин"
    else:
        mode = "ротация (смена IP каждую минуту)"
    lines += [
        "",
        f"🌍 Гео: {di_country_label(str(settings.get('country') or ''))}",
        f"🔁 Режим: {mode}",
        f"🔢 Кол-во за выдачу: {int(settings.get('proxy_count') or 1)}",
        f"🧩 Формат: {di_format_label(str(settings.get('format') or 'hpu'))}",
        "🔌 Протокол: HTTP/HTTPS (CONNECT)",
        "",
        "ℹ️ Смена пароля отключает все ранее выданные прокси.",
    ]
    markup = di_manage_menu(pool, settings, price_per_gb=price / 100)
    text = "\n".join(lines)
    if isinstance(event, CallbackQuery):
        await safe_edit(event, text, markup)
    else:
        await event.answer(text, reply_markup=markup)


async def _di_save_setting(db: Database, user_id: int, pool: str, **changes: Any) -> None:
    current = await db.get_di_settings(user_id, pool)
    current.update(changes)
    await db.save_di_settings(user_id, pool, current)


async def _di_configured(db: Database) -> bool:
    return bool(
        await db.get_setting("di_login", secret=True)
        and await db.get_setting("di_password", secret=True)
    )


async def proxy_section_prices(db: Database, sp: StrikeProxyClient) -> dict[str, int]:
    """Price per GB of every proxy section that is actually on sale.

    A provider without credentials contributes nothing, so the hub never shows a
    pool that cannot be bought.
    """
    prices: dict[str, int] = {}
    if await _di_configured(db):
        prices.update(await _di_prices(db))
    if await _sp_configured(db):
        await _sp_sync_costs(db, sp)
        prices.update(await _sp_prices(db))
    return prices


@router.callback_query(F.data == "cat:proxy")
async def proxy_root(callback: CallbackQuery, db: Database, sp: StrikeProxyClient) -> None:
    await callback.answer()
    if not await proxy_section_enabled(db):
        await safe_edit(callback, PROXY_SECTION_OFF_NOTICE, back("menu:home"))
        return
    await ensure_user(callback, db)
    prices = await proxy_section_prices(db, sp)
    overrides = await db.service_overrides()
    await safe_edit(
        callback,
        "⭐ <b>Прокси</b>\n\nВыберите тип пула. Оплата — за гигабайты трафика:",
        proxy_menu(prices, overrides),
    )


@router.callback_query(F.data.startswith("dip:"))
async def di_pool_open(callback: CallbackQuery, db: Database, di: DataImpulseClient) -> None:
    await callback.answer()
    pool = (callback.data or "").split(":", 1)[1]
    if not await proxy_section_enabled(db):
        await safe_edit(callback, PROXY_SECTION_OFF_NOTICE, back("menu:home"))
        return
    if not _di_valid_pool(pool):
        await safe_edit(callback, "Этот тип прокси недоступен.", back("cat:proxy"))
        return
    if (await db.get_service_override(pool))["hidden"]:
        await safe_edit(callback, "Этот тип прокси сейчас недоступен.", back("cat:proxy"))
        return
    await ensure_user(callback, db)
    await render_di_manage(callback, db, di, callback.from_user.id, pool)


@router.callback_query(F.data.startswith("dm:"))
async def di_manage(
    callback: CallbackQuery, db: Database, di: DataImpulseClient, state: FSMContext
) -> None:
    pool, action, arg = _di_parse(callback.data or "")
    if not _di_valid_pool(pool):
        await callback.answer()
        await safe_edit(callback, "Этот тип прокси недоступен.", back("cat:proxy"))
        return
    user_id = callback.from_user.id

    if action == "home":
        await callback.answer()
        await render_di_manage(callback, db, di, user_id, pool)
        return

    if action == "country":
        await callback.answer()
        settings = await db.get_di_settings(user_id, pool)
        rows = await _di_countries(di, pool)
        await safe_edit(
            callback,
            "Выберите гео:",
            di_country_menu(pool, rows, str(settings.get("country") or "")),
        )
        return

    if action == "country_page":
        await callback.answer()
        settings = await db.get_di_settings(user_id, pool)
        rows = await _di_countries(di, pool)
        try:
            page = max(0, int(arg))
        except ValueError:
            page = 0
        await safe_edit(
            callback,
            "Выберите гео:",
            di_country_menu(pool, rows, str(settings.get("country") or ""), page),
        )
        return

    if action == "country_set":
        await callback.answer("Гео сохранено")
        code = "" if arg in ("", "any") else arg.upper()
        await _di_save_setting(db, user_id, pool, country=code)
        await render_di_manage(callback, db, di, user_id, pool)
        return

    if action == "country_search":
        await callback.answer()
        await state.set_state(DataImpulseState.country_search)
        await state.update_data(di_pool=pool)
        await safe_edit(
            callback,
            "Пришлите название страны или её код (например <code>US</code>).",
            back(f"dm:{pool}:country", "Отмена"),
        )
        return

    if action == "rotation":
        await callback.answer()
        settings = await db.get_di_settings(user_id, pool)
        await safe_edit(
            callback, "Режим выдачи IP:", di_rotation_menu(pool, str(settings.get("rotation")))
        )
        return

    if action == "rotation_set":
        await callback.answer("Сохранено")
        await _di_save_setting(
            db, user_id, pool, rotation="sticky" if arg == "sticky" else "rotating"
        )
        await render_di_manage(callback, db, di, user_id, pool)
        return

    if action == "session":
        await callback.answer()
        settings = await db.get_di_settings(user_id, pool)
        await safe_edit(
            callback,
            "⏱ Время жизни сессии (для режима «Фикс. IP»):",
            di_session_menu(pool, int(settings.get("session_ttl") or 0)),
        )
        return

    if action == "session_set":
        await callback.answer("Сохранено")
        try:
            ttl = max(60, min(int(arg), DI_SESSION_TTL_CAP))
        except ValueError:
            ttl = 1800
        await _di_save_setting(db, user_id, pool, session_ttl=ttl)
        await render_di_manage(callback, db, di, user_id, pool)
        return

    if action == "format":
        await callback.answer()
        settings = await db.get_di_settings(user_id, pool)
        await safe_edit(
            callback, "Формат выдачи:", di_format_menu(pool, str(settings.get("format") or "hpu"))
        )
        return

    if action == "format_set":
        await callback.answer("Сохранено")
        fmt = arg if arg in DI_PROXY_FORMATS else "hpu"
        await _di_save_setting(db, user_id, pool, format=fmt)
        await render_di_manage(callback, db, di, user_id, pool)
        return

    if action == "count":
        await callback.answer()
        settings = await db.get_di_settings(user_id, pool)
        await safe_edit(
            callback,
            "Сколько прокси выдавать за раз:",
            di_count_menu(pool, int(settings.get("proxy_count") or 1)),
        )
        return

    if action == "count_set":
        await callback.answer("Сохранено")
        try:
            count = max(1, min(int(arg), DI_COUNT_CAP))
        except ValueError:
            count = 1
        await _di_save_setting(db, user_id, pool, proxy_count=count)
        await render_di_manage(callback, db, di, user_id, pool)
        return

    if action == "count_custom":
        await callback.answer()
        await state.set_state(DataImpulseState.proxy_count)
        await state.update_data(di_pool=pool)
        await safe_edit(
            callback,
            f"Пришлите количество прокси (1–{DI_COUNT_CAP}).",
            back(f"dm:{pool}:home", "Отмена"),
        )
        return

    if action == "reset":
        await callback.answer("Настройки сброшены")
        await db.save_di_settings(user_id, pool, dict(Database.DEFAULT_DI_SETTINGS))
        await render_di_manage(callback, db, di, user_id, pool)
        return

    if action == "get":
        await callback.answer("Генерирую прокси…")
        await _di_deliver(callback, db, di, user_id, pool)
        return

    if action == "topup":
        await callback.answer()
        unit = await _di_price_kopecks(db, pool)
        if unit <= 0:
            await safe_edit(callback, PRICE_UNKNOWN_NOTICE, back(f"dm:{pool}:home"))
            return
        prices = {gb: max(int(round(unit * gb)), 1) for gb in DI_TOPUP_GB}
        await safe_edit(
            callback,
            f"➕ <b>Пополнить трафик</b>\n\nЦена: {unit / 100:.2f} $ / GB.\nВыберите объём:",
            di_topup_menu(pool, prices),
        )
        return

    if action == "topup_custom":
        await callback.answer()
        await state.set_state(DataImpulseState.topup_amount)
        await state.update_data(di_pool=pool)
        await safe_edit(
            callback,
            f"Пришлите объём трафика в GB (от {DI_TOPUP_MIN_GB:g}).\n"
            "Спишется с баланса по текущей цене за GB.",
            back(f"dm:{pool}:home", "Отмена"),
        )
        return

    if action == "topup_buy":
        await callback.answer()
        if not isinstance(callback.message, Message):
            return
        try:
            gb = round(float(arg), 2)
            if gb <= 0:
                raise ValueError
        except ValueError:
            await safe_edit(callback, "⚠️ Некорректный объём", back(f"dm:{pool}:home"))
            return
        await _di_topup_execute(callback.message, db, di, user_id, pool, gb)
        return

    if action == "password":
        await callback.answer()
        await safe_edit(
            callback,
            "🔑 <b>Смена пароля</b>\n\n"
            "Будут выпущены новые логин и пароль. Все ранее полученные прокси перестанут "
            "работать — после смены нажмите «Получить прокси» и заберите новый список.\n\n"
            "Продолжить?",
            di_password_confirm(pool),
        )
        return

    if action == "password_go":
        await callback.answer("Меняю пароль…")
        reply = callback.message.answer if isinstance(callback.message, Message) else None
        row = await db.get_di_subuser(user_id, pool)
        if not row:
            if reply:
                await reply("⚠️ Прокси-аккаунт ещё не создан.")
            return
        try:
            res = await di.subuser_reset_password(int(row["subuser_id"]))
        except ApiError as exc:
            if reply:
                await reply(f"⚠️ {html.escape(str(exc))}")
            return
        payload = res.get("subuser") if isinstance(res.get("subuser"), dict) else res
        await db.set_di_credentials(
            user_id, pool, str(payload.get("login") or ""), str(payload.get("password") or "")
        )
        if reply:
            await reply("✅ Пароль изменён. Старые прокси больше не работают.")
        await render_di_manage(callback, db, di, user_id, pool)
        return

    await callback.answer()


async def _di_deliver(
    callback: CallbackQuery, db: Database, di: DataImpulseClient, user_id: int, pool: str
) -> None:
    reply = callback.message.answer if isinstance(callback.message, Message) else None
    row = await db.get_di_subuser(user_id, pool)
    if not row or float(row.get("paid_gb") or 0) <= 0:
        if reply:
            await reply("⚠️ Нет трафика. Нажмите «Пополнить трафик».")
        return
    try:
        remaining, _used = await _di_usage(di, row)
    except ApiError as exc:
        if reply:
            await reply(f"⚠️ {html.escape(str(exc))}")
        return
    if remaining <= 0:
        if reply:
            await reply("⚠️ Трафик закончился. Пополните, чтобы получить новые прокси.")
        return
    settings = await db.get_di_settings(user_id, pool)
    lines = di_build_lines(
        str(row.get("login") or ""),
        str(row.get("password") or ""),
        count=int(settings.get("proxy_count") or 1),
        country=str(settings.get("country") or ""),
        rotation=str(settings.get("rotation") or "rotating"),
        session_ttl=int(settings.get("session_ttl") or 0),
        fmt=str(settings.get("format") or "hpu"),
    )
    if not lines:
        if reply:
            await reply("⚠️ Не удалось сформировать прокси.")
        return
    if reply:
        file = BufferedInputFile("\n".join(lines).encode(), filename=f"{pool}_proxies.txt")
        await callback.message.answer_document(
            file, caption=f"{html.escape(pool_label(pool))} · {len(lines)} шт"
        )
    await render_di_manage(callback, db, di, user_id, pool)


async def _di_topup_execute(
    msg: Message,
    db: Database,
    di: DataImpulseClient,
    user_id: int,
    pool: str,
    gb: float,
) -> None:
    """Charge the customer's shop balance and push the traffic upstream."""
    gb = round(float(gb), 2)
    unit = await _di_price_kopecks(db, pool)
    if unit <= 0:
        # Last line of defence: never charge against a price we did not get
        # from the provider.
        await msg.answer(PRICE_UNKNOWN_NOTICE, reply_markup=back(f"dm:{pool}:home"))
        return
    total = max(int(round(unit * gb)), 1)
    request_id = f"di_{pool}_{user_id}_{uuid.uuid4().hex}"
    try:
        await _ensure_di_subuser(di, db, user_id, pool)
    except ApiError as exc:
        await msg.answer(f"⚠️ {html.escape(str(exc))}", reply_markup=back(f"dm:{pool}:home"))
        return
    try:
        order_id = await db.reserve_order(
            request_id=request_id,
            user_id=user_id,
            kind="proxy_service",
            title=f"{pool} +{gb:g} GB",
            quantity=gb,
            unit_price_kopecks=max(int(total / gb), 1),
            total_kopecks=total,
            request={"provider": "dataimpulse", "pool": pool, "gb": gb},
        )
    except InsufficientFunds:
        await msg.answer(
            f"Недостаточно средств. Нужно <b>{rub(total)} $</b>.",
            reply_markup=back("menu:topup", "Пополнить баланс"),
        )
        return
    await msg.answer(f"⏳ Пополнение №{order_id}…")
    await db.add_di_paid_gb(user_id, pool, gb)
    try:
        await _di_provision(di, db, user_id, pool)
    except ApiError as exc:
        if not exc.uncertain:
            # A clean failure means no traffic was added, so take the credit back.
            # An uncertain one may still have landed upstream — leave it for the
            # admin to settle rather than silently taking paid GB away.
            await db.add_di_paid_gb(user_id, pool, -gb)
        await db.fail_order(
            order_id, {"error": str(exc), "status": exc.status}, uncertain=exc.uncertain
        )
        if exc.uncertain:
            await msg.answer(
                f"⚠️ Пополнение №{order_id}: статус неясен, передано администратору. "
                "Средства зарезервированы."
            )
        else:
            await msg.answer(
                f"❌ Пополнение №{order_id} не выполнено: {html.escape(str(exc))}. "
                "Средства возвращены."
            )
        return
    await db.complete_order(
        order_id, external_id=request_id, response={"pool": pool, "gb": gb},
        delivery="", status="completed",
    )
    await msg.answer(f"✅ Добавлено {gb:g} GB.")
    await render_di_manage(msg, db, di, user_id, pool)


@router.message(DataImpulseState.country_search)
async def di_country_search_input(
    message: Message, state: FSMContext, db: Database, di: DataImpulseClient
) -> None:
    data = await state.get_data()
    pool = str(data.get("di_pool") or "")
    await state.clear()
    if not _di_valid_pool(pool):
        return
    rows = di_filter_countries(await _di_countries(di, pool), message.text or "")
    if not rows:
        await message.answer("Ничего не найдено.", reply_markup=back(f"dm:{pool}:country"))
        return
    await message.answer(
        "Найдено:", reply_markup=di_country_results_menu(pool, rows[:20])
    )


@router.message(DataImpulseState.proxy_count)
async def di_count_input(
    message: Message, state: FSMContext, db: Database, di: DataImpulseClient
) -> None:
    data = await state.get_data()
    pool = str(data.get("di_pool") or "")
    await state.clear()
    if not _di_valid_pool(pool):
        return
    try:
        count = int((message.text or "").strip())
        if count < 1 or count > DI_COUNT_CAP:
            raise ValueError
    except ValueError:
        await message.answer(
            f"Нужно целое число от 1 до {DI_COUNT_CAP}.", reply_markup=back(f"dm:{pool}:home")
        )
        return
    await _di_save_setting(db, message_user_id(message), pool, proxy_count=count)
    await render_di_manage(message, db, di, message_user_id(message), pool)


@router.message(DataImpulseState.topup_amount)
async def di_topup_input(
    message: Message, state: FSMContext, db: Database, di: DataImpulseClient
) -> None:
    data = await state.get_data()
    pool = str(data.get("di_pool") or "")
    await state.clear()
    if not _di_valid_pool(pool):
        return
    try:
        gb = round(float((message.text or "").replace(",", ".").strip()), 2)
        if gb < DI_TOPUP_MIN_GB or gb > 10000:
            raise ValueError
    except ValueError:
        await message.answer(
            f"Нужно число от {DI_TOPUP_MIN_GB:g} до 10000 GB.",
            reply_markup=back(f"dm:{pool}:home"),
        )
        return
    await _di_topup_execute(message, db, di, message_user_id(message), pool, gb)


# --- StrikeProxy proxies ------------------------------------------------------
#
# Same customer experience as the DataImpulse pools, but the provider sells
# *services*: the first purchase creates one (with its own proxy login/password
# and a GB allowance) and later top-ups extend the same service.

SP_COUNT_CAP = 1000

# The provider publishes the account's real reseller price list, so the buying
# price is refreshed from it instead of being typed in by hand.
SP_COST_REFRESH_SECONDS = 6 * 3600
_SP_COST_REFRESHED_AT = 0.0

# One /reseller/services call returns every service on the account, so a short
# cache keeps menu renders from hammering the provider.
SP_SERVICES_CACHE_SECONDS = 30
_SP_SERVICES_CACHE: tuple[float, list[dict[str, Any]]] = (0.0, [])

_SP_COUNTRY_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}


async def _sp_configured(db: Database) -> bool:
    return bool(await db.get_setting("sp_api_key", secret=True))


async def _sp_sync_costs(db: Database, sp: StrikeProxyClient, *, force: bool = False) -> None:
    """Pin the provider's own reseller rate per plan into settings."""
    global _SP_COST_REFRESHED_AT
    if not force and time.time() - _SP_COST_REFRESHED_AT < SP_COST_REFRESH_SECONDS:
        return
    try:
        costs = await sp.plan_costs()
    except ApiError:
        return
    _SP_COST_REFRESHED_AT = time.time()
    for plan, price in costs.items():
        current = await db.get_setting(sp_cost_setting_key(plan))
        try:
            if current and abs(float(current) - price) < 1e-6:
                continue
        except ValueError:
            pass
        await db.set_setting(sp_cost_setting_key(plan), f"{price:g}")
        logger.info("strikeproxy: %s cost per GB = %s", plan, price)


def _sp_decimal(raw: str) -> Decimal | None:
    if not raw:
        return None
    try:
        value = Decimal(str(raw).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None


async def _sp_cost_per_gb(db: Database, plan: str) -> Decimal | None:
    """What a gigabyte of this pool really costs the shop.

    The provider bills a fresh order and a top-up of an existing service at
    different rates, and a customer's second purchase is always a top-up — so
    the price has to be built on the dearer of the two, otherwise the shop sells
    the repeat business below cost.
    """
    order_cost = _sp_decimal(await db.get_setting(sp_cost_setting_key(plan)))
    if order_cost is not None:
        return order_cost
    return _sp_decimal(await db.get_setting(sp_topup_cost_setting_key(plan)))


async def _sp_price_kopecks(db: Database, plan: str) -> int:
    """Customer-facing price of one GB in cents; ``0`` means "not for sale"."""
    cost = await _sp_cost_per_gb(db, plan)
    if cost is None:
        return 0
    rate, markup = await db.proxy_pricing(sp_section_code(plan))
    return sale_price_kopecks(cost, rate, markup)


async def _sp_prices(db: Database) -> dict[str, int]:
    return {sp_section_code(plan): await _sp_price_kopecks(db, plan) for plan in SP_PLAN_TYPES}


async def _sp_services(sp: StrikeProxyClient, *, force: bool = False) -> list[dict[str, Any]]:
    global _SP_SERVICES_CACHE
    stamp, rows = _SP_SERVICES_CACHE
    if not force and rows and time.time() - stamp < SP_SERVICES_CACHE_SECONDS:
        return rows
    fresh = await sp.services()
    _SP_SERVICES_CACHE = (time.time(), fresh)
    return fresh


def _sp_invalidate_services() -> None:
    global _SP_SERVICES_CACHE
    _SP_SERVICES_CACHE = (0.0, [])


async def _sp_remote_service(
    sp: StrikeProxyClient, row: dict[str, Any], *, force: bool = False
) -> dict[str, Any] | None:
    username = str(row.get("proxy_username") or "")
    service_id = int(row.get("service_id") or 0)
    for service in await _sp_services(sp, force=force):
        if username and str(service.get("proxy_username") or "") == username:
            return service
        if service_id and str(service.get("id") or "") == str(service_id):
            return service
    return None


async def _sp_usage(
    sp: StrikeProxyClient, db: Database, user_id: int, plan: str, row: dict[str, Any]
) -> tuple[float, float]:
    """(GB the customer has left, GB used) — paid is ours, used is upstream."""
    paid = float(row.get("paid_gb") or 0)
    used = 0.0
    if row.get("proxy_username"):
        service = await _sp_remote_service(sp, row)
        if service:
            try:
                used = float(service.get("used_gb") or 0)
            except (TypeError, ValueError):
                used = 0.0
            remote_id = int(service.get("id") or 0)
            if remote_id and remote_id != int(row.get("service_id") or 0):
                await db.set_sp_service_id(user_id, plan, remote_id)
                row["service_id"] = remote_id
    return max(round(paid - used, 3), 0.0), round(used, 3)


async def _sp_ensure_row(db: Database, user_id: int, plan: str) -> dict[str, Any]:
    """The customer's local service record. Credentials stay empty until the
    first paid GB actually buys a service upstream."""
    row = await db.get_sp_service(user_id, plan)
    if row:
        return row
    await db.save_sp_service(user_id, plan, service_id=0, proxy_username="", proxy_password="")
    return await db.get_sp_service(user_id, plan) or {}


# A single purchase's charged/GB reading must never swing the shop-wide price on
# its own: on 2026-09-24 a 10 GB mobile top-up came back charged at 1.4 $/GB
# against a catalog price of 0.8, calibration overwrote the shared
# sp_cost_mobile setting, and every buyer saw 2.80 $/GB until an unrelated
# customer's fresh order recalibrated it back down 22 minutes later.
#
# That thrashing had two causes and both are fixed: the 1.4 was not noise but
# the provider's real top-up rate (verified twice — a 10 GB top-up billed
# exactly 14.00, a 1 GB one exactly 1.40), and it was being written over the
# *order* rate. Each rate now lives under its own key, so a reading is only ever
# compared with readings of the same kind, and a wild outlier is still ignored.
SP_CALIBRATE_MAX_DEVIATION = 0.25


async def _sp_calibrate_cost(
    db: Database, plan: str, cost: Any, gb: float, *, topup: bool
) -> None:
    """Record what the provider actually charged for this purchase.

    The catalog gives a price, but the charged amount is the truth — and it
    differs between a new order and a top-up, so each goes to its own setting
    instead of one overwriting the other. A reading that is wildly off the
    previous one *of the same kind* is logged rather than applied.
    """
    try:
        charged = float(cost)
    except (TypeError, ValueError):
        return
    if charged <= 0 or gb <= 0:
        return
    rate = round(charged / gb, 4)
    if not 0 < rate < 1000:
        return
    key = sp_topup_cost_setting_key(plan) if topup else sp_cost_setting_key(plan)
    current = await db.get_setting(key)
    try:
        current_rate = float(current) if current else None
    except ValueError:
        current_rate = None
    if current_rate is not None and abs(current_rate - rate) < 1e-4:
        return
    if (
        current_rate is not None
        and abs(rate - current_rate) > current_rate * SP_CALIBRATE_MAX_DEVIATION
    ):
        logger.warning(
            "strikeproxy: %s charged rate per GB (%s) = %s deviates from %s by more than "
            "%.0f%% -- ignoring, shop price unchanged (check for a coupon/retry/provider glitch)",
            plan,
            "докупка" if topup else "заказ",
            rate,
            current_rate,
            SP_CALIBRATE_MAX_DEVIATION * 100,
        )
        return
    await db.set_setting(key, f"{rate:g}")
    logger.info(
        "strikeproxy: %s charged rate per GB (%s) = %s",
        plan,
        "докупка" if topup else "заказ",
        rate,
    )


def _sp_service_gb(service: dict[str, Any]) -> float:
    """GB the provider says a service holds."""
    for key in ("purchased_gb", "max_gb"):
        try:
            value = float(service.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


async def _sp_orphan_service(
    sp: StrikeProxyClient, db: Database, plan: str
) -> dict[str, Any] | None:
    """A provider service of this plan that no customer owns.

    When a purchase POST dies on a gateway error the order may still have gone
    through upstream. The services list is the only way to tell, and an
    unclaimed service is exactly that lost purchase.
    """
    taken = await db.sp_taken_usernames()
    for service in await _sp_services(sp, force=True):
        if str(service.get("plan_type") or "") != plan:
            continue
        username = str(service.get("proxy_username") or "")
        if username and username not in taken:
            return service
    return None


async def _sp_adopt_service(
    db: Database, user_id: int, plan: str, service: dict[str, Any]
) -> None:
    try:
        service_id = int(service.get("id") or 0)
    except (TypeError, ValueError):
        service_id = 0
    await db.save_sp_service(
        user_id,
        plan,
        service_id=service_id,
        proxy_username=str(service.get("proxy_username") or ""),
        proxy_password=str(service.get("proxy_password") or ""),
    )


async def _sp_provision(sp: StrikeProxyClient, db: Database, user_id: int, plan: str) -> None:
    """Make sure the upstream service holds at least the GB the customer paid.

    The provider bills whole gigabytes, so a customer who buys a sliver still
    gets a full GB provisioned — the shop must never gate generation behind a
    1 GB minimum.

    A write that fails with a gateway error is never assumed to be a no-op: the
    provider is asked what it actually holds before the customer is refunded,
    because a blind rollback turns a charged-but-lost purchase into money the
    shop pays for and the buyer never sees.
    """
    row = await db.get_sp_service(user_id, plan)
    if not row:
        return
    paid = float(row.get("paid_gb") or 0)
    provisioned = float(row.get("provisioned_gb") or 0)
    target = max(1.0, math.ceil(paid - 1e-9)) if paid > 0 else 0.0
    delta = int(round(target - provisioned))
    if delta <= 0:
        return

    username = str(row.get("proxy_username") or "")
    result: dict[str, Any] = {}
    for attempt in (1, 2):
        try:
            if username:
                result = await sp.add_bandwidth(username, delta)
            else:
                result = await sp.order(plan, delta)
                service = (
                    result.get("service") if isinstance(result.get("service"), dict) else result
                )
                proxy_username = str(service.get("proxy_username") or "")
                if not proxy_username:
                    raise ApiError("Сервис не выдал прокси-аккаунт", uncertain=True)
                await _sp_adopt_service(db, user_id, plan, service)
            break
        except ApiError as exc:
            landed = await _sp_verify_provisioned(
                sp, db, user_id, plan, row, username=username, want_gb=provisioned + delta
            )
            if landed is True:
                # It went through despite the error — no second charge, no refund.
                result = {}
                break
            if landed is None:
                # Could not reach the provider to check: hand it to the admin
                # instead of silently refunding a purchase that may exist.
                exc.uncertain = True
                raise
            if attempt == 1 and exc.status >= 500:
                logger.warning(
                    "strikeproxy: %s provisioning failed with %s, nothing landed — retrying",
                    plan,
                    exc.status,
                )
                await asyncio.sleep(3)
                continue
            # Verified that the provider holds nothing extra: a clean failure,
            # the customer gets their money back.
            exc.uncertain = False
            raise

    await db.add_sp_provisioned_gb(user_id, plan, delta)
    _sp_invalidate_services()
    await _sp_calibrate_cost(
        db, plan, result.get("cost"), float(delta), topup=bool(username)
    )

    # The order response does not always carry the service id; the services list
    # does, and generation needs it.
    row = await db.get_sp_service(user_id, plan) or {}
    if not int(row.get("service_id") or 0):
        try:
            service = await _sp_remote_service(sp, row, force=True)
        except ApiError:
            service = None
        if service:
            try:
                await db.set_sp_service_id(user_id, plan, int(service.get("id") or 0))
            except (TypeError, ValueError):
                pass


async def _sp_verify_provisioned(
    sp: StrikeProxyClient,
    db: Database,
    user_id: int,
    plan: str,
    row: dict[str, Any],
    *,
    username: str,
    want_gb: float,
) -> bool | None:
    """Did the failed write land anyway?

    ``True`` — the provider already holds the traffic (adopted if it was a new
    service), ``False`` — it holds nothing extra, ``None`` — the provider could
    not be asked, so nothing may be assumed either way.
    """
    try:
        if username:
            service = await _sp_remote_service(sp, row, force=True)
            if service is None:
                return False
            return _sp_service_gb(service) >= want_gb - 0.01
        service = await _sp_orphan_service(sp, db, plan)
    except ApiError:
        return None
    if not service:
        return False
    if _sp_service_gb(service) < want_gb - 0.01:
        # An unclaimed service that is too small is not this purchase — it may
        # be left over from an admin test. Handing it over would give the buyer
        # less traffic than they paid for.
        logger.warning(
            "strikeproxy: ignoring unclaimed %s service %s — holds %s GB, order needs %s GB",
            plan,
            service.get("proxy_username"),
            _sp_service_gb(service),
            want_gb,
        )
        return False
    await _sp_adopt_service(db, user_id, plan, service)
    logger.warning(
        "strikeproxy: adopted orphaned %s service %s for user %s after a failed order",
        plan,
        service.get("proxy_username"),
        user_id,
    )
    return True


def _sp_parse(data: str) -> tuple[str, str, str]:
    """``sm:<plan>:<action>[:<arg>]``"""
    parts = (data or "").split(":", 3)
    plan = parts[1] if len(parts) > 1 else ""
    action = parts[2] if len(parts) > 2 else "home"
    arg = parts[3] if len(parts) > 3 else ""
    return plan, action, arg


def _sp_valid_plan(plan: str) -> bool:
    return plan in SP_PLAN_TYPES


async def _sp_countries(sp: StrikeProxyClient, plan: str) -> list[dict[str, Any]]:
    cached = _SP_COUNTRY_CACHE.get(plan)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    try:
        rows = await sp.locations(plan)
    except ApiError:
        rows = []
    if rows:
        rows.sort(key=lambda row: str(row.get("name") or ""))
        _SP_COUNTRY_CACHE[plan] = (time.time(), rows)
    return rows


async def _sp_save_setting(db: Database, user_id: int, plan: str, **changes: Any) -> None:
    current = await db.get_sp_settings(user_id, plan)
    current.update(changes)
    await db.save_sp_settings(user_id, plan, current)


async def render_sp_manage(
    event: CallbackQuery | Message,
    db: Database,
    sp: StrikeProxyClient,
    user_id: int,
    plan: str,
    *,
    notice: str = "",
) -> None:
    settings = await db.get_sp_settings(user_id, plan)
    overrides = await db.service_overrides()
    title = service_display_label(sp_section_code(plan), overrides)
    price = await _sp_price_kopecks(db, plan)
    remaining = used = 0.0
    error_text = ""
    row = await db.get_sp_service(user_id, plan)
    if row:
        try:
            remaining, used = await _sp_usage(sp, db, user_id, plan, row)
        except ApiError as exc:
            error_text = str(exc)

    lines = [f"<b>{html.escape(title)}</b>", ""]
    if notice:
        lines += [notice, ""]
    if error_text:
        lines += [f"⚠️ {html.escape(error_text)}", ""]
    lines.append(f"📦 Доступный трафик: <b>{remaining:.3f} GB</b>")
    if used > 0:
        lines.append(f"📉 Израсходовано: {used:.3f} GB")
    if price > 0:
        lines.append(f"💵 Пополнение: {price / 100:.2f} $ / GB")
    else:
        lines.append(PRICE_UNKNOWN_NOTICE)
    if str(settings.get("rotation")) == "sticky":
        mode = f"фикс. IP · сессия {int(settings.get('session_ttl') or 0) // 60} мин"
    else:
        mode = "ротация (смена IP каждую минуту)"
    lines += [
        "",
        f"🌍 Гео: {sp_country_label(str(settings.get('country') or ''))}",
        f"🔁 Режим: {mode}",
        f"🔢 Кол-во за выдачу: {int(settings.get('proxy_count') or 1)}",
        f"🧩 Формат: {sp_format_label(str(settings.get('format') or 'hpu'))}",
        "🔌 Протокол: HTTP/HTTPS (CONNECT)",
        "",
        "ℹ️ Смена пароля отключает все ранее выданные прокси.",
    ]
    markup = sp_manage_menu(plan, settings, price_per_gb=price / 100)
    text = "\n".join(lines)
    if isinstance(event, CallbackQuery):
        await safe_edit(event, text, markup)
    else:
        await event.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("spp:"))
async def sp_plan_open(callback: CallbackQuery, db: Database, sp: StrikeProxyClient) -> None:
    await callback.answer()
    plan = (callback.data or "").split(":", 1)[1]
    if not await proxy_section_enabled(db):
        await safe_edit(callback, PROXY_SECTION_OFF_NOTICE, back("menu:home"))
        return
    if not _sp_valid_plan(plan):
        await safe_edit(callback, "Этот тип прокси недоступен.", back("cat:proxy"))
        return
    if (await db.get_service_override(sp_section_code(plan)))["hidden"]:
        await safe_edit(callback, "Этот тип прокси сейчас недоступен.", back("cat:proxy"))
        return
    await ensure_user(callback, db)
    await render_sp_manage(callback, db, sp, callback.from_user.id, plan)


@router.callback_query(F.data.startswith("sm:"))
async def sp_manage(
    callback: CallbackQuery,
    db: Database,
    sp: StrikeProxyClient,
    state: FSMContext,
    config: Config,
) -> None:
    plan, action, arg = _sp_parse(callback.data or "")
    if not _sp_valid_plan(plan):
        await callback.answer()
        await safe_edit(callback, "Этот тип прокси недоступен.", back("cat:proxy"))
        return
    user_id = callback.from_user.id

    if action == "home":
        await callback.answer()
        await render_sp_manage(callback, db, sp, user_id, plan)
        return

    if action == "country":
        await callback.answer()
        settings = await db.get_sp_settings(user_id, plan)
        rows = await _sp_countries(sp, plan)
        await safe_edit(
            callback,
            "Выберите гео:",
            sp_country_menu(plan, rows, str(settings.get("country") or "")),
        )
        return

    if action == "country_page":
        await callback.answer()
        settings = await db.get_sp_settings(user_id, plan)
        rows = await _sp_countries(sp, plan)
        try:
            page = max(0, int(arg))
        except ValueError:
            page = 0
        await safe_edit(
            callback,
            "Выберите гео:",
            sp_country_menu(plan, rows, str(settings.get("country") or ""), page),
        )
        return

    if action == "country_set":
        await callback.answer("Гео сохранено")
        code = "" if arg in ("", "any") else arg.upper()
        await _sp_save_setting(db, user_id, plan, country=code)
        await render_sp_manage(callback, db, sp, user_id, plan)
        return

    if action == "country_search":
        await callback.answer()
        await state.set_state(StrikeProxyState.country_search)
        await state.update_data(sp_plan=plan)
        await safe_edit(
            callback,
            "Пришлите название страны или её код (например <code>US</code>).",
            back(f"sm:{plan}:country", "Отмена"),
        )
        return

    if action == "rotation":
        await callback.answer()
        settings = await db.get_sp_settings(user_id, plan)
        await safe_edit(
            callback, "Режим выдачи IP:", sp_rotation_menu(plan, str(settings.get("rotation")))
        )
        return

    if action == "rotation_set":
        await callback.answer("Сохранено")
        await _sp_save_setting(
            db, user_id, plan, rotation="sticky" if arg == "sticky" else "rotating"
        )
        await render_sp_manage(callback, db, sp, user_id, plan)
        return

    if action == "session":
        await callback.answer()
        settings = await db.get_sp_settings(user_id, plan)
        await safe_edit(
            callback,
            "⏱ Время жизни сессии (для режима «Фикс. IP»):",
            sp_session_menu(plan, int(settings.get("session_ttl") or 0)),
        )
        return

    if action == "session_set":
        await callback.answer("Сохранено")
        try:
            ttl = max(60, min(int(arg), SP_SESSION_MINUTES_CAP * 60))
        except ValueError:
            ttl = 1800
        await _sp_save_setting(db, user_id, plan, session_ttl=ttl)
        await render_sp_manage(callback, db, sp, user_id, plan)
        return

    if action == "format":
        await callback.answer()
        settings = await db.get_sp_settings(user_id, plan)
        await safe_edit(
            callback, "Формат выдачи:", sp_format_menu(plan, str(settings.get("format") or "hpu"))
        )
        return

    if action == "format_set":
        await callback.answer("Сохранено")
        fmt = arg if arg in SP_PROXY_FORMATS else "hpu"
        await _sp_save_setting(db, user_id, plan, format=fmt)
        await render_sp_manage(callback, db, sp, user_id, plan)
        return

    if action == "count":
        await callback.answer()
        settings = await db.get_sp_settings(user_id, plan)
        await safe_edit(
            callback,
            "Сколько прокси выдавать за раз:",
            sp_count_menu(plan, int(settings.get("proxy_count") or 1)),
        )
        return

    if action == "count_set":
        await callback.answer("Сохранено")
        try:
            count = max(1, min(int(arg), SP_COUNT_CAP))
        except ValueError:
            count = 1
        await _sp_save_setting(db, user_id, plan, proxy_count=count)
        await render_sp_manage(callback, db, sp, user_id, plan)
        return

    if action == "count_custom":
        await callback.answer()
        await state.set_state(StrikeProxyState.proxy_count)
        await state.update_data(sp_plan=plan)
        await safe_edit(
            callback,
            f"Пришлите количество прокси (1–{SP_COUNT_CAP}).",
            back(f"sm:{plan}:home", "Отмена"),
        )
        return

    if action == "reset":
        await callback.answer("Настройки сброшены")
        await db.save_sp_settings(user_id, plan, dict(Database.DEFAULT_SP_SETTINGS))
        await render_sp_manage(callback, db, sp, user_id, plan)
        return

    if action == "get":
        await callback.answer("Генерирую прокси…")
        await _sp_deliver(callback, db, sp, user_id, plan)
        return

    if action == "topup":
        await callback.answer()
        unit = await _sp_price_kopecks(db, plan)
        if unit <= 0:
            await safe_edit(callback, PRICE_UNKNOWN_NOTICE, back(f"sm:{plan}:home"))
            return
        prices = {gb: max(int(round(unit * gb)), 1) for gb in SP_TOPUP_GB}
        await safe_edit(
            callback,
            f"➕ <b>Пополнить трафик</b>\n\nЦена: {unit / 100:.2f} $ / GB.\nВыберите объём:",
            sp_topup_menu(plan, prices),
        )
        return

    if action == "topup_custom":
        await callback.answer()
        await state.set_state(StrikeProxyState.topup_amount)
        await state.update_data(sp_plan=plan)
        await safe_edit(
            callback,
            f"Пришлите объём трафика в GB (от {SP_TOPUP_MIN_GB:g}).\n"
            "Спишется с баланса по текущей цене за GB.",
            back(f"sm:{plan}:home", "Отмена"),
        )
        return

    if action == "topup_buy":
        await callback.answer()
        if not isinstance(callback.message, Message):
            return
        try:
            gb = round(float(arg), 2)
            if gb <= 0:
                raise ValueError
        except ValueError:
            await safe_edit(callback, "⚠️ Некорректный объём", back(f"sm:{plan}:home"))
            return
        await _sp_topup_execute(
            callback.message, db, sp, user_id, plan, gb, config.admin_ids
        )
        return

    if action == "password":
        await callback.answer()
        await safe_edit(
            callback,
            "🔑 <b>Смена пароля</b>\n\n"
            "Будет выпущен новый пароль. Все ранее полученные прокси перестанут "
            "работать — после смены нажмите «Получить прокси» и заберите новый список.\n\n"
            "Продолжить?",
            sp_password_confirm(plan),
        )
        return

    if action == "password_go":
        await callback.answer("Меняю пароль…")
        reply = callback.message.answer if isinstance(callback.message, Message) else None
        row = await db.get_sp_service(user_id, plan)
        if not row or not row.get("proxy_username"):
            if reply:
                await reply("⚠️ Прокси-аккаунт ещё не создан.")
            return
        password = secrets.token_urlsafe(12)
        try:
            await sp.update_password(str(row["proxy_username"]), password)
        except ApiError as exc:
            if reply:
                await reply(f"⚠️ {html.escape(str(exc))}")
            return
        await db.set_sp_password(user_id, plan, password)
        _sp_invalidate_services()
        if reply:
            await reply("✅ Пароль изменён. Старые прокси больше не работают.")
        await render_sp_manage(callback, db, sp, user_id, plan)
        return

    await callback.answer()


async def _sp_deliver(
    callback: CallbackQuery, db: Database, sp: StrikeProxyClient, user_id: int, plan: str
) -> None:
    reply = callback.message.answer if isinstance(callback.message, Message) else None
    row = await db.get_sp_service(user_id, plan)
    if not row or float(row.get("paid_gb") or 0) <= 0 or not row.get("proxy_username"):
        if reply:
            await reply("⚠️ Нет трафика. Нажмите «Пополнить трафик».")
        return
    try:
        remaining, _used = await _sp_usage(sp, db, user_id, plan, row)
    except ApiError as exc:
        if reply:
            await reply(f"⚠️ {html.escape(str(exc))}")
        return
    if remaining <= 0:
        if reply:
            await reply("⚠️ Трафик закончился. Пополните, чтобы получить новые прокси.")
        return
    service_id = int(row.get("service_id") or 0)
    if not service_id:
        if reply:
            await reply("⚠️ Прокси-аккаунт ещё не готов, попробуйте через минуту.")
        return
    settings = await db.get_sp_settings(user_id, plan)
    try:
        raw = await sp.generate(
            service_id,
            count=int(settings.get("proxy_count") or 1),
            country=str(settings.get("country") or ""),
            rotation=str(settings.get("rotation") or "rotating"),
            session_ttl=int(settings.get("session_ttl") or 0),
        )
    except ApiError as exc:
        if reply:
            await reply(f"⚠️ {html.escape(str(exc))}")
        return
    lines = sp_rewrite_lines(raw, str(settings.get("format") or "hpu"))
    if not lines:
        if reply:
            await reply("⚠️ Не удалось сформировать прокси.")
        return
    if reply:
        file = BufferedInputFile("\n".join(lines).encode(), filename=f"{plan}_proxies.txt")
        await callback.message.answer_document(
            file, caption=f"{html.escape(sp_plan_label(plan))} · {len(lines)} шт"
        )
    await render_sp_manage(callback, db, sp, user_id, plan)


# A provider outage must reach the owner from the bot, not from a customer
# complaining. Failures are throttled per plan so a retrying buyer cannot spam.
SP_ALERT_THROTTLE_SECONDS = 900
_SP_ALERTED_AT: dict[str, float] = {}


async def _sp_alert_admins(
    msg: Message, admin_ids: Iterable[int], text: str, *, throttle_key: str = ""
) -> None:
    if throttle_key:
        last = _SP_ALERTED_AT.get(throttle_key, 0.0)
        if time.time() - last < SP_ALERT_THROTTLE_SECONDS:
            return
        _SP_ALERTED_AT[throttle_key] = time.time()
    for admin_id in admin_ids:
        try:
            await msg.bot.send_message(admin_id, text)
        except TelegramBadRequest:
            continue
        except Exception:  # noqa: BLE001 - a dead admin chat must not break a sale
            logger.warning("strikeproxy: could not alert admin %s", admin_id, exc_info=True)


async def _sp_topup_execute(
    msg: Message,
    db: Database,
    sp: StrikeProxyClient,
    user_id: int,
    plan: str,
    gb: float,
    admin_ids: Iterable[int] = (),
) -> None:
    """Charge the customer's shop balance and buy the traffic upstream."""
    gb = round(float(gb), 2)
    unit = await _sp_price_kopecks(db, plan)
    if unit <= 0:
        # Last line of defence: never charge against a price we did not get
        # from the provider.
        await msg.answer(PRICE_UNKNOWN_NOTICE, reply_markup=back(f"sm:{plan}:home"))
        return
    total = max(int(round(unit * gb)), 1)
    request_id = f"sp_{plan}_{user_id}_{uuid.uuid4().hex}"
    await _sp_ensure_row(db, user_id, plan)
    try:
        order_id = await db.reserve_order(
            request_id=request_id,
            user_id=user_id,
            kind="proxy_service",
            title=f"{plan} +{gb:g} GB",
            quantity=gb,
            unit_price_kopecks=max(int(total / gb), 1),
            total_kopecks=total,
            request={"provider": "strikeproxy", "plan": plan, "gb": gb},
        )
    except InsufficientFunds:
        await msg.answer(
            f"Недостаточно средств. Нужно <b>{rub(total)} $</b>.",
            reply_markup=back("menu:topup", "Пополнить баланс"),
        )
        return
    await msg.answer(f"⏳ Пополнение №{order_id}…")
    await db.add_sp_paid_gb(user_id, plan, gb)
    try:
        await _sp_provision(sp, db, user_id, plan)
    except ApiError as exc:
        if not exc.uncertain:
            # A clean refusal means nothing was bought, so take the credit back.
            # An uncertain one may still have landed upstream — leave it for the
            # admin to settle rather than silently taking paid GB away.
            await db.add_sp_paid_gb(user_id, plan, -gb)
        await db.fail_order(
            order_id, {"error": str(exc), "status": exc.status}, uncertain=exc.uncertain
        )
        if exc.uncertain:
            await msg.answer(
                f"⚠️ Пополнение №{order_id}: статус неясен, передано администратору. "
                "Средства зарезервированы."
            )
            await _sp_alert_admins(
                msg,
                admin_ids,
                f"⚠️ Прокси-заказ №{order_id} ({plan} {gb:g} GB, покупатель {user_id}) "
                f"в статусе «на проверке»: {html.escape(str(exc))}\n"
                "Проверь у поставщика, не списался ли трафик, и закрой заказ вручную.",
            )
        else:
            await msg.answer(
                f"❌ Пополнение №{order_id} не выполнено: {html.escape(str(exc))}. "
                "Средства возвращены."
            )
            await _sp_alert_admins(
                msg,
                admin_ids,
                f"❌ Прокси-заказ №{order_id} ({plan} {gb:g} GB, покупатель {user_id}) "
                f"не прошёл: {html.escape(str(exc))}\n"
                "Деньги покупателю возвращены, у поставщика ничего не списалось.",
                throttle_key=f"fail:{plan}",
            )
        return
    await db.complete_order(
        order_id,
        external_id=request_id,
        response={"plan": plan, "gb": gb},
        delivery="",
        status="completed",
    )
    await msg.answer(f"✅ Добавлено {gb:g} GB.")
    await render_sp_manage(msg, db, sp, user_id, plan)


@router.message(StrikeProxyState.country_search)
async def sp_country_search_input(
    message: Message, state: FSMContext, db: Database, sp: StrikeProxyClient
) -> None:
    data = await state.get_data()
    plan = str(data.get("sp_plan") or "")
    await state.clear()
    if not _sp_valid_plan(plan):
        return
    rows = sp_filter_countries(await _sp_countries(sp, plan), message.text or "")
    if not rows:
        await message.answer("Ничего не найдено.", reply_markup=back(f"sm:{plan}:country"))
        return
    await message.answer("Найдено:", reply_markup=sp_country_results_menu(plan, rows[:20]))


@router.message(StrikeProxyState.proxy_count)
async def sp_count_input(
    message: Message, state: FSMContext, db: Database, sp: StrikeProxyClient
) -> None:
    data = await state.get_data()
    plan = str(data.get("sp_plan") or "")
    await state.clear()
    if not _sp_valid_plan(plan):
        return
    try:
        count = int((message.text or "").strip())
        if count < 1 or count > SP_COUNT_CAP:
            raise ValueError
    except ValueError:
        await message.answer(
            f"Нужно целое число от 1 до {SP_COUNT_CAP}.", reply_markup=back(f"sm:{plan}:home")
        )
        return
    await _sp_save_setting(db, message_user_id(message), plan, proxy_count=count)
    await render_sp_manage(message, db, sp, message_user_id(message), plan)


@router.message(StrikeProxyState.topup_amount)
async def sp_topup_input(
    message: Message, state: FSMContext, db: Database, sp: StrikeProxyClient, config: Config
) -> None:
    data = await state.get_data()
    plan = str(data.get("sp_plan") or "")
    await state.clear()
    if not _sp_valid_plan(plan):
        return
    try:
        gb = round(float((message.text or "").replace(",", ".").strip()), 2)
        if gb < SP_TOPUP_MIN_GB or gb > 10000:
            raise ValueError
    except ValueError:
        await message.answer(
            f"Нужно число от {SP_TOPUP_MIN_GB:g} до 10000 GB.",
            reply_markup=back(f"sm:{plan}:home"),
        )
        return
    await _sp_topup_execute(
        message, db, sp, message_user_id(message), plan, gb, config.admin_ids
    )


@router.callback_query(F.data == "menu:profile")
async def profile(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    await ensure_user(callback, db)
    user = await db.get_user(callback.from_user.id)
    if user is None:
        await safe_edit(callback, "Профиль не найден.", back())
        return
    await safe_edit(
        callback,
        "ℹ️ <b>Профиль</b>\n\n"
        f"👤 ID: <code>{callback.from_user.id}</code>\n\n"
        "💰 <b>Финансы</b>\n"
        f"Баланс: <b>{rub(int(user['balance_kopecks']))} $</b>\n\n"
        "💳 <b>Покупки</b>\n"
        f"Куплено товаров: <b>{int(user['purchased_count'])}</b>\n"
        f"Всего потрачено: <b>{rub(int(user['spent_kopecks']))} $</b>",
        profile_menu(),
    )


@router.callback_query(F.data == "profile:promo")
async def ask_promo(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(PromoState.code)
    await safe_edit(callback, "Введите промокод:", back("menu:profile", "Отмена"))


@router.message(PromoState.code)
async def redeem_promo(message: Message, state: FSMContext, db: Database) -> None:
    await state.clear()
    try:
        amount = await db.redeem_promo(message.text or "", message_user_id(message))
        await message.answer(f"✅ На баланс зачислено <b>{rub(amount)} $</b>.", reply_markup=profile_menu())
    except ValueError as exc:
        await message.answer(f"❌ {html.escape(str(exc))}", reply_markup=profile_menu())


@router.callback_query(F.data == "profile:referral")
async def referral(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    count, _ = await db.referrals(callback.from_user.id)
    percent = float(await db.get_setting("referral_percent") or "0")
    if callback.bot is None:
        return
    me = await callback.bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{callback.from_user.id}"
    await safe_edit(
        callback,
        "🤝 <b>Реферальная система</b>\n\n"
        f"Вы получаете <b>{percent:g}%</b> от пополнений приглашённых пользователей.\n"
        f"Приглашено: <b>{count}</b>\n\n"
        f"Ваша ссылка:\n<code>{html.escape(link)}</code>",
        back("menu:profile"),
    )


@router.callback_query(F.data == "menu:topup")
async def ask_topup(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.set_state(PaymentState.amount)
    await safe_edit(
        callback,
        "💳 <b>Пополнение баланса</b>\n\nВведите сумму в USDT ($), например: <code>10</code>.",
        back("menu:home", "Отмена"),
    )


@router.message(PaymentState.amount)
async def create_topup(message: Message, state: FSMContext, db: Database, heleket: HeleketClient) -> None:
    try:
        amount = Decimal((message.text or "").strip().replace(",", "."))
        if amount < Decimal("1") or amount > Decimal("100000"):
            raise ValueError
        amount = amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        await message.answer("Введите сумму от 1 до 100 000 $.")
        return
    await state.clear()
    order_id = f"topup_{uuid.uuid4().hex}"
    amount_kopecks = int(amount * 100)
    user_id = message_user_id(message)
    await db.create_payment(order_id, user_id, amount_kopecks, purpose="topup")
    public_url = (await db.get_setting("public_base_url")).rstrip("/")
    callback_url = f"{public_url}/webhooks/heleket" if public_url else ""
    try:
        invoice = await heleket.create_invoice(
            amount_rub=amount,
            order_id=order_id,
            user_id=user_id,
            callback_url=callback_url,
        )
        url = str(invoice.get("url") or "")
        if not url:
            raise ApiError("Heleket не вернул ссылку на оплату")
        await db.update_payment_invoice(
            order_id,
            str(invoice.get("payment_status") or "check"),
            str(invoice.get("uuid") or ""),
            url,
        )
        await message.answer(
            f"Счёт на <b>{amount:.2f} $</b> создан. После оплаты нажмите «Проверить оплату».",
            reply_markup=payment_menu(url, order_id),
        )
    except ApiError as exc:
        await db.update_payment_invoice(order_id, "failed")
        await message.answer(f"❌ Не удалось создать счёт: {html.escape(str(exc))}", reply_markup=back())


PAYMENT_PURPOSE_NAMES = {
    "topup": "пополнение баланса",
    "market_purchase": "оплата заказа криптовалютой",
}


async def notify_admins_payment(
    *,
    bot,
    db: Database,
    admin_ids: Iterable[int],
    payment: dict[str, Any],
) -> None:
    """Tell every admin that a user's payment has been credited."""
    admins = [int(admin_id) for admin_id in admin_ids or ()]
    if not admins:
        return
    user_id = int(payment.get("user_id") or 0)
    amount = int(payment.get("amount_kopecks") or 0)
    purpose = str(payment.get("purpose") or "topup")
    purpose_name = PAYMENT_PURPOSE_NAMES.get(purpose, purpose)

    who = f"<code>{user_id}</code>"
    balance_line = ""
    try:
        user = await db.get_user(user_id)
        if user:
            username = str(user.get("username") or "")
            full_name = str(user.get("full_name") or "")
            label = f"@{username}" if username else full_name
            if label:
                who = f"{html.escape(label)} (<code>{user_id}</code>)"
            balance_line = f"\nБаланс сейчас: <b>{rub(int(user['balance_kopecks']))} $</b>"
    except Exception:
        logger.warning("Could not load user %s for the admin payment notice", user_id)

    bonus = int(payment.get("referral_bonus") or 0)
    referrer_id = int(payment.get("referrer_id") or 0)
    bonus_line = ""
    if referrer_id and bonus:
        bonus_line = (
            f"\nРеферальный бонус: <b>{rub(bonus)} $</b> → <code>{referrer_id}</code>"
        )

    text = (
        "💰 <b>Поступила оплата</b>\n\n"
        f"Пользователь: {who}\n"
        f"Сумма: <b>{rub(amount)} $</b>\n"
        f"Назначение: {html.escape(purpose_name)}\n"
        f"Счёт: <code>{html.escape(str(payment.get('order_id') or ''))}</code>"
        f"{balance_line}{bonus_line}"
    )
    for admin_id in admins:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logger.warning("Could not send the payment notice to admin %s", admin_id)


async def process_payment_status(
    *,
    db: Database,
    sous: SousClient,
    order_id: str,
    provider_status: str,
    bot,
    notify_user: bool = True,
    admin_ids: Iterable[int] = (),
) -> dict[str, Any] | None:
    credited = await db.credit_payment(order_id, provider_status)
    if credited and not credited.get("already_credited"):
        await finalize_paid_payment(db=db, sous=sous, bot=bot, payment=credited, notify_user=notify_user)
        await notify_admins_payment(bot=bot, db=db, admin_ids=admin_ids, payment=credited)
        referrer_id = int(credited.get("referrer_id") or 0)
        bonus = int(credited.get("referral_bonus") or 0)
        if referrer_id and bonus:
            try:
                await bot.send_message(referrer_id, f"🤝 Реферальный бонус: <b>{rub(bonus)} $</b>.")
            except Exception:
                logger.warning("Could not notify referrer %s", referrer_id)
    return credited


@router.callback_query(F.data.startswith("paycheck:"))
async def check_payment(
    callback: CallbackQuery,
    db: Database,
    heleket: HeleketClient,
    sous: SousClient,
    config: Config,
) -> None:
    await callback.answer("Проверяю…")
    order_id = (callback.data or "").split(":", 1)[1]
    payment = await db.get_payment(order_id)
    if not payment or int(payment["user_id"]) != callback.from_user.id:
        await safe_edit(callback, "Счёт не найден.", back())
        return
    if payment.get("credited_at"):
        text = "✅ Этот счёт уже обработан."
        if str(payment.get("purpose") or "topup") == "topup":
            text = "✅ Этот счёт уже зачислен на баланс."
        await safe_edit(callback, text, back("menu:profile"))
        return
    try:
        info = await heleket.payment_info(order_id)
        status = str(info.get("payment_status") or info.get("status") or "check")
        if callback.bot is None:
            raise ApiError("Контекст бота недоступен")
        credited = await process_payment_status(
            db=db,
            sous=sous,
            order_id=order_id,
            provider_status=status,
            bot=callback.bot,
            notify_user=False,
            admin_ids=config.admin_ids,
        )
    except ApiError as exc:
        await safe_edit(callback, f"⚠️ {html.escape(str(exc))}", payment_menu(payment["payment_url"], order_id))
        return
    if credited:
        purpose = str(payment.get("purpose") or "topup")
        text = f"✅ Оплата подтверждена. На баланс зачислено <b>{rub(int(payment['amount_kopecks']))} $</b>."
        if purpose == "market_purchase":
            text = "✅ Оплата подтверждена. Заказ создаётся автоматически."
        await safe_edit(callback, text, back("menu:profile"))
    else:
        await safe_edit(
            callback,
            f"Платёж пока не завершён. Статус: <b>{html.escape(status)}</b>.",
            payment_menu(payment["payment_url"], order_id),
        )


STATUS_NAMES = {
    "completed": "✅ выполнен",
    "delivery_pending": "⏳ ожидает выдачи",
    "processing": "⏳ создаётся",
    "review": "⚠️ на проверке",
    "failed": "❌ отменён",
}


@router.callback_query(F.data == "menu:orders")
async def orders(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    items = await db.user_orders(callback.from_user.id)
    if not items:
        await safe_edit(callback, "📦 <b>Мои заказы</b>\n\nУ вас пока нет заказов.", back())
        return
    lines = ["📦 <b>Мои заказы</b>", ""]
    rows = []
    from aiogram.types import InlineKeyboardMarkup
    from .keyboards import button

    for item in items:
        status = STATUS_NAMES.get(item["status"], item["status"])
        ref = item["order_number"] or item["id"]
        lines.append(
            f"№{ref} · {html.escape(cut(item['title'], 45))}\n"
            f"{rub(int(item['total_kopecks']))} $ · {status}"
        )
        rows.append([button(f"Заказ №{ref}", f"order:{item['id']}")])
    rows.append([button("‹ Назад", "menu:home")])
    await safe_edit(callback, "\n\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.regexp(r"^order:\d+$"))
async def order_detail_handler(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    order_id = int((callback.data or "").split(":")[1])
    order = await db.get_order(order_id)
    if not order or int(order["user_id"]) != callback.from_user.id:
        await safe_edit(callback, "Заказ не найден.", back("menu:orders"))
        return
    status = STATUS_NAMES.get(order["status"], order["status"])
    ref = order["order_number"] or order_id
    text = (
        f"📦 <b>Заказ №{ref}</b>\n\n"
        f"{html.escape(cut(order['title'], 1000))}\n"
        f"Количество: {float(order['quantity']):g}\n"
        f"Сумма: <b>{rub(int(order['total_kopecks']))} $</b>\n"
        f"Статус: {status}"
    )
    await safe_edit(
        callback,
        text,
        order_menu(
            order_id,
            order["status"] == "delivery_pending"
            and order["kind"] in {"market", "proxy_service"}
            and bool(order["external_id"]),
        ),
    )
    if order["delivery"] and isinstance(callback.message, Message):
        await send_delivery(callback.message, order_id, order["delivery"], str(ref))


@router.callback_query(F.data.startswith("ordercheck:"))
async def order_check(callback: CallbackQuery, db: Database, sous: SousClient) -> None:
    await callback.answer("Проверяю…")
    order_id = int((callback.data or "").split(":")[1])
    order = await db.get_order(order_id)
    if not order or int(order["user_id"]) != callback.from_user.id or order["kind"] not in {"market", "proxy_service"}:
        await safe_edit(callback, "Заказ не найден.", back("menu:orders"))
        return
    try:
        if order["kind"] == "market":
            result = await sous.market_order_status(order["external_id"])
        else:
            result = await sous.proxy_service_order_status(order["external_id"])
        delivery = extract_delivery(result)
        status_raw = str(
            (result.get("order") or result).get("status") or result.get("status") or ""
        ).lower()
        number = external_order_number(result)
        if not delivery and status_raw in MARKET_REFUNDED_STATUSES:
            refunded = await settle_refunded_market_order(
                db=db,
                bot=callback.bot,
                user_id=callback.from_user.id,
                order_id=order_id,
                order=order,
                result=result,
            )
            if refunded:
                await safe_edit(
                    callback,
                    f"❌ Заказ №{order_ref(order, order_id)} не выдан поставщиком. "
                    "Деньги вернулись на баланс.",
                    back("menu:orders"),
                )
                return
        if delivery or status_raw == "completed":
            await db.complete_order(
                order_id,
                external_id=order["external_id"],
                response=result,
                delivery=delivery,
                status="completed",
                order_number=number,
            )
            ref = number or order["order_number"] or order_id
            await safe_edit(callback, f"✅ Заказ №{ref} выполнен.", order_menu(order_id))
            if isinstance(callback.message, Message):
                await send_delivery(callback.message, order_id, delivery, str(ref))
        else:
            await safe_edit(callback, "⏳ Выдача ещё формируется.", order_menu(order_id, True))
    except ApiError as exc:
        await safe_edit(callback, f"⚠️ {html.escape(str(exc))}", order_menu(order_id, True))


@router.callback_query(F.data == "menu:settings")
async def settings(callback: CallbackQuery) -> None:
    await callback.answer()
    await safe_edit(callback, "⚙️ <b>Настройки</b>\n\nЯзык: <b>Русский</b>", settings_menu())


@router.callback_query(F.data == "menu:support")
async def support(callback: CallbackQuery, db: Database) -> None:
    await callback.answer()
    username = (await db.get_setting("support_username")).lstrip("@")
    if username:
        text = f"💬 Поддержка: @{html.escape(username)}\n\nhttps://t.me/{html.escape(username)}"
    else:
        text = "Контакт поддержки пока не указан."
    await safe_edit(callback, text, back())


@router.callback_query(F.data == "menu:about")
async def about(callback: CallbackQuery) -> None:
    await callback.answer()
    await safe_edit(
        callback,
        "⚡ <b>О сервисе</b>\n\n"
        "Магазин цифровых товаров: аккаунты Instagram и YouTube, выделенные и резидентские прокси, а также пакеты прокси-сервисов.\n\n"
        "Оплата криптовалютой через Heleket. Выдача заказов происходит автоматически после списания средств с внутреннего баланса.",
        back(),
    )
