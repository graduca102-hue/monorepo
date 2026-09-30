from __future__ import annotations

from typing import Any, Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .clients import sale_price_kopecks
from .ui import product_display_title, YOUTUBE_CATEGORY_IDS


def button(text: str, data: str, *, style: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data, style=style)


def main_menu(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [button("📸 Instagram", "cat:instagram"), button("▶️ YouTube", "cat:youtube")],
        [button("🌐 Proxy", "cat:proxy")],
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


PROXY_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("9proxy", "9Proxy | IPs / GB", ("9proxy", "9proxy_gb")),
    ("piaproxy", "PIA Proxy | IPs / GB", ("piaproxy", "piaproxy_gb")),
    ("922proxy", "922 Proxy | IPs / GB", ("922proxy", "922proxy_gb")),
    ("abcproxy", "ABC Proxy | IPs / GB", ("abcproxy", "abcproxy_gb")),
    ("lokiproxy", "LokiProxy | IPs", ("lokiproxy",)),
    ("proxy001", "Proxy 001 | GB", ("proxy001_gb",)),
    ("711proxy", "711 Proxy | IPs", ("711proxy",)),
    ("cliproxy", "CliProxy | IPs", ("cliproxy",)),
)

PROXY_VARIANT_LABELS = {
    "9proxy": "9Proxy | IPs",
    "9proxy_gb": "9Proxy | GB",
    "piaproxy": "PIA Proxy | IPs",
    "piaproxy_gb": "PIA Proxy | GB",
    "922proxy": "922 Proxy | IPs",
    "922proxy_gb": "922 Proxy | GB",
    "abcproxy": "ABC Proxy | IPs",
    "abcproxy_gb": "ABC Proxy | GB",
    "lokiproxy": "LokiProxy | IPs",
    "proxy001_gb": "Proxy 001 | GB",
    "711proxy": "711 Proxy | IPs",
    "cliproxy": "CliProxy | IPs",
}


def service_display_label(code: str, overrides: dict[str, Any] | None = None) -> str:
    """Admin-defined name wins over the built-in label for a proxy service."""
    custom = str(((overrides or {}).get(code) or {}).get("custom_title") or "").strip()
    if custom:
        return custom
    if code == "residential":
        return "Резидентские"
    return PROXY_VARIANT_LABELS.get(code, code)


def is_service_hidden(code: str, overrides: dict[str, Any] | None = None) -> bool:
    return bool(((overrides or {}).get(code) or {}).get("hidden"))


def proxy_menu(
    services: Iterable[dict[str, Any]],
    rate: float,
    markup: float,
    overrides: dict[str, Any] | None = None,
) -> InlineKeyboardMarkup:
    available = {str(item.get("service", "")): item for item in services}
    rows: list[list[InlineKeyboardButton]] = []

    if "residential" in available and not is_service_hidden("residential", overrides):
        residential = available.get("residential", {})
        residential_kopecks = sale_price_kopecks(residential.get("price", 0.4), rate, markup)
        label = service_display_label("residential", overrides)
        rows.append(
            [
                button(
                    f"🌐 {label} | {residential_kopecks / 100:.2f} $ / GB",
                    "pxs:residential",
                    style="success",
                )
            ]
        )

    for group, label, codes in PROXY_GROUPS:
        present = [
            code
            for code in codes
            if code in available and not is_service_hidden(code, overrides)
        ]
        if not present:
            continue
        if len(present) > 1:
            rows.append([button(label, f"pxg:{group}")])
        else:
            # Only one variant left, so link straight to it and honour its name.
            rows.append([button(service_display_label(present[0], overrides), f"pxs:{present[0]}")])
    rows.append([button("Назад", "menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def proxy_group_variants(
    group: str,
    services: Iterable[dict[str, Any]],
    overrides: dict[str, Any] | None = None,
) -> InlineKeyboardMarkup:
    available = {str(item.get("service", "")) for item in services}
    codes: tuple[str, ...] = ()
    for group_code, _, group_services in PROXY_GROUPS:
        if group_code == group:
            codes = group_services
            break
    rows = [
        [button(service_display_label(code, overrides), f"pxs:{code}")]
        for code in codes
        if code in available and not is_service_hidden(code, overrides)
    ]
    rows.append([button("↶ Назад", "cat:proxy")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def proxy_group_title(group: str) -> str:
    for group_code, label, _ in PROXY_GROUPS:
        if group_code == group:
            return label.split(" |", 1)[0]
    return group


def proxy_service_back(service: str) -> str:
    for group, _, codes in PROXY_GROUPS:
        if service in codes and len(codes) > 1:
            return f"pxg:{group}"
    return "cat:proxy"


def proxy_countries(countries: Iterable[dict[str, Any]], prices: dict[int, int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for country in countries:
        cid = int(country["id"])
        label = f"{country.get('country_name', country.get('country_code', 'Прокси'))} · {prices[cid] / 100:.2f} $"
        builder.button(text=label[:60], callback_data=f"pxc:{cid}")
    builder.adjust(1)
    builder.row(button("‹ Назад", "cat:proxy"))
    return builder.as_markup()


def proxy_protocols(country_id: int, protocols: Iterable[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for protocol in protocols:
        stock = int(protocol.get("stock", 0))
        builder.row(
            button(
                f"{protocol.get('name', 'Proxy')} · в наличии {stock}",
                f"pxp:{country_id}:{int(protocol['id'])}",
            )
        )
    builder.row(button("‹ Назад", "px:dedicated"))
    return builder.as_markup()


def proxy_service_list(services: Iterable[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for service in services:
        code = str(service.get("service", ""))
        unit = service.get("unit", "")
        price = float(service.get("price", 0))
        builder.row(button(f"{code} · от {price:g} USDT/{unit}", f"pxs:{code}"))
    builder.row(button("‹ Назад", "cat:proxy"))
    return builder.as_markup()


def service_tariffs(
    service: dict[str, Any], rate: float, markup: float, back_data: str = "cat:proxy"
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    code = str(service.get("service", ""))
    tariffs = service.get("tariffs") or []
    if code == "residential":
        unit_kopecks = sale_price_kopecks(service.get("price", 0.4), rate, markup)
        for quantity in (1, 5, 10, 20):
            total = unit_kopecks * quantity / 100
            builder.button(text=f"{quantity} GB · {total:.2f} $", callback_data=f"pxsb:{code}:{quantity}")
        builder.adjust(2)
    elif tariffs:
        for tariff in tariffs:
            quantity = tariff.get("quantity")
            total_kopecks = sale_price_kopecks(tariff.get("total_price", 0), rate, markup)
            builder.button(
                text=f"{quantity} · {total_kopecks / 100:.2f} $", callback_data=f"pxsb:{code}:{quantity}"
            )
        builder.adjust(2)
    else:
        minimum = service.get("min_quantity", 1)
        builder.row(button(f"Купить {minimum}", f"pxsb:{code}:{minimum}"))
    builder.row(button("↶ Назад", back_data))
    return builder.as_markup()


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
            [button("🧪 Проверить SOUS", "admintest:sous")],
            [button("🧪 Проверить Heleket", "admintest:heleket")],
            [button("‹ Назад", "admin:home")],
        ]
    )


def admin_pricing_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("Множитель цены (USDT)", "adminset:usdt_rub_rate")],
            [button("Наценка Instagram/YouTube", "adminset:market_markup_percent")],
            [button("Наценка Proxy", "adminset:proxy_markup_percent")],
            [button("Реферальный процент", "adminset:referral_percent")],
            [button("Мин. цена товара ($)", "adminset:market_min_price_usd")],
            [button("‹ Назад", "admin:home")],
        ]
    )


def cancel_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[button("Отмена", "admin:home")]])


def admin_products_platform() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("📸 Instagram", "apcat:instagram"), button("▶️ YouTube", "apcat:youtube")],
            [button("🌐 Proxy", "apcat:proxy")],
            [button("‹ Назад", "admin:home")],
        ]
    )


def admin_service_list(
    services: Iterable[dict[str, Any]], overrides: dict[str, Any]
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for service in services:
        code = str(service.get("service", ""))
        if not code:
            continue
        override = overrides.get(code) or {}
        title = service_display_label(code, overrides)
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


# --- Residential proxy management ------------------------------------------------

RESIDENTIAL_COUNTRIES: tuple[tuple[str, str], ...] = (
    ("", "🌍 Любая"),
    ("UA", "🇺🇦 Украина"),
    ("US", "🇺🇸 США"),
    ("GB", "🇬🇧 Британия"),
    ("DE", "🇩🇪 Германия"),
    ("FR", "🇫🇷 Франция"),
    ("PL", "🇵🇱 Польша"),
    ("NL", "🇳🇱 Нидерланды"),
    ("ES", "🇪🇸 Испания"),
    ("IT", "🇮🇹 Италия"),
    ("KZ", "🇰🇿 Казахстан"),
    ("TR", "🇹🇷 Турция"),
    ("CA", "🇨🇦 Канада"),
)

RESIDENTIAL_SESSION_TTLS: tuple[tuple[int, str], ...] = (
    (0, "Без сессии"),
    (300, "5 минут"),
    (900, "15 минут"),
    (1800, "30 минут"),
    (3600, "1 час"),
)

RESIDENTIAL_FORMATS: dict[str, tuple[str, str]] = {
    # key -> (api format string, human label). Every delivery is repointed at
    # our own relay IP afterwards, so the labels show ip:port, not host:port.
    "hpu": ("hostname:port:login:password", "ip:port:login:pass"),
    "uph": ("login:password@hostname:port", "login:pass@ip:port"),
    "hpa": ("hostname:port@login:password", "ip:port@login:pass"),
    "url": ("protocol://login:password@hostname:port", "http://login:pass@ip:port"),
}

RESIDENTIAL_COUNTS: tuple[int, ...] = (1, 3, 5, 10, 25, 50, 100)
RESIDENTIAL_TOPUP_GB: tuple[int, ...] = (1, 5, 10, 20)


def _fmt_key(settings: dict[str, Any]) -> str:
    target = str(settings.get("format") or "hostname:port:login:password")
    for key, (api_value, _) in RESIDENTIAL_FORMATS.items():
        if api_value == target:
            return key
    return "hpu"


def residential_format_label(settings: dict[str, Any]) -> str:
    return RESIDENTIAL_FORMATS[_fmt_key(settings)][1]


def residential_country_label(code: str) -> str:
    for value, label in RESIDENTIAL_COUNTRIES:
        if value == (code or ""):
            return label
    return f"🏳 {code}" if code else "🌍 Любая"


def residential_manage_menu(
    settings: dict[str, Any], *, has_traffic: bool = True, price_per_gb: float = 0.0
) -> InlineKeyboardMarkup:
    topup_label = "➕ Пополнить трафик"
    if price_per_gb > 0:
        topup_label += f" · {price_per_gb:.2f} $/GB"
    rows: list[list[InlineKeyboardButton]] = [
        [button(topup_label, "rm:topup")],
        [button("📥 Получить прокси", "rm:get", style="success")],
        [button("♻️ Проверить и заменить нерабочие", "rm:recheck")],
        [
            button(residential_country_label(settings.get("country", "")), "rm:country"),
            button(
                "🔁 Ротация" if settings.get("rotation") == "rotating" else "📌 Фикс. IP",
                "rm:rotation",
            ),
        ],
        [
            button("⏱ Сессия", "rm:session"),
            button(f"🔢 {int(settings.get('proxy_count') or 1)} шт", "rm:count"),
        ],
        [button(f"🧩 Формат: {residential_format_label(settings)}", "rm:format")],
        [button("♻️ Сбросить настройки", "rm:reset"), button("🔑 Пароль", "rm:password")],
        [button("‹ Назад", "px:services")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Used only if the live country list from the reseller API is unavailable.
RESIDENTIAL_COUNTRIES_FALLBACK: tuple[dict[str, str], ...] = tuple(
    {"code": value, "name": label} for value, label in RESIDENTIAL_COUNTRIES if value
)

RESIDENTIAL_COUNTRIES_PER_PAGE = 8


def residential_country_menu(
    countries: Iterable[dict[str, Any]], current: str, page: int = 0
) -> InlineKeyboardMarkup:
    rows = list(countries)
    per_page = RESIDENTIAL_COUNTRIES_PER_PAGE
    start = page * per_page
    builder = InlineKeyboardBuilder()
    builder.button(
        text=("✅ " if not current else "") + "🌍 Любая",
        callback_data="rm:country:set:any",
    )
    for row in rows[start : start + per_page]:
        code = str(row.get("code") or "").upper()
        name = str(row.get("name") or code)
        mark = "✅ " if code == (current or "") else ""
        builder.button(text=f"{mark}{name}", callback_data=f"rm:country:set:{code or 'any'}")
    builder.adjust(2)
    nav = []
    if page:
        nav.append(button("‹", f"rm:country:page:{page - 1}"))
    if start + per_page < len(rows):
        nav.append(button("›", f"rm:country:page:{page + 1}"))
    if nav:
        builder.row(*nav)
    builder.row(button("🔎 Поиск страны", "rm:country:search"))
    builder.row(button("‹ Назад", "rm:home"))
    return builder.as_markup()


def residential_country_results_menu(rows: Iterable[dict[str, Any]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for row in rows:
        code = str(row.get("code") or "").upper()
        name = str(row.get("name") or code)
        builder.row(button(name, f"rm:country:set:{code or 'any'}"))
    builder.row(button("🔎 Искать снова", "rm:country:search"))
    builder.row(button("‹ Назад", "rm:country"))
    return builder.as_markup()


def residential_rotation_menu(current: str) -> InlineKeyboardMarkup:
    rows = [
        [button(("✅ " if current == "rotating" else "") + "🔁 Ротация (каждый запрос)", "rm:rotation:set:rotating")],
        [button(("✅ " if current == "sticky" else "") + "📌 Фиксированный IP (сессия)", "rm:rotation:set:sticky")],
        [button("‹ Назад", "rm:home")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def residential_session_menu(current: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value, label in RESIDENTIAL_SESSION_TTLS:
        mark = "✅ " if int(current) == value else ""
        builder.button(text=f"{mark}{label}", callback_data=f"rm:session:set:{value}")
    builder.adjust(2)
    builder.row(button("‹ Назад", "rm:home"))
    return builder.as_markup()


def residential_format_menu(settings: dict[str, Any]) -> InlineKeyboardMarkup:
    current = _fmt_key(settings)
    builder = InlineKeyboardBuilder()
    for key, (_, label) in RESIDENTIAL_FORMATS.items():
        mark = "✅ " if key == current else ""
        builder.button(text=f"{mark}{label}", callback_data=f"rm:format:set:{key}")
    builder.adjust(1)
    builder.row(button("‹ Назад", "rm:home"))
    return builder.as_markup()


def residential_count_menu(current: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for value in RESIDENTIAL_COUNTS:
        mark = "✅ " if int(current) == value else ""
        builder.button(text=f"{mark}{value}", callback_data=f"rm:count:set:{value}")
    builder.adjust(4)
    builder.row(button("✏️ Своё число", "rm:count:custom"))
    builder.row(button("‹ Назад", "rm:home"))
    return builder.as_markup()


def residential_topup_menu(prices_kopecks: dict[int, int]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for gb in RESIDENTIAL_TOPUP_GB:
        price = prices_kopecks.get(gb, 0) / 100
        builder.button(text=f"{gb} GB · {price:.2f} $", callback_data=f"rm:topup:{gb}")
    builder.adjust(2)
    builder.row(button("‹ Назад", "rm:home"))
    return builder.as_markup()


def residential_password_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [button("⚠️ Да, сменить пароль", "rm:password:go")],
            [button("‹ Отмена", "rm:home")],
        ]
    )
