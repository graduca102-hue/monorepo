from __future__ import annotations

from typing import Any, Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


from .dataimpulse import POOL_TYPES, PROXY_FORMATS, format_label, pool_label
from .strikeproxy import (
    PLAN_TYPES as SP_PLAN_TYPES,
    plan_label as sp_plan_label,
    plan_of as sp_plan_of,
    section_code as sp_section_code,
    section_codes as sp_section_codes,
)
from .ui import product_display_title, YOUTUBE_CATEGORY_IDS


def button(text: str, data: str, *, style: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data, style=style)


def main_menu(is_admin: bool = False, *, show_proxy: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [button("📸 Instagram", "cat:instagram"), button("▶️ YouTube", "cat:youtube")],
    ]
    if show_proxy:
        rows.append([button("🌐 Proxy", "cat:proxy")])
    rows += [
        [button("👤 Профиль", "menu:profile")],
        [button("💳 Пополнить баланс", "menu:topup")],
        [button("📦 Мои заказы", "menu:orders"), button("⚙️ Настройки", "menu:settings")],
        [button("💬 Поддержка", "menu:support")],
    ]
    if is_admin:
        rows.append([button("🛠 Админка", "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back(data: str = "menu:home", text: str = "‹ Назад") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button(text, data)]])


def market_categories(items: Iterable[dict[str, Any]], platform: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        name = str(item.get("name", "Категория")).lstrip("►").strip()
        builder.row(button(name[:55], f"mc:{int(item['id'])}:1"))
    builder.row(button("Назад", "menu:home"))
    return builder.as_markup()


def product_list(
    products: Iterable[dict[str, Any]], category_id: int, page: int, last_page: int, prices: dict[int, int]
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for product in products:
        product_id = int(product["id"])
        price = prices[product_id] / 100
        title = product_display_title(product, category_id)
        label = f"{price:.2f} $ · {title}"
        builder.row(button(label[:60], f"prod:{category_id}:{page}:{product_id}"))
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(button("←", f"mc:{category_id}:{page - 1}"))
    nav.append(button(f"{page}/{max(last_page, 1)}", "noop"))
    if page < last_page:
        nav.append(button("→", f"mc:{category_id}:{page + 1}"))
    builder.row(*nav)
    platform_callback = "cat:youtube" if category_id in YOUTUBE_CATEGORY_IDS else "cat:instagram"
    builder.row(button("К категориям", platform_callback))
    return builder.as_markup()


def product_detail(category_id: int, page: int, product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("🛒 Купить", f"buyprod:{category_id}:{page}:{product_id}")],
            [button("Назад", f"mc:{category_id}:{page}")],
        ]
    )


def market_quantity_menu(category_id: int, page: int, product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button("1", f"mq:{category_id}:{page}:{product_id}:1"), button("5", f"mq:{category_id}:{page}:{product_id}:5")],
        [button("10", f"mq:{category_id}:{page}:{product_id}:10"), button("50", f"mq:{category_id}:{page}:{product_id}:50")],
        [button("Своё количество", f"mqcustom:{category_id}:{page}:{product_id}")],
        [button("‹ Назад", f"prod:{category_id}:{page}:{product_id}")],
    ])


def market_payment_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [button("💰 Оплатить балансом", "mp:balance")],
        [button("₿ Оплатить криптовалютой", "mp:crypto")],
        [button("‹ Назад", "menu:catalog")],
    ])


def section_label(code: str) -> str:
    """Built-in name of a proxy section, whichever provider serves it."""
    plan = sp_plan_of(code)
    return sp_plan_label(plan) if plan else pool_label(code)


def section_callback(code: str) -> str:
    """Where a proxy section button leads — each provider has its own router."""
    plan = sp_plan_of(code)
    return f"spp:{plan}" if plan else f"dip:{code}"


def service_display_label(code: str, overrides: dict[str, Any] | None = None) -> str:
    """Admin-defined name wins over the built-in pool label."""
    custom = str(((overrides or {}).get(code) or {}).get("custom_title") or "").strip()
    return custom or section_label(code)


def admin_section_title(code: str, overrides: dict[str, Any] | None = None) -> str:
    """Admin-side label. Both providers use the same pool names, so tag which
    one a section belongs to — buyers never see this."""
    tag = "SP" if sp_plan_of(code) else "DI"
    return f"{service_display_label(code, overrides)} · {tag}"


def is_service_hidden(code: str, overrides: dict[str, Any] | None = None) -> bool:
    return bool(((overrides or {}).get(code) or {}).get("hidden"))


def proxy_menu(
    prices_kopecks: dict[str, int],
    overrides: dict[str, Any] | None = None,
) -> InlineKeyboardMarkup:
    """Proxy hub: one row per offered pool, priced per GB.

    The caller decides which sections exist — a provider with no credentials
    contributes nothing, so the menu never shows a pool that cannot be bought.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for code, price_kopecks in prices_kopecks.items():
        if is_service_hidden(code, overrides):
            continue
        price = price_kopecks / 100
        label = service_display_label(code, overrides)
        # No price yet means the provider rate has not been observed, so the
        # pool is listed but not offered at a made-up number.
        suffix = f" · {price:.2f} $ / GB" if price > 0 else " · цена уточняется"
        rows.append([button(f"{label}{suffix}", section_callback(code))])
    if not rows:
        rows.append([button("Прокси временно недоступны", "noop")])
    rows.append([button("Назад", "menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)



def profile_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("💳 Пополнить баланс", "menu:topup")],
            [button("🎟 Активировать промокод", "profile:promo")],
            [button("🤝 Реферальная система", "profile:referral")],
            [button("‹ Назад", "menu:home")],
        ]
    )


def payment_menu(url: str, order_id: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="💳 Перейти к оплате", url=url)]]
    rows.append([button("🔄 Проверить оплату", f"paycheck:{order_id}")])
    rows.append([button("‹ В меню", "menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def order_menu(order_id: int, pending_external: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if pending_external:
        rows.append([button("🔄 Проверить выдачу", f"ordercheck:{order_id}")])
    rows.append([button("‹ К заказам", "menu:orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def settings_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("🌐 Язык: Русский", "noop")],
            [button("‹ Назад", "menu:home")],
        ]
    )


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("📣 Рассылка", "admin:broadcast")],
            [button("🎟 Промокоды", "admin:promo")],
            [button("🔑 Ключи API и оплаты", "admin:keys")],
            [button("🛒 Товары в категориях", "admin:products")],
            [button("💳 Добавить баланс", "admin:balance")],
            [button("📊 CRM-статистика", "admin:stats")],
            [button("⚙️ Курсы и наценка", "admin:pricing")],
            [button("← Назад", "menu:home")],
        ]
    )


def admin_keys_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("SOUS API key", "adminset:sous_api_key")],
            [button("Heleket merchant ID", "adminset:heleket_merchant_id")],
            [button("Heleket API key", "adminset:heleket_api_key")],
            [button("Публичный URL", "adminset:public_base_url")],
            [button("Контакт поддержки", "adminset:support_username")],
            [button("DataImpulse login", "adminset:di_login")],
            [button("DataImpulse password", "adminset:di_password")],
            [button("StrikeProxy API key", "adminset:sp_api_key")],
            [button("🧪 Проверить SOUS", "admintest:sous")],
            [button("🧪 Проверить Heleket", "admintest:heleket")],
            [button("🧪 Проверить DataImpulse", "admintest:di")],
            [button("🧪 Проверить StrikeProxy", "admintest:sp")],
            [button("‹ Назад", "admin:home")],
        ]
    )


def admin_pricing_menu() -> InlineKeyboardMarkup:
    rows = [
        [button("Множитель цены (USDT)", "adminset:usdt_rub_rate")],
        [button("Наценка Instagram/YouTube", "adminset:market_markup_percent")],
        [button("💹 Наценка Proxy", "admin:proxymarkup")],
        [button("Реферальный процент", "adminset:referral_percent")],
        [button("Мин. цена товара ($)", "adminset:market_min_price_usd")],
    ]
    # Closing price per GB for every DataImpulse pool: the provider has no price
    # endpoint, so the reseller rate is entered by hand and marked up as usual.
    for pool in POOL_TYPES:
        rows.append([button(f"Себестоимость GB · {pool_label(pool)}", f"adminset:di_cost_{pool}")])
    rows.append([button("‹ Назад", "admin:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_proxy_markup_menu(
    codes: Iterable[str],
    overrides: dict[str, str],
    titles: dict[str, Any] | None = None,
) -> InlineKeyboardMarkup:
    """Proxy markup on its own screen: one common percent plus a per-pool
    override, so proxy pricing can be changed without touching anything else."""
    rows: list[list[InlineKeyboardButton]] = [
        [button("💹 Общая наценка Proxy (%)", "adminset:proxy_markup_percent")]
    ]
    for code in codes:
        label = admin_section_title(code, titles)
        own = overrides.get(code)
        mark = f" · {own}%" if own else " · общая"
        rows.append([button(f"{label}{mark}"[:60], f"adminset:proxy_markup_{code}")])
        if own:
            rows.append([button(f"♻️ Сбросить наценку · {label}"[:60], f"apmreset:{code}")])
    rows.append([button("‹ Назад", "admin:pricing")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button("Отмена", "admin:home")]])


def admin_products_platform(proxy_enabled: bool = True) -> InlineKeyboardMarkup:
    toggle = (
        button("🙈 Скрыть раздел Прокси", "apsecproxy:0")
        if proxy_enabled
        else button("👁 Показать раздел Прокси", "apsecproxy:1")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("📸 Instagram", "apcat:instagram"), button("▶️ YouTube", "apcat:youtube")],
            [button("🌐 Proxy", "apcat:proxy")],
            [toggle],
            [button("‹ Назад", "admin:home")],
        ]
    )


def admin_service_list(
    overrides: dict[str, Any], codes: Iterable[str] | None = None
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for code in codes if codes is not None else POOL_TYPES:
        override = overrides.get(code) or {}
        title = admin_section_title(code, overrides)
        mark = "🙈 " if override.get("hidden") else ("✏️ " if override.get("custom_title") else "")
        builder.row(button(f"{mark}{title}"[:60], f"apsvc:{code}"))
    builder.row(button("‹ Назад", "admin:products"))
    return builder.as_markup()


def admin_service_card(code: str, *, hidden: bool, has_custom: bool) -> InlineKeyboardMarkup:
    rows = [[button("✏️ Изменить имя", f"apsvcrename:{code}")]]
    if hidden:
        rows.append([button("👁 Показать прокси", f"apsvcshow:{code}")])
    else:
        rows.append([button("🙈 Скрыть прокси", f"apsvchide:{code}")])
    if has_custom:
        rows.append([button("♻️ Сбросить имя", f"apsvcreset:{code}")])
    rows.append([button("‹ К списку прокси", "apcat:proxy")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_category_list(items: Iterable[dict[str, Any]], platform: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for item in items:
        name = str(item.get("name", "Категория")).lstrip("►").strip()
        builder.row(button(name[:55], f"apc:{int(item['id'])}:1"))
    builder.row(button("‹ Назад", "admin:products"))
    return builder.as_markup()


def admin_product_list(
    products: Iterable[dict[str, Any]],
    category_id: int,
    page: int,
    last_page: int,
    overrides: dict[int, dict[str, Any]],
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for product in products:
        product_id = int(product["id"])
        override = overrides.get(product_id) or {}
        title = override.get("custom_title") or product_display_title(product, category_id)
        mark = "🙈 " if override.get("hidden") else ("✏️ " if override.get("custom_title") else "")
        builder.row(button(f"{mark}{title}"[:60], f"aprod:{category_id}:{page}:{product_id}"))
    nav: list[InlineKeyboardButton] = []
    if page > 1:
        nav.append(button("←", f"apc:{category_id}:{page - 1}"))
    nav.append(button(f"{page}/{max(last_page, 1)}", "noop"))
    if page < last_page:
        nav.append(button("→", f"apc:{category_id}:{page + 1}"))
    builder.row(*nav)
    builder.row(button("‹ Категории", "admin:products"))
    return builder.as_markup()


def admin_product_card(
    category_id: int, page: int, product_id: int, *, hidden: bool, has_custom: bool
) -> InlineKeyboardMarkup:
    rows = [[button("✏️ Изменить имя", f"aprename:{category_id}:{page}:{product_id}")]]
    if hidden:
        rows.append([button("👁 Показать товар", f"apshow:{category_id}:{page}:{product_id}")])
    else:
        rows.append([button("🙈 Скрыть товар", f"aphide:{category_id}:{page}:{product_id}")])
    if has_custom:
        rows.append([button("♻️ Сбросить имя (из API)", f"apreset:{category_id}:{page}:{product_id}")])
    rows.append([button("‹ К списку товаров", f"apc:{category_id}:{page}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# --- DataImpulse proxy management ------------------------------------------

DI_SESSION_TTLS: tuple[tuple[int, str], ...] = (
    (300, "5 минут"),
    (900, "15 минут"),
    (1800, "30 минут"),
    (3600, "1 час"),
    (7200, "2 часа"),
)

DI_COUNTS: tuple[int, ...] = (1, 3, 5, 10, 25, 50, 100)
DI_TOPUP_GB: tuple[float, ...] = (1, 2, 5, 10, 20)
DI_TOPUP_MIN_GB = 0.05
DI_COUNTRIES_PER_PAGE = 8


def di_country_label(code: str) -> str:
    return f"🏳 {code.upper()}" if code else "🌍 Любая"


def di_manage_menu(
    pool: str, settings: dict[str, Any], *, price_per_gb: float = 0.0
) -> InlineKeyboardMarkup:
    topup_label = "➕ Пополнить трафик"
    if price_per_gb > 0:
        topup_label += f" · {price_per_gb:.2f} $/GB"
    else:
        topup_label = "⏳ Цена уточняется"
    rotation = "🔁 Ротация" if settings.get("rotation") != "sticky" else "📌 Фикс. IP"
    rows: list[list[InlineKeyboardButton]] = [
        [button(topup_label, f"dm:{pool}:topup")],
        [button("📥 Получить прокси", f"dm:{pool}:get", style="success")],
        [
            button(di_country_label(str(settings.get("country") or "")), f"dm:{pool}:country"),
            button(rotation, f"dm:{pool}:rotation"),
        ],
        [
            button("⏱ Сессия", f"dm:{pool}:session"),
            button(f"🔢 {int(settings.get('proxy_count') or 1)} шт", f"dm:{pool}:count"),
        ],
        [button(f"🧩 Формат: {format_label(str(settings.get('format') or 'hpu'))}", f"dm:{pool}:format")],
        [
            button("♻️ Сбросить настройки", f"dm:{pool}:reset"),
            button("🔑 Пароль", f"dm:{pool}:password"),
        ],
        [button("‹ Назад", "cat:proxy")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def di_country_menu(
    pool: str, countries: Iterable[dict[str, Any]], current: str, page: int = 0
) -> InlineKeyboardMarkup:
    rows = list(countries)
    start = page * DI_COUNTRIES_PER_PAGE
    builder = InlineKeyboardBuilder()
    builder.button(
        text=("✅ " if not current else "") + "🌍 Любая",
        callback_data=f"dm:{pool}:country_set:any",
    )
    for row in rows[start : start + DI_COUNTRIES_PER_PAGE]:
        code = str(row.get("code") or "").upper()
        name = str(row.get("name") or code)
        mark = "✅ " if code == (current or "").upper() else ""
        builder.button(text=f"{mark}{name}"[:60], callback_data=f"dm:{pool}:country_set:{code or 'any'}")
    builder.adjust(2)
    nav: list[InlineKeyboardButton] = []
    if page:
        nav.append(button("‹", f"dm:{pool}:country_page:{page - 1}"))
    if start + DI_COUNTRIES_PER_PAGE < len(rows):
        nav.append(button("›", f"dm:{pool}:country_page:{page + 1}"))
    if nav:
        builder.row(*nav)
    builder.row(button("🔎 Поиск страны", f"dm:{pool}:country_search"))
    builder.row(button("‹ Назад", f"dm:{pool}:home"))
    return builder.as_markup()


def di_country_results_menu(pool: str, rows: Iterable[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        code = str(row.get("code") or "").upper()
        name = str(row.get("name") or code)
        builder.row(button(name[:60], f"dm:{pool}:country_set:{code or 'any'}"))
    builder.row(button("🔎 Искать снова", f"dm:{pool}:country_search"))
    builder.row(button("‹ Назад", f"dm:{pool}:country"))
    return builder.as_markup()


def di_rotation_menu(pool: str, current: str) -> InlineKeyboardMarkup:
    rows = [
        [
            button(
                ("✅ " if current != "sticky" else "") + "🔁 Ротация (частая смена IP)",
                f"dm:{pool}:rotation_set:rotating",
            )
        ],
        [
            button(
                ("✅ " if current == "sticky" else "") + "📌 Фиксированный IP (сессия)",
                f"dm:{pool}:rotation_set:sticky",
            )
        ],
        [button("‹ Назад", f"dm:{pool}:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def di_session_menu(pool: str, current: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value, label in DI_SESSION_TTLS:
        mark = "✅ " if int(current) == value else ""
        builder.button(text=f"{mark}{label}", callback_data=f"dm:{pool}:session_set:{value}")
    builder.adjust(2)
    builder.row(button("‹ Назад", f"dm:{pool}:home"))
    return builder.as_markup()


def di_format_menu(pool: str, current: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key in PROXY_FORMATS:
        mark = "✅ " if key == current else ""
        builder.button(text=f"{mark}{format_label(key)}", callback_data=f"dm:{pool}:format_set:{key}")
    builder.adjust(1)
    builder.row(button("‹ Назад", f"dm:{pool}:home"))
    return builder.as_markup()


def di_count_menu(pool: str, current: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value in DI_COUNTS:
        mark = "✅ " if int(current) == value else ""
        builder.button(text=f"{mark}{value}", callback_data=f"dm:{pool}:count_set:{value}")
    builder.adjust(4)
    builder.row(button("✏️ Своё число", f"dm:{pool}:count_custom"))
    builder.row(button("‹ Назад", f"dm:{pool}:home"))
    return builder.as_markup()


def di_topup_menu(pool: str, prices_kopecks: dict[float, int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for gb in DI_TOPUP_GB:
        price = prices_kopecks.get(gb, 0) / 100
        builder.button(text=f"{gb:g} GB · {price:.2f} $", callback_data=f"dm:{pool}:topup_buy:{gb:g}")
    builder.adjust(2)
    builder.row(button("✏️ Своя сумма", f"dm:{pool}:topup_custom"))
    builder.row(button("‹ Назад", f"dm:{pool}:home"))
    return builder.as_markup()


def di_password_confirm(pool: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("⚠️ Да, сменить пароль", f"dm:{pool}:password_go")],
            [button("‹ Отмена", f"dm:{pool}:home")],
        ]
    )


# --- StrikeProxy proxy management -------------------------------------------
# Same menu shape as the DataImpulse one — the customer should not be able to
# tell which upstream serves a pool — but on its own ``sm:`` callback prefix.

SP_SESSION_TTLS: tuple[tuple[int, str], ...] = (
    (300, "5 минут"),
    (900, "15 минут"),
    (1800, "30 минут"),
    (3600, "1 час"),
    (7200, "2 часа"),
)

SP_COUNTS: tuple[int, ...] = (1, 3, 5, 10, 25, 50, 100)
SP_TOPUP_GB: tuple[float, ...] = (1, 2, 5, 10, 20)
SP_TOPUP_MIN_GB = 0.05
SP_COUNTRIES_PER_PAGE = 8


def sp_country_label(code: str) -> str:
    return f"🏳 {code.upper()}" if code else "🌍 Любая"


def sp_manage_menu(
    plan: str, settings: dict[str, Any], *, price_per_gb: float = 0.0
) -> InlineKeyboardMarkup:
    topup_label = "➕ Пополнить трафик"
    if price_per_gb > 0:
        topup_label += f" · {price_per_gb:.2f} $/GB"
    else:
        topup_label = "⏳ Цена уточняется"
    rotation = "🔁 Ротация" if settings.get("rotation") != "sticky" else "📌 Фикс. IP"
    rows: list[list[InlineKeyboardButton]] = [
        [button(topup_label, f"sm:{plan}:topup")],
        [button("📥 Получить прокси", f"sm:{plan}:get", style="success")],
        [
            button(sp_country_label(str(settings.get("country") or "")), f"sm:{plan}:country"),
            button(rotation, f"sm:{plan}:rotation"),
        ],
        [
            button("⏱ Сессия", f"sm:{plan}:session"),
            button(f"🔢 {int(settings.get('proxy_count') or 1)} шт", f"sm:{plan}:count"),
        ],
        [
            button(
                f"🧩 Формат: {format_label(str(settings.get('format') or 'hpu'))}",
                f"sm:{plan}:format",
            )
        ],
        [
            button("♻️ Сбросить настройки", f"sm:{plan}:reset"),
            button("🔑 Пароль", f"sm:{plan}:password"),
        ],
        [button("‹ Назад", "cat:proxy")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sp_country_menu(
    plan: str, countries: Iterable[dict[str, Any]], current: str, page: int = 0
) -> InlineKeyboardMarkup:
    rows = list(countries)
    start = page * SP_COUNTRIES_PER_PAGE
    builder = InlineKeyboardBuilder()
    builder.button(
        text=("✅ " if not current else "") + "🌍 Любая",
        callback_data=f"sm:{plan}:country_set:any",
    )
    for row in rows[start : start + SP_COUNTRIES_PER_PAGE]:
        code = str(row.get("code") or "").upper()
        name = str(row.get("name") or code)
        mark = "✅ " if code == (current or "").upper() else ""
        builder.button(
            text=f"{mark}{name}"[:60], callback_data=f"sm:{plan}:country_set:{code or 'any'}"
        )
    builder.adjust(2)
    nav: list[InlineKeyboardButton] = []
    if page:
        nav.append(button("‹", f"sm:{plan}:country_page:{page - 1}"))
    if start + SP_COUNTRIES_PER_PAGE < len(rows):
        nav.append(button("›", f"sm:{plan}:country_page:{page + 1}"))
    if nav:
        builder.row(*nav)
    builder.row(button("🔎 Поиск страны", f"sm:{plan}:country_search"))
    builder.row(button("‹ Назад", f"sm:{plan}:home"))
    return builder.as_markup()


def sp_country_results_menu(plan: str, rows: Iterable[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        code = str(row.get("code") or "").upper()
        name = str(row.get("name") or code)
        builder.row(button(name[:60], f"sm:{plan}:country_set:{code or 'any'}"))
    builder.row(button("🔎 Искать снова", f"sm:{plan}:country_search"))
    builder.row(button("‹ Назад", f"sm:{plan}:country"))
    return builder.as_markup()


def sp_rotation_menu(plan: str, current: str) -> InlineKeyboardMarkup:
    rows = [
        [
            button(
                ("✅ " if current != "sticky" else "") + "🔁 Ротация (частая смена IP)",
                f"sm:{plan}:rotation_set:rotating",
            )
        ],
        [
            button(
                ("✅ " if current == "sticky" else "") + "📌 Фиксированный IP (сессия)",
                f"sm:{plan}:rotation_set:sticky",
            )
        ],
        [button("‹ Назад", f"sm:{plan}:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sp_session_menu(plan: str, current: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value, label in SP_SESSION_TTLS:
        mark = "✅ " if int(current) == value else ""
        builder.button(text=f"{mark}{label}", callback_data=f"sm:{plan}:session_set:{value}")
    builder.adjust(2)
    builder.row(button("‹ Назад", f"sm:{plan}:home"))
    return builder.as_markup()


def sp_format_menu(plan: str, current: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key in PROXY_FORMATS:
        mark = "✅ " if key == current else ""
        builder.button(
            text=f"{mark}{format_label(key)}", callback_data=f"sm:{plan}:format_set:{key}"
        )
    builder.adjust(1)
    builder.row(button("‹ Назад", f"sm:{plan}:home"))
    return builder.as_markup()


def sp_count_menu(plan: str, current: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value in SP_COUNTS:
        mark = "✅ " if int(current) == value else ""
        builder.button(text=f"{mark}{value}", callback_data=f"sm:{plan}:count_set:{value}")
    builder.adjust(4)
    builder.row(button("✏️ Своё число", f"sm:{plan}:count_custom"))
    builder.row(button("‹ Назад", f"sm:{plan}:home"))
    return builder.as_markup()


def sp_topup_menu(plan: str, prices_kopecks: dict[float, int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for gb in SP_TOPUP_GB:
        price = prices_kopecks.get(gb, 0) / 100
        builder.button(
            text=f"{gb:g} GB · {price:.2f} $", callback_data=f"sm:{plan}:topup_buy:{gb:g}"
        )
    builder.adjust(2)
    builder.row(button("✏️ Своя сумма", f"sm:{plan}:topup_custom"))
    builder.row(button("‹ Назад", f"sm:{plan}:home"))
    return builder.as_markup()


def sp_password_confirm(plan: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("⚠️ Да, сменить пароль", f"sm:{plan}:password_go")],
            [button("‹ Отмена", f"sm:{plan}:home")],
        ]
    )
