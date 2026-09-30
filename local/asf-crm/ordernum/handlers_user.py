from __future__ import annotations

import asyncio
import html
import json
import logging
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
    tariff_total,
)
from .config import Config
from .db import Database, InsufficientFunds
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
    proxy_countries,
    proxy_group_title,
    proxy_group_variants,
    proxy_menu,
    proxy_protocols,
    proxy_service_back,
    PROXY_VARIANT_LABELS,
    service_display_label,
    service_tariffs,
    settings_menu,
)
from .states import PaymentState, PromoState, PurchaseState
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


async def show_home(message: Message, config: Config, db: Database) -> None:
    await ensure_user(message, db)
    name = html.escape(message.from_user.full_name if message.from_user else "покупатель")
    await message.answer(
        f"Добро пожаловать, <b>{name}</b>!\n\nВыберите раздел в меню ниже.",
        reply_markup=main_menu(bool(message.from_user and message.from_user.id in config.admin_ids)),
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
        main_menu(callback.from_user.id in config.admin_ids),
    )


@router.callback_query(F.data == "menu:catalog")
async def catalog(callback: CallbackQuery, state: FSMContext, config: Config) -> None:
    await callback.answer()
    await state.clear()
    await safe_edit(
        callback,
        "Выберите раздел:",
        main_menu(callback.from_user.id in config.admin_ids),
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
        await safe_edit(callback, f"В разделе {title} пока нет категорий.", back("menu:home"))
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
    data = {**data, "items": resolved_items}
    rate, markup = await db.pricing("market")
    prices = {
        int(item["id"]): sale_price_kopecks(item.get("price_usdt", 0), rate, markup)
        for item in data.get("items") or []
    }
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
        await safe_edit(callback, "В этой категории сейчас нет товаров.", back("menu:home"))
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
async def cancel_state(message: Message, state: FSMContext, config: Config) -> None:
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=main_menu(message_user_id(message) in config.admin_ids))


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


async def wait_for_market_delivery(
    *,
    db: Database,
    bot,
    user_id: int,
    order_id: int,
    external_id: str,
    delivery_check: Callable[[str], Awaitable[dict[str, Any]]],
) -> None:
    """Poll a newly created market order briefly, without making the buyer retry manually."""
    for _ in range(60):
        await asyncio.sleep(5)
        order = await db.get_order(order_id)
        if not order or order["status"] != "delivery_pending":
            return
        try:
            result = await delivery_check(external_id)
            delivery = extract_delivery(result)
            if not delivery:
                continue
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
            return
        except Exception:
            logger.exception("Unable to check delivery for market order %s", order_id)
            return


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


@router.callback_query(F.data == "cat:proxy")
async def proxy_root(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
    await callback.answer()
    await show_proxy_hub(callback, sous, db)


@router.callback_query(F.data == "px:dedicated")
async def dedicated_proxy(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
    await callback.answer()
    try:
        data = await sous.proxy_catalog()
        countries = [item for item in data.get("countries") or [] if item.get("available")]
        rate, markup = await db.pricing("proxy")
        prices = {
            int(item["id"]): sale_price_kopecks(item.get("sale_unit_price", 0), rate, markup)
            for item in countries
        }
    except ApiError as exc:
        await safe_edit(callback, f"⚠️ {html.escape(str(exc))}", back("cat:proxy"))
        return
    await safe_edit(
        callback,
        "💎 <b>Выделенные прокси</b>\n\nВыберите страну. Цена указана за один прокси:",
        proxy_countries(countries, prices),
    )


async def find_proxy_country(sous: SousClient, country_id: int) -> dict[str, Any] | None:
    data = await sous.proxy_catalog()
    return next((x for x in data.get("countries") or [] if int(x.get("id", 0)) == country_id), None)


@router.callback_query(F.data.startswith("pxc:"))
async def dedicated_country(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
    await callback.answer()
    try:
        country_id = int((callback.data or "").split(":")[1])
        country = await find_proxy_country(sous, country_id)
        if not country:
            raise ValueError("Страна не найдена")
        rate, markup = await db.pricing("proxy")
        price = sale_price_kopecks(country.get("sale_unit_price", 0), rate, markup)
    except (ValueError, ApiError) as exc:
        await safe_edit(callback, f"⚠️ {html.escape(str(exc))}", back("px:dedicated"))
        return
    await safe_edit(
        callback,
        f"🌍 <b>{html.escape(str(country.get('country_name', 'Прокси')))}</b>\n"
        f"Цена: <b>{rub(price)} $ / шт.</b>\n\nВыберите протокол:",
        proxy_protocols(country_id, country.get("protocols") or []),
    )


@router.callback_query(F.data.startswith("pxp:"))
async def ask_proxy_quantity(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    try:
        _, country_raw, protocol_raw = (callback.data or "").split(":")
        await state.set_state(PurchaseState.proxy_quantity)
        await state.update_data(country_id=int(country_raw), protocol_id=int(protocol_raw))
    except ValueError:
        await safe_edit(callback, "Некорректная позиция.", back("px:dedicated"))
        return
    await safe_edit(callback, "Введите количество прокси от 1 до 100.", back("px:dedicated", "Отмена"))


@router.message(PurchaseState.proxy_quantity)
async def buy_dedicated_proxy(message: Message, state: FSMContext, sous: SousClient, db: Database) -> None:
    try:
        quantity = int((message.text or "").strip())
        if quantity < 1 or quantity > 100:
            raise ValueError
    except ValueError:
        await message.answer("Введите целое количество от 1 до 100.")
        return
    selected = await state.get_data()
    await state.clear()
    try:
        country = await find_proxy_country(sous, selected["country_id"])
        if not country:
            raise ValueError("Страна больше не доступна")
        protocol = next(
            (x for x in country.get("protocols") or [] if int(x.get("id", 0)) == selected["protocol_id"]),
            None,
        )
        if not protocol or int(protocol.get("stock", 0)) < quantity:
            raise ValueError("Недостаточно прокси в наличии")
        rate, markup = await db.pricing("proxy")
        unit_price = sale_price_kopecks(country.get("sale_unit_price", 0), rate, markup)
    except (ApiError, ValueError) as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}", reply_markup=back("cat:proxy"))
        return
    request_id = f"proxy_{message_user_id(message)}_{message.message_id}_{uuid.uuid4().hex[:8]}"
    await run_purchase(
        message=message,
        db=db,
        request_id=request_id,
        kind="proxy_dedicated",
        title=f"{country.get('country_name')} {protocol.get('name')}",
        quantity=quantity,
        unit_price=unit_price,
        total=unit_price * quantity,
        request_payload={**selected, "quantity": quantity},
        api_call=lambda: sous.proxy_order(selected["country_id"], selected["protocol_id"], quantity),
    )


async def get_proxy_services(sous: SousClient) -> list[dict[str, Any]]:
    data = await sous.proxy_services()
    return list(data.get("items") or [])


async def show_proxy_hub(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
    try:
        services = await get_proxy_services(sous)
    except ApiError as exc:
        await safe_edit(callback, html.escape(str(exc)), back("menu:home"))
        return
    rate, markup = await db.pricing("proxy")
    remaining_text = "н/д"
    try:
        traffic = await sous.residential_traffic()
        pool = traffic.get("pool") or {}
        remaining = float(pool.get("allocatable_gb", pool.get("available_gb", 0)) or 0)
        remaining_text = f"{remaining:.4f}".rstrip("0").rstrip(".")
    except (ApiError, TypeError, ValueError):
        pass
    overrides = await db.service_overrides()
    await safe_edit(
        callback,
        "⭐ <b>Прокси</b>\n\n"
        "Количество трафика:\n"
        f"💻 Резидентские: <b>{remaining_text} GB</b>",
        proxy_menu(services, rate, markup, overrides),
    )


@router.callback_query(F.data == "px:services")
async def proxy_services(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
    await callback.answer()
    await show_proxy_hub(callback, sous, db)


@router.callback_query(F.data.startswith("pxg:"))
async def proxy_group(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
    await callback.answer()
    group = (callback.data or "").split(":", 1)[1]
    try:
        services = await get_proxy_services(sous)
    except ApiError as exc:
        await safe_edit(callback, f"⚠️ {html.escape(str(exc))}", back("cat:proxy"))
        return
    overrides = await db.service_overrides()
    await safe_edit(
        callback,
        f"⭐ <b>{html.escape(proxy_group_title(group))}</b>\n\nВыберите нужный тип прокси:",
        proxy_group_variants(group, services, overrides),
    )


@router.callback_query(F.data.startswith("pxs:"))
async def proxy_service_detail(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
    await callback.answer()
    code = (callback.data or "").split(":", 1)[1]
    try:
        if (await db.get_service_override(code))["hidden"]:
            raise ValueError("Этот тип прокси сейчас недоступен")
        service = next((s for s in await get_proxy_services(sous) if s.get("service") == code), None)
        if not service:
            raise ValueError("Сервис не найден")
        rate, markup = await db.pricing("proxy")
        minimum = float(service.get("min_quantity", 1))
        start_total = tariff_total(service, minimum)
        start_price = sale_price_kopecks(start_total, rate, markup)
    except (ApiError, ValueError) as exc:
        await safe_edit(callback, f"⚠️ {html.escape(str(exc))}", back("cat:proxy"))
        return
    overrides = await db.service_overrides()
    title = service_display_label(code, overrides)
    if code == "residential" and not (overrides.get(code) or {}).get("custom_title"):
        title = "Резидентские прокси"
    extra = "\nНастройки выдачи: UA, HTTP, rotating, 10 прокси." if code == "residential" else ""
    await safe_edit(
        callback,
        f"<b>{html.escape(title)}</b>\n"
        f"Единица: {html.escape(str(service.get('unit', '')))}\n"
        f"Цена от: <b>{rub(start_price)} $</b>{extra}\n\nВыберите пакет:",
        service_tariffs(service, rate, markup, proxy_service_back(code)),
    )


@router.callback_query(F.data.startswith("pxsb:"))
async def buy_proxy_service(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
    await callback.answer()
    try:
        _, code, quantity_raw = (callback.data or "").split(":")
        quantity = float(quantity_raw)
        if (await db.get_service_override(code))["hidden"]:
            raise ValueError("Этот тип прокси сейчас недоступен")
        service = next((s for s in await get_proxy_services(sous) if s.get("service") == code), None)
        if not service:
            raise ValueError("Сервис не найден")
        upstream_total = tariff_total(service, quantity)
        if upstream_total <= 0:
            raise ValueError("Не удалось определить цену")
        rate, markup = await db.pricing("proxy")
        total = sale_price_kopecks(upstream_total, rate, markup)
    except (ValueError, ApiError) as exc:
        await safe_edit(callback, f"⚠️ {html.escape(str(exc))}", back("cat:proxy"))
        return
    request_id = f"svc_{callback.from_user.id}_{uuid.uuid4().hex}"
    settings = None
    if code == "residential":
        settings = {
            "proxy_count": 10,
            "rotation": "rotating",
            "session_ttl": 0,
            "country": "UA",
            "protocol": "http",
            "format": "hostname:port:login:password",
        }
    if not isinstance(callback.message, Message):
        return
    await run_purchase(
        message=callback.message,
        db=db,
        request_id=request_id,
        kind="proxy_service",
        title=f"{code} · {quantity:g} {service.get('unit', '')}",
        quantity=quantity,
        unit_price=total,
        total=total,
        request_payload={"service": code, "quantity": quantity, "settings": settings or {}},
        api_call=lambda: sous.proxy_service_order(code, quantity, request_id, settings),
        actor_id=callback.from_user.id,
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
        status_raw = str(result.get("status") or "").lower()
        number = external_order_number(result)
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
