from __future__ import annotations

import asyncio
import html
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .clients import ApiError, HeleketClient, SousClient, sale_price_kopecks
from .config import Config
from .db import Database, proxy_markup_key
from .dataimpulse import DataImpulseClient, POOL_TYPES, pool_label
from .strikeproxy import (
    StrikeProxyClient,
    PLAN_TYPES as SP_PLAN_TYPES,
    cost_setting_key as sp_cost_setting_key,
    topup_cost_setting_key as sp_topup_cost_setting_key,
    plan_label as sp_plan_label,
    plan_of as sp_plan_of,
    section_code as sp_section_code,
    section_codes as sp_section_codes,
)
from .handlers_user import (
    _di_configured,
    _sp_configured,
    proxy_section_enabled,
    relevant_categories,
    rub,
    safe_edit,
)
from .keyboards import (
    admin_category_list,
    admin_keys_menu,
    admin_menu,
    admin_pricing_menu,
    admin_product_card,
    admin_product_list,
    admin_products_platform,
    admin_proxy_markup_menu,
    admin_section_title,
    admin_service_card,
    admin_service_list,
    back,
    section_label,
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


SETTING_META: dict[str, tuple[str, bool, str]] = {
    "sous_api_key": ("SOUS API key", True, "секретный API-ключ поставщика"),
    "heleket_merchant_id": ("Heleket merchant ID", True, "UUID мерчанта Heleket"),
    "heleket_api_key": ("Heleket API key", True, "платёжный API-ключ Heleket"),
    "public_base_url": ("Публичный URL", False, "HTTPS URL сервера, например https://shop.example.com"),
    "support_username": ("Контакт поддержки", False, "username без @"),
    "usdt_rub_rate": ("Множитель цены (USDT)", False, "1 = цена в чистом USDT, например 1.00"),
    "market_markup_percent": ("Наценка Instagram/YouTube", False, "процент от 0 до 1000"),
    "proxy_markup_percent": ("Наценка Proxy", False, "процент от 0 до 1000"),
    "referral_percent": ("Реферальный процент", False, "процент от 0 до 100"),
    "di_login": ("DataImpulse login", True, "e-mail из раздела API Management"),
    "di_password": ("DataImpulse password", True, "пароль из раздела API Management"),
    "sp_api_key": ("StrikeProxy API key", True, "токен вида sp_live_… из Dashboard → Settings"),
    "market_min_price_usd": (
        "Мин. цена товара, $",
        False,
        "товары дешевле этой цены скрыты из каталога; 0 — фильтр выключен. Прокси не затрагивает",
    ),
}

# Buying price per GB for each DataImpulse pool. There is no price endpoint, so
# the shop reads the rate out of the provider's top-up log and pins it here; the
# value stays editable for the rare case the log is empty.
for _pool in POOL_TYPES:
    SETTING_META[f"di_cost_{_pool}"] = (
        f"Себестоимость GB · {pool_label(_pool)}",
        False,
        "цена поставщика за 1 GB в долларах; обновляется сама из истории пополнений",
    )

# StrikeProxy publishes the account's own reseller price list, so these are
# refreshed from the provider; they stay editable only as an override.
for _plan in SP_PLAN_TYPES:
    SETTING_META[sp_cost_setting_key(_plan)] = (
        f"Себестоимость GB · {sp_plan_label(_plan)} · новый заказ",
        False,
        "цена поставщика за 1 GB при создании нового прокси-аккаунта; "
        "обновляется сама из каталога реселлера и по факту списания",
    )
    # A top-up of an existing service is billed at its own, higher rate — the
    # customer price is built on whichever of the two is dearer.
    SETTING_META[sp_topup_cost_setting_key(_plan)] = (
        f"Себестоимость GB · {sp_plan_label(_plan)} · докупка",
        False,
        "цена поставщика за 1 GB при добавлении трафика в существующий аккаунт; "
        "обновляется сама по факту списания",
    )

# Per-section proxy markup: repricing one pool must not touch the rest of the
# shop, so every section gets its own optional percent.
PROXY_SECTION_CODES: tuple[str, ...] = tuple(POOL_TYPES) + sp_section_codes()
for _code in PROXY_SECTION_CODES:
    SETTING_META[proxy_markup_key(_code)] = (
        f"Наценка · {section_label(_code)}",
        False,
        "процент от 0 до 1000; пусто — берётся общая наценка Proxy",
    )


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
        di_login = await db.get_setting("di_login", secret=True) or "не задан"
        di_password = mask_secret(await db.get_setting("di_password", secret=True))
        sp_key = mask_secret(await db.get_setting("sp_api_key", secret=True))
        await safe_edit(
            callback,
            "🔑 <b>Ключи API и оплаты</b>\n\n"
            f"SOUS: <code>{html.escape(sous)}</code>\n"
            f"DataImpulse login: <code>{html.escape(di_login)}</code>\n"
            f"DataImpulse password: <code>{html.escape(di_password)}</code>\n"
            f"StrikeProxy key: <code>{html.escape(sp_key)}</code>\n"
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
        min_price = await db.get_setting("market_min_price_usd") or "0"
        try:
            min_price_active = Decimal(min_price.replace(",", ".")) > 0
        except InvalidOperation:
            min_price_active = False
        min_price_line = (
            f"Мин. цена товара: <b>{html.escape(min_price)} $</b>"
            if min_price_active
            else "Мин. цена товара: <b>фильтр выключен</b>"
        )
        pool_lines = []
        for pool in POOL_TYPES:
            cost = await db.get_setting(f"di_cost_{pool}")
            shown = (
                f"<b>{html.escape(str(cost))} $ / GB</b>"
                if cost
                else "<b>не получена</b> — пул не продаётся"
            )
            pool_lines.append(f"{pool_label(pool)}: {shown}")
        sp_lines = []
        for plan in SP_PLAN_TYPES:
            order_cost = await db.get_setting(sp_cost_setting_key(plan))
            topup_cost = await db.get_setting(sp_topup_cost_setting_key(plan))
            if not order_cost and not topup_cost:
                sp_lines.append(
                    f"{sp_plan_label(plan)}: <b>не получена</b> — пул не продаётся"
                )
                continue
            parts = [f"заказ {html.escape(order_cost)} $" if order_cost else "заказ —"]
            parts.append(
                f"докупка <b>{html.escape(topup_cost)} $</b>" if topup_cost else "докупка —"
            )
            sp_lines.append(f"{sp_plan_label(plan)}: " + " · ".join(parts) + " / GB")
        await safe_edit(
            callback,
            "⚙️ <b>Курсы и наценка</b>\n\n"
            f"Множитель цены: <b>{html.escape(rate)}</b>\n"
            f"Instagram/YouTube: <b>{html.escape(market)}%</b>\n"
            f"Proxy: <b>{html.escape(proxy)}%</b>\n"
            f"Реферальный бонус: <b>{html.escape(referral)}%</b>\n"
            f"{min_price_line}\n\n"
            "<b>Закупка DataImpulse</b> — берётся из истории пополнений\n"
            "поставщика, кнопка «🧪 Проверить DataImpulse» обновляет.\n"
            "Цена покупателю = закупка + наценка Proxy.\n"
            + "\n".join(pool_lines),
            admin_pricing_menu(),
        )

    async def _active_proxy_sections(db: Database) -> tuple[str, ...]:
        """Only pools whose provider is actually configured, so the admin lists
        mirror what customers can see."""
        codes: list[str] = []
        if await _di_configured(db):
            codes.extend(POOL_TYPES)
        if await _sp_configured(db):
            codes.extend(sp_section_codes())
        return tuple(codes) or PROXY_SECTION_CODES

    def _as_cost(raw: str) -> Decimal | None:
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            value = Decimal(raw.replace(",", "."))
        except InvalidOperation:
            return None
        return value if value > 0 else None

    async def _proxy_section_cost(db: Database, code: str) -> Decimal | None:
        """Buying price per GB the shop must cover for a section.

        For StrikeProxy that is the dearer of the two real rates (new order vs
        top-up), because a repeat customer always pays the top-up one.
        """
        plan = sp_plan_of(code)
        if not plan:
            return _as_cost(await db.get_setting(f"di_cost_{code}"))
        values = [
            value
            for value in (
                _as_cost(await db.get_setting(sp_cost_setting_key(plan))),
                _as_cost(await db.get_setting(sp_topup_cost_setting_key(plan))),
            )
            if value is not None
        ]
        return max(values) if values else None

    @router.callback_query(F.data == "admin:proxymarkup")
    async def proxy_markup_screen(callback: CallbackQuery, db: Database) -> None:
        await callback.answer()
        common = await db.get_setting("proxy_markup_percent") or "0"
        codes = await _active_proxy_sections(db)
        overrides = await db.proxy_markup_overrides(codes)
        titles = await db.service_overrides()
        lines = [
            "💹 <b>Наценка Proxy</b>",
            "",
            f"Общая наценка: <b>{html.escape(common)}%</b>",
            "",
            "Наценка на пул перекрывает общую — цену прокси можно менять",
            "отдельно, не трогая товары и курс.",
            "",
        ]
        for code in codes:
            cost = await _proxy_section_cost(db, code)
            markup = await db.proxy_markup(code)
            own = overrides.get(code)
            title = admin_section_title(code, titles)
            source = f"{own}% (своя)" if own else f"{common}% (общая)"
            if cost is None:
                lines.append(f"{title}: закупка не получена · наценка {source}")
                continue
            price = sale_price_kopecks(cost, 1.0, markup) / 100
            lines.append(
                f"{title}: {cost:g} $ → <b>{price:.2f} $ / GB</b> · наценка {source}"
            )
        await safe_edit(
            callback,
            "\n".join(lines),
            admin_proxy_markup_menu(codes, overrides, titles),
        )

    @router.callback_query(F.data.startswith("apmreset:"))
    async def proxy_markup_reset(callback: CallbackQuery, db: Database) -> None:
        await callback.answer("Наценка сброшена на общую")
        code = (callback.data or "").split(":", 1)[1]
        if code in PROXY_SECTION_CODES:
            await db.clear_setting(proxy_markup_key(code))
        await proxy_markup_screen(callback, db)

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
            elif key.startswith("di_cost_") or key.startswith("sp_cost_"):
                number = Decimal(value.replace(",", "."))
                if number <= 0 or number > Decimal("1000"):
                    raise ValueError("Допустимо от 0.01 до 1000")
                value = format(number.normalize(), "f")
            elif key.startswith("proxy_markup_") and key != "proxy_markup_percent":
                number = Decimal(value.replace(",", "."))
                if number < 0 or number > Decimal("1000"):
                    raise ValueError("Допустимо от 0 до 1000")
                value = format(number.normalize(), "f")
            elif key in {
                "usdt_rub_rate",
                "market_markup_percent",
                "proxy_markup_percent",
                "referral_percent",
                "market_min_price_usd",
            }:
                number = Decimal(value.replace(",", "."))
                if key == "referral_percent":
                    maximum = Decimal("100")
                elif key == "market_min_price_usd":
                    maximum = Decimal("100000")
                else:
                    maximum = Decimal("1000")
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

    @router.callback_query(F.data == "admintest:di")
    async def test_dataimpulse(callback: CallbackQuery, db: Database, di: DataImpulseClient) -> None:
        await callback.answer("Проверяю…")
        try:
            data = await di.balance()
            subusers = await di.subuser_list(limit=1000)
            # Read the real buying price per pool out of the top-up log and pin
            # it, so the shop marks up what the provider actually charges.
            rates: dict[str, float] = {}
            for row in subusers:
                pool = str(row.get("pool_type") or "")
                if pool in POOL_TYPES and pool not in rates:
                    rate = await di.observed_rate(int(row.get("id") or 0))
                    if 0 < rate < 100:
                        rates[pool] = rate
                        await db.set_setting(f"di_cost_{pool}", f"{rate:g}")
            if rates:
                price_lines = "\n".join(
                    f"{pool_label(p)}: закупка <b>{r:g} $/GB</b>" for p, r in rates.items()
                )
            else:
                price_lines = "Закупочных цен ещё нет — появятся после первого пополнения трафика."
            await safe_edit(
                callback,
                "✅ DataImpulse работает.\n"
                f"Баланс реселлера: <b>{float(data.get('balance', 0)):g}</b>\n"
                f"Суб-аккаунтов: <b>{len(subusers)}</b>\n\n"
                f"{price_lines}",
                admin_keys_menu(),
            )
        except ApiError as exc:
            await safe_edit(callback, f"❌ DataImpulse: {html.escape(str(exc))}", admin_keys_menu())

    @router.callback_query(F.data == "admintest:sp")
    async def test_strikeproxy(callback: CallbackQuery, db: Database, sp: StrikeProxyClient) -> None:
        await callback.answer("Проверяю…")
        try:
            profile = await sp.reseller_me()
            balance = await sp.balance()
            costs = await sp.plan_costs()
            # The catalog is the provider's own price list for this account, so
            # pin it: the shop always marks up a real buying price.
            for plan, price in costs.items():
                await db.set_setting(sp_cost_setting_key(plan), f"{price:g}")
            services = await sp.services()
        except ApiError as exc:
            await safe_edit(callback, f"❌ StrikeProxy: {html.escape(str(exc))}", admin_keys_menu())
            return
        if costs:
            price_lines = "\n".join(
                f"{sp_plan_label(plan)}: закупка <b>{price:g} $/GB</b>"
                for plan, price in costs.items()
            )
        else:
            price_lines = "Каталог не вернул цены — продажа прокси заблокирована."
        status = "реселлер" if profile.get("is_reseller") else str(profile.get("role") or "—")
        await safe_edit(
            callback,
            "✅ StrikeProxy работает.\n"
            f"Аккаунт: <b>{html.escape(str(profile.get('username') or '—'))}</b> ({html.escape(status)})\n"
            f"Баланс реселлера: <b>{balance:g} $</b>\n"
            f"Активных сервисов: <b>{len(services)}</b>\n\n"
            f"{price_lines}",
            admin_keys_menu(),
        )

    _STATUS_LABELS = {
        "completed": "выполнено",
        "delivery_pending": "ждёт выдачи",
        "processing": "в обработке",
        "review": "на проверке",
        "failed": "ошибка",
        "pending": "ожидает оплаты",
        "paid": "оплачен",
    }
    _KIND_LABELS = {
        "market": "Instagram/YouTube",
        "instagram": "Instagram",
        "youtube": "YouTube",
        "proxy": "Прокси",
        "proxy_service": "Прокси-сервисы",
        "proxy_dedicated": "Выделенные прокси",
    }

    @router.callback_query(F.data == "admin:stats")
    async def stats(callback: CallbackQuery, db: Database) -> None:
        await callback.answer()
        d = await db.stats()
        u, o, a, t, p = d["users"], d["orders"], d["attention"], d["topups"], d["promo"]

        lines = [
            "📊 <b>CRM-статистика</b>",
            "",
            "<b>👥 Пользователи</b>",
            f"Всего: <b>{u['total']}</b> · платящих: <b>{u['paying']}</b> "
            f"(конверсия <b>{u['conversion']:g}%</b>)",
            f"Новые: <b>{u['new_1d']}</b> за сутки · <b>{u['new_7d']}</b> за 7 дней · "
            f"<b>{u['new_30d']}</b> за 30 дней",
            f"С балансом: <b>{u['funded']}</b> · по рефералам: <b>{u['referred']}</b>",
            f"Баланс на счетах: <b>{rub(u['balance_kopecks'])} $</b> · "
            f"всего потрачено: <b>{rub(u['spent_kopecks'])} $</b>",
            "",
            "<b>📦 Заказы (оплаченные)</b>",
            f"Всего: <b>{o['paid']}</b> · средний чек: <b>{rub(o['avg_kopecks'])} $</b>",
            f"Оборот: <b>{rub(o['revenue_kopecks'])} $</b>",
            f"За сутки: <b>{o['count_1d']}</b> / <b>{rub(o['revenue_1d_kopecks'])} $</b>",
            f"За 7 дней: <b>{o['count_7d']}</b> / <b>{rub(o['revenue_7d_kopecks'])} $</b>",
            f"За 30 дней: <b>{o['count_30d']}</b> / <b>{rub(o['revenue_30d_kopecks'])} $</b>",
        ]

        if o["by_status"]:
            parts = [
                f"{_STATUS_LABELS.get(status, html.escape(status))} — {count}"
                for status, count in o["by_status"]
            ]
            lines += ["", "<b>Статусы заказов</b>", " · ".join(parts)]

        if o["by_kind"]:
            lines += ["", "<b>По направлениям</b>"]
            lines += [
                f"{_KIND_LABELS.get(kind, html.escape(kind or '—'))}: "
                f"<b>{count}</b> / {rub(revenue)} $"
                for kind, count, revenue in o["by_kind"]
            ]

        if o["top_products"]:
            lines += ["", "<b>🏆 Топ товаров по выручке</b>"]
            lines += [
                f"{idx}. {html.escape(title[:40])} — <b>{count}</b> шт / {rub(revenue)} $"
                for idx, (title, count, revenue) in enumerate(o["top_products"], 1)
            ]

        lines += [
            "",
            "<b>💳 Пополнения баланса</b>",
            f"Всего: <b>{t['count']}</b> на <b>{rub(t['total_kopecks'])} $</b>",
            f"Приход: <b>{rub(t['sum_1d_kopecks'])} $</b> за сутки · "
            f"<b>{rub(t['sum_7d_kopecks'])} $</b> за 7 дней · "
            f"<b>{rub(t['sum_30d_kopecks'])} $</b> за 30 дней",
            "",
            "<b>🎟 Промокоды</b>",
            f"Активных: <b>{p['active']}</b> из <b>{p['total']}</b> · "
            f"остаток активаций: <b>{p['uses_left']}</b>",
            f"Активировано: <b>{p['redeemed']}</b> на <b>{rub(p['credited_kopecks'])} $</b>",
            "",
            "<b>⚠️ Требуют внимания</b>",
            f"Заказы в работе: <b>{a['pending']}</b> (на проверке: <b>{a['review']}</b>)",
            f"Ошибки за 7 дней: <b>{a['failed_7d']}</b> · "
            f"незакрытые пополнения: <b>{a['topups_open']}</b>",
        ]

        text = "\n".join(lines)
        if len(text) > 3900:
            # Trim on a line boundary so no HTML tag is split.
            text = text[: text.rfind("\n", 0, 3900)].rstrip() + "\n…"
        await safe_edit(callback, text, back("admin:home"))

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
    async def admin_products(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
        await callback.answer()
        await state.clear()
        await safe_edit(
            callback,
            "🛒 <b>Товары в категориях</b>\n\n"
            + (
                "👁 Раздел «Прокси» виден покупателям."
                if await proxy_section_enabled(db)
                else "🙈 Раздел «Прокси» скрыт от покупателей."
            )
            + "\n\nВыберите платформу:",
            admin_products_platform(await proxy_section_enabled(db)),
        )

    @router.callback_query(F.data.startswith("apsecproxy:"))
    async def admin_toggle_proxy_section(callback: CallbackQuery, db: Database) -> None:
        enable = (callback.data or "").endswith(":1")
        await db.set_setting("proxy_section_enabled", "1" if enable else "0")
        await callback.answer("Раздел показан" if enable else "Раздел скрыт")
        state_line = (
            "👁 Раздел «Прокси» виден покупателям."
            if enable
            else "🙈 Раздел «Прокси» скрыт — кнопки нет в меню, старые кнопки не открываются."
        )
        await safe_edit(
            callback,
            f"🛒 <b>Товары в категориях</b>\n\n{state_line}\n\nВыберите платформу:",
            admin_products_platform(enable),
        )

    @router.callback_query(F.data.startswith("apcat:"))
    async def admin_product_categories(callback: CallbackQuery, sous: SousClient, db: Database) -> None:
        await callback.answer()
        platform = (callback.data or "").split(":", 1)[1]

        if platform == "proxy":
            overrides = await db.service_overrides()
            await safe_edit(
                callback,
                "🌐 <b>Прокси</b>\n\n"
                "🙈 — скрыт от покупателей, ✏️ — задано своё имя.\n"
                "Выберите пул для редактирования:",
                admin_service_list(overrides, await _active_proxy_sections(db)),
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
