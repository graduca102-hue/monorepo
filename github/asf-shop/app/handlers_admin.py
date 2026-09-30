from __future__ import annotations

import asyncio
import html
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .clients import ApiError, HeleketClient, SousClient
from .config import Config
from .db import Database
from .handlers_user import get_proxy_services, relevant_categories, rub, safe_edit
from .keyboards import (
    admin_category_list,
    admin_keys_menu,
    admin_menu,
    admin_pricing_menu,
    admin_product_card,
    admin_product_list,
    admin_products_platform,
    admin_service_card,
    admin_service_list,
    back,
    cancel_admin,
    service_display_label,
)
from .security import mask_secret
from .states import AdminState
from .ui import is_auto_delivery_product, product_display_title


async def _admin_fetch_products(
    sous: SousClient, category_id: int, page: int
) -> tuple[list[dict], int]:
    data = await sous.market_products(category_id, page)
    items = [item for item in data.get("items") or [] if not is_auto_delivery_product(item)]
    return items, int(data.get("last_page", 1))


async def _render_admin_product(
    callback: CallbackQuery,
    sous: SousClient,
    db: Database,
    category_id: int,
    page: int,
    product_id: int,
) -> None:
    try:
        items, _ = await _admin_fetch_products(sous, category_id, page)
        product = next((item for item in items if int(item.get("id", 0)) == product_id), None)
        if not product:
            raise ValueError("Товар не найден на этой странице")
    except (ValueError, ApiError) as exc:
        await safe_edit(callback, html.escape(str(exc)), back("admin:products"))
        return
    override = await db.get_product_override(product_id)
    api_title = product_display_title(product, category_id)
    lines = [
        "🛒 <b>Редактирование товара</b>",
        "",
        f"ID: <code>{product_id}</code>",
        f"Название из API: {html.escape(api_title)}",
    ]
    if override["custom_title"]:
        lines.append(f"Своё название: <b>{html.escape(override['custom_title'])}</b>")
    lines.append(f"Статус: {'🙈 скрыт от покупателей' if override['hidden'] else '👁 виден'}")
    await safe_edit(
        callback,
        "\n".join(lines),
        admin_product_card(
            category_id,
            page,
            product_id,
            hidden=override["hidden"],
            has_custom=bool(override["custom_title"]),
        ),
    )


SETTING_META = {
    "sous_api_key": ("SOUS API key", True, "секретный API-ключ поставщика"),
    "heleket_merchant_id": ("Heleket merchant ID", True, "UUID мерчанта Heleket"),
    "heleket_api_key": ("Heleket API key", True, "платёжный API-ключ Heleket"),
    "public_base_url": ("Публичный URL", False, "HTTPS URL сервера, например https://shop.example.com"),
    "support_username": ("Контакт поддержки", False, "username без @"),
    "usdt_rub_rate": ("Множитель цены (USDT)", False, "1 = цена в чистом USDT, например 1.00"),
    "market_markup_percent": ("Наценка Instagram/YouTube", False, "процент от 0 до 1000"),
    "proxy_markup_percent": ("Наценка Proxy", False, "процент от 0 до 1000"),
    "referral_percent": ("Реферальный процент", False, "процент от 0 до 100"),
}


def setup_admin_router(admin_ids: frozenset[int]) -> Router:
    router = Router(name="admin")
    router.message.filter(F.from_user.id.in_(admin_ids))
    router.callback_query.filter(F.from_user.id.in_(admin_ids))

    @router.message(Command("admin"))
    async def admin_command(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("🛠 <b>Админка</b>\nВыберите действие:", reply_markup=admin_menu())

    @router.callback_query(F.data == "admin:home")
    async def admin_home(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.clear()
        await safe_edit(callback, "🛠 <b>Админка</b>\nВыберите действие:", admin_menu())

    @router.callback_query(F.data == "admin:keys")
    async def admin_keys(callback: CallbackQuery, db: Database) -> None:
        await callback.answer()
        sous = mask_secret(await db.get_setting("sous_api_key", secret=True))
        merchant = mask_secret(await db.get_setting("heleket_merchant_id", secret=True))
        heleket_key = mask_secret(await db.get_setting("heleket_api_key", secret=True))
        public_url = await db.get_setting("public_base_url") or "не задан"
        support = await db.get_setting("support_username") or "не задан"
        await safe_edit(
            callback,
            "🔑 <b>Ключи API и оплаты</b>\n\n"
            f"SOUS: <code>{html.escape(sous)}</code>\n"
            f"Heleket merchant: <code>{html.escape(merchant)}</code>\n"
            f"Heleket key: <code>{html.escape(heleket_key)}</code>\n"
            f"Public URL: <code>{html.escape(public_url)}</code>\n"
            f"Поддержка: <code>{html.escape(support)}</code>\n\n"
            "Секреты хранятся в базе в зашифрованном виде и полностью не отображаются.",
            admin_keys_menu(),
        )

    @router.callback_query(F.data == "admin:pricing")
    async def pricing(callback: CallbackQuery, db: Database) -> None:
        await callback.answer()
        rate = await db.get_setting("usdt_rub_rate")
        market = await db.get_setting("market_markup_percent")
        proxy = await db.get_setting("proxy_markup_percent")
        referral = await db.get_setting("referral_percent")
        await safe_edit(
            callback,
            "⚙️ <b>Курсы и наценка</b>\n\n"
            f"Множитель цены: <b>{html.escape(rate)}</b>\n"
            f"Instagram/YouTube: <b>{html.escape(market)}%</b>\n"
            f"Proxy: <b>{html.escape(proxy)}%</b>\n"
            f"Реферальный бонус: <b>{html.escape(referral)}%</b>",
            admin_pricing_menu(),
        )

    @router.callback_query(F.data.startswith("adminset:"))
    async def ask_setting(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        key = (callback.data or "").split(":", 1)[1]
        meta = SETTING_META.get(key)
        if not meta:
            await safe_edit(callback, "Неизвестная настройка.", back("admin:home"))
            return
        label, secret, hint = meta
        await state.set_state(AdminState.setting_value)
        await state.update_data(setting_key=key, setting_secret=secret)
        warning = "\n\nСообщение будет сразу удалено из чата." if secret else ""
        await safe_edit(
            callback,
            f"Введите <b>{html.escape(label)}</b>.\n{html.escape(hint)}.{warning}",
            cancel_admin(),
        )

    @router.message(AdminState.setting_value)
    async def save_setting(message: Message, state: FSMContext, db: Database) -> None:
        data = await state.get_data()
        key = str(data.get("setting_key", ""))
        secret = bool(data.get("setting_secret"))
        value = (message.text or "").strip()
        meta = SETTING_META.get(key)
        if not meta or not value:
            await message.answer("Значение не может быть пустым.")
            return
        try:
            if key == "public_base_url":
                if not value.startswith("https://"):
                    raise ValueError("Нужен URL, начинающийся с https://")
                value = value.rstrip("/")
            elif key == "support_username":
                value = value.lstrip("@").replace("https://t.me/", "")
                if not value or "/" in value:
                    raise ValueError("Введите Telegram username")
            elif key in {"usdt_rub_rate", "market_markup_percent", "proxy_markup_percent", "referral_percent"}:
                number = Decimal(value.replace(",", "."))
                maximum = Decimal("100") if key == "referral_percent" else Decimal("1000")
                if number <= 0 and key == "usdt_rub_rate":
                    raise ValueError("Курс должен быть больше нуля")
                if key != "usdt_rub_rate" and (number < 0 or number > maximum):
                    raise ValueError(f"Допустимо от 0 до {maximum:g}")
                value = format(number.normalize(), "f")
        except (InvalidOperation, ValueError) as exc:
            await message.answer(f"❌ {html.escape(str(exc) or 'Некорректное значение')}")
            return
        if secret:
            try:
                await message.delete()
            except TelegramBadRequest:
                pass
        await db.set_setting(key, value, secret=secret)
        await state.clear()
        await message.answer(f"✅ {html.escape(meta[0])} сохранён.", reply_markup=admin_menu())

    @router.callback_query(F.data == "admintest:sous")
    async def test_sous(callback: CallbackQuery, sous: SousClient) -> None:
        await callback.answer("Проверяю…")
        try:
            profile = await sous.profile()
            await safe_edit(
                callback,
                "✅ SOUS API работает.\n"
                f"Баланс поставщика: <b>{float(profile.get('balance', 0)):g} {html.escape(str(profile.get('currency', 'USDT')))}</b>",
                admin_keys_menu(),
            )
        except ApiError as exc:
            await safe_edit(callback, f"❌ SOUS: {html.escape(str(exc))}", admin_keys_menu())

    @router.callback_query(F.data == "admintest:heleket")
    async def test_heleket(callback: CallbackQuery, heleket: HeleketClient) -> None:
        await callback.answer("Проверяю…")
        try:
            data = await heleket.services()
            items = data.get("items") or data.get("services") or []
            await safe_edit(
                callback,
                f"✅ Heleket работает. Доступно методов: <b>{len(items)}</b>.",
                admin_keys_menu(),
            )
        except ApiError as exc:
            await safe_edit(callback, f"❌ Heleket: {html.escape(str(exc))}", admin_keys_menu())

    @router.callback_query(F.data == "admin:users")
    async def users(callback: CallbackQuery, db: Database) -> None:
        await callback.answer()
        users_list = await db.recent_users()
        if not users_list:
            text = "👥 Пользователей пока нет."
        else:
            lines = ["👥 <b>Последние пользователи</b>", ""]
            for user in users_list:
                username = f"@{user['username']}" if user["username"] else user["full_name"]
                lines.append(
                    f"<code>{user['id']}</code> · {html.escape(str(username))} · {rub(int(user['balance_kopecks']))} $"
                )
            text = "\n".join(lines)
        await safe_edit(callback, text, back("admin:home"))

    @router.callback_query(F.data == "admin:stats")
    async def stats(callback: CallbackQuery, db: Database) -> None:
        await callback.answer()
        data = await db.stats()
        await safe_edit(
            callback,
            "📦 <b>Статистика магазина</b>\n\n"
            f"Пользователей: <b>{data['users']}</b>\n"
            f"Выполнено заказов: <b>{data['orders']}</b>\n"
            f"Оборот: <b>{rub(data['revenue_kopecks'])} $</b>\n"
            f"Пополнений: <b>{rub(data['topups_kopecks'])} $</b>\n"
            f"Требуют внимания: <b>{data['pending']}</b>",
            back("admin:home"),
        )

    @router.callback_query(F.data == "admin:balance")
    async def ask_balance(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(AdminState.add_balance)
        await safe_edit(
            callback,
            "Введите ID пользователя и сумму в USDT ($) через пробел.\n"
            "Пример: <code>7657064561 500</code>\n"
            "Для списания укажите отрицательную сумму.",
            cancel_admin(),
        )

    @router.message(AdminState.add_balance)
    async def add_balance(message: Message, state: FSMContext, db: Database) -> None:
        try:
            user_raw, amount_raw = (message.text or "").split(maxsplit=1)
            user_id = int(user_raw)
            amount = Decimal(amount_raw.replace(",", ".")).quantize(Decimal("0.01"))
            if amount == 0 or abs(amount) > Decimal("10000000"):
                raise ValueError
            kopecks = int(amount * 100)
            admin_id = message.from_user.id if message.from_user else 0
            balance = await db.adjust_balance(user_id, kopecks, "admin_adjustment", str(admin_id))
        except Exception as exc:
            await message.answer(f"❌ Не удалось изменить баланс: {html.escape(str(exc) or 'проверьте формат')}.")
            return
        await state.clear()
        await message.answer(
            f"✅ Баланс пользователя <code>{user_id}</code>: <b>{rub(balance)} $</b>.",
            reply_markup=admin_menu(),
        )
        try:
            if message.bot is None:
                return
            await message.bot.send_message(
                user_id,
                f"💳 Администратор изменил ваш баланс на <b>{amount:+.2f} $</b>.\n"
                f"Текущий баланс: <b>{rub(balance)} $</b>.",
            )
        except Exception:
            pass

    @router.callback_query(F.data == "admin:promo")
    async def ask_promo(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(AdminState.create_promo)
        await safe_edit(
            callback,
            "Введите код, сумму в USDT ($) и число активаций.\n"
            "Пример: <code>WELCOME 100 20</code>\n\n"
            "Повторный код обновит существующий промокод.",
            cancel_admin(),
        )

    @router.message(AdminState.create_promo)
    async def create_promo(message: Message, state: FSMContext, db: Database) -> None:
        try:
            code, amount_raw, uses_raw = (message.text or "").split()
            if not code.replace("_", "").replace("-", "").isalnum() or len(code) > 32:
                raise ValueError("код должен содержать буквы, цифры, _ или -")
            amount = Decimal(amount_raw.replace(",", ".")).quantize(Decimal("0.01"))
            uses = int(uses_raw)
            if amount <= 0 or uses <= 0:
                raise ValueError("сумма и число активаций должны быть положительными")
            await db.create_promo(code, int(amount * 100), uses)
        except Exception as exc:
            await message.answer(f"❌ {html.escape(str(exc) or 'Проверьте формат')}.")
            return
        await state.clear()
        await message.answer(
            f"✅ Промокод <code>{html.escape(code.upper())}</code>: {amount:.2f} $, активаций {uses}.",
            reply_markup=admin_menu(),
        )

    @router.callback_query(F.data == "admin:broadcast")
    async def ask_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.set_state(AdminState.broadcast)
        await safe_edit(
            callback,
            "Отправьте сообщение для рассылки. Текст, фото и кнопки будут скопированы пользователям.",
            cancel_admin(),
        )

    @router.message(AdminState.broadcast)
    async def broadcast(message: Message, state: FSMContext, db: Database) -> None:
        await state.clear()
        ids = await db.all_user_ids()
        progress = await message.answer(f"📣 Рассылка началась. Получателей: {len(ids)}.")
        sent = 0
        failed = 0
        for user_id in ids:
            try:
                await message.copy_to(user_id)
                sent += 1
            except (TelegramForbiddenError, TelegramBadRequest):
                failed += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.035)
        await progress.edit_text(
            f"✅ Рассылка завершена.\nДоставлено: <b>{sent}</b>\nОшибок: <b>{failed}</b>",
            reply_markup=admin_menu(),
        )

    @router.callback_query(F.data == "admin:products")
    async def admin_products(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        await state.clear()
        await safe_edit(
            callback,
            "🛒 <b>Товары в категориях</b>\n\nВыберите платформу:",
            admin_products_platform(),
        )

    @router.callback_query(F.data.startswith("apcat:"))
    async def admin_product_categories(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
        await callback.answer()
        platform = (callback.data or "").split(":", 1)[1]

        if platform == "proxy":
            try:
                services = await get_proxy_services(sous)
            except ApiError as exc:
                await safe_edit(callback, html.escape(str(exc)), back("admin:products"))
                return
            if not services:
                await safe_edit(callback, "Прокси-сервисы сейчас недоступны.", back("admin:products"))
                return
            overrides = await db.service_overrides()
            await safe_edit(
                callback,
                "🌐 <b>Прокси</b>\n\n"
                "🙈 — скрыт от покупателей, ✏️ — задано своё имя.\n"
                "Выберите позицию для редактирования:",
                admin_service_list(services, overrides),
            )
            return

        try:
            items = await relevant_categories(sous, platform)
        except ApiError as exc:
            await safe_edit(callback, html.escape(str(exc)), back("admin:products"))
            return
        title = "Instagram" if platform == "instagram" else "YouTube"
        if not items:
            await safe_edit(callback, f"В разделе {title} нет категорий.", back("admin:products"))
            return
        await safe_edit(
            callback,
            f"<b>{title}</b>\n\nВыберите категорию:",
            admin_category_list(items, platform),
        )

    @router.callback_query(F.data.startswith("apc:"))
    async def admin_category_products(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
        await callback.answer()
        try:
            _, category_raw, page_raw = (callback.data or "").split(":")
            category_id, page = int(category_raw), max(1, int(page_raw))
            items, last_page = await _admin_fetch_products(sous, category_id, page)
        except (ValueError, ApiError) as exc:
            await safe_edit(callback, f"Не удалось загрузить товары: {html.escape(str(exc))}", back("admin:products"))
            return
        if not items:
            await safe_edit(callback, "В этой категории сейчас нет товаров.", back("admin:products"))
            return
        overrides = await db.get_product_overrides([int(item["id"]) for item in items])
        await safe_edit(
            callback,
            f"🛒 <b>Товары</b> · страница {page} из {last_page}\n\n"
            "🙈 — скрыт от покупателей, ✏️ — задано своё имя.\n"
            "Выберите товар для редактирования:",
            admin_product_list(items, category_id, page, last_page, overrides),
        )

    @router.callback_query(F.data.startswith("aprod:"))
    async def admin_product_open(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
        await callback.answer()
        try:
            _, category_raw, page_raw, product_raw = (callback.data or "").split(":")
        except ValueError:
            await safe_edit(callback, "Некорректный товар.", back("admin:products"))
            return
        await _render_admin_product(
            callback, sous, db, int(category_raw), int(page_raw), int(product_raw)
        )

    @router.callback_query(F.data.startswith("aphide:"))
    async def admin_product_hide(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
        await callback.answer("Товар скрыт")
        _, category_raw, page_raw, product_raw = (callback.data or "").split(":")
        await db.set_product_hidden(int(product_raw), True)
        await _render_admin_product(
            callback, sous, db, int(category_raw), int(page_raw), int(product_raw)
        )

    @router.callback_query(F.data.startswith("apshow:"))
    async def admin_product_show(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
        await callback.answer("Товар снова виден")
        _, category_raw, page_raw, product_raw = (callback.data or "").split(":")
        await db.set_product_hidden(int(product_raw), False)
        await _render_admin_product(
            callback, sous, db, int(category_raw), int(page_raw), int(product_raw)
        )

    @router.callback_query(F.data.startswith("apreset:"))
    async def admin_product_reset_name(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
        await callback.answer("Имя сброшено на название из API")
        _, category_raw, page_raw, product_raw = (callback.data or "").split(":")
        await db.set_product_title(int(product_raw), "")
        await _render_admin_product(
            callback, sous, db, int(category_raw), int(page_raw), int(product_raw)
        )

    @router.callback_query(F.data.startswith("aprename:"))
    async def admin_product_rename(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        try:
            _, category_raw, page_raw, product_raw = (callback.data or "").split(":")
        except ValueError:
            await safe_edit(callback, "Некорректный товар.", back("admin:products"))
            return
        await state.set_state(AdminState.product_rename)
        await state.update_data(
            rename_category=int(category_raw),
            rename_page=int(page_raw),
            rename_product=int(product_raw),
        )
        await safe_edit(
            callback,
            "Введите новое название товара (от 1 до 120 символов).\n"
            "Оно заменит название из API для покупателей.",
            cancel_admin(),
        )

    async def _render_service(callback: CallbackQuery, db: Database, code: str) -> None:
        override = await db.get_service_override(code)
        overrides = await db.service_overrides()
        lines = [
            "🌐 <b>Редактирование прокси</b>",
            "",
            f"Код сервиса: <code>{html.escape(code)}</code>",
            f"Название сейчас: {html.escape(service_display_label(code, overrides))}",
        ]
        if override["custom_title"]:
            lines.append(f"Своё название: <b>{html.escape(override['custom_title'])}</b>")
        lines.append(f"Статус: {'🙈 скрыт от покупателей' if override['hidden'] else '👁 виден'}")
        await safe_edit(
            callback,
            "\n".join(lines),
            admin_service_card(
                code,
                hidden=override["hidden"],
                has_custom=bool(override["custom_title"]),
            ),
        )

    @router.callback_query(F.data.startswith("apsvc:"))
    async def admin_service_open(callback: CallbackQuery, db: Database) -> None:
        await callback.answer()
        code = (callback.data or "").split(":", 1)[1]
        await _render_service(callback, db, code)

    @router.callback_query(F.data.startswith("apsvchide:"))
    async def admin_service_hide(callback: CallbackQuery, db: Database) -> None:
        await callback.answer("Прокси скрыты")
        code = (callback.data or "").split(":", 1)[1]
        await db.set_service_hidden(code, True)
        await _render_service(callback, db, code)

    @router.callback_query(F.data.startswith("apsvcshow:"))
    async def admin_service_show(callback: CallbackQuery, db: Database) -> None:
        await callback.answer("Прокси снова видны")
        code = (callback.data or "").split(":", 1)[1]
        await db.set_service_hidden(code, False)
        await _render_service(callback, db, code)

    @router.callback_query(F.data.startswith("apsvcreset:"))
    async def admin_service_reset(callback: CallbackQuery, db: Database) -> None:
        await callback.answer("Имя сброшено")
        code = (callback.data or "").split(":", 1)[1]
        await db.set_service_title(code, "")
        await _render_service(callback, db, code)

    @router.callback_query(F.data.startswith("apsvcrename:"))
    async def admin_service_rename(callback: CallbackQuery, state: FSMContext) -> None:
        await callback.answer()
        code = (callback.data or "").split(":", 1)[1]
        await state.set_state(AdminState.service_rename)
        await state.update_data(rename_service=code)
        await safe_edit(
            callback,
            "Введите новое название для этого типа прокси (1–120 символов).\n"
            "Оно заменит стандартное название для покупателей.",
            cancel_admin(),
        )

    @router.message(AdminState.service_rename)
    async def admin_service_rename_save(message: Message, state: FSMContext, db: Database) -> None:
        title = (message.text or "").strip()
        if not title or len(title) > 120:
            await message.answer("Название должно быть от 1 до 120 символов.")
            return
        data = await state.get_data()
        code = str(data.get("rename_service") or "")
        if not code:
            await state.clear()
            await message.answer("Не удалось определить сервис.", reply_markup=admin_menu())
            return
        await db.set_service_title(code, title)
        await state.clear()
        await message.answer(
            f"✅ Название прокси <code>{html.escape(code)}</code> обновлено:\n<b>{html.escape(title)}</b>",
            reply_markup=admin_menu(),
        )

    @router.message(AdminState.product_rename)
    async def admin_product_rename_save(message: Message, state: FSMContext, db: Database) -> None:
        title = (message.text or "").strip()
        if not title or len(title) > 120:
            await message.answer("Название должно быть от 1 до 120 символов.")
            return
        data = await state.get_data()
        product_id = int(data.get("rename_product", 0))
        if not product_id:
            await state.clear()
            await message.answer("Не удалось определить товар.", reply_markup=admin_menu())
            return
        await db.set_product_title(product_id, title)
        await state.clear()
        await message.answer(
            f"✅ Название товара <code>{product_id}</code> обновлено:\n<b>{html.escape(title)}</b>",
            reply_markup=admin_menu(),
        )

    return router
