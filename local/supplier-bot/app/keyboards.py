"""Inline keyboards."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton as Button
from aiogram.types import InlineKeyboardMarkup

from .catalog import shorten
from .config import Config


def _markup(rows: list[list[Button]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def support_button(cfg: Config) -> Button:
    return Button(text="🆘 Тех поддержка", url=cfg.support_url)


def home_button() -> Button:
    return Button(text="🏠 Меню", callback_data="home")


def guest(cfg: Config, *, can_apply: bool) -> InlineKeyboardMarkup:
    rows = [[Button(text="📝 Подать заявку", callback_data="apply")]] if can_apply else []
    rows.append([support_button(cfg)])
    return _markup(rows)


def supplier_menu(cfg: Config) -> InlineKeyboardMarkup:
    return _markup([
        [Button(text="➕ Выставить товар", callback_data="card_new")],
        [Button(text="🗂 Мои товары", callback_data="cards")],
        [Button(text="📋 Условия", callback_data="terms")],
        [support_button(cfg)],
    ])


def cancel() -> InlineKeyboardMarkup:
    return _markup([[Button(text="✖️ Отмена", callback_data="home")]])


def skip_contact() -> InlineKeyboardMarkup:
    return _markup([
        [Button(text="Пропустить", callback_data="apply_skip")],
        [Button(text="✖️ Отмена", callback_data="home")],
    ])


def home_and_support(cfg: Config) -> InlineKeyboardMarkup:
    return _markup([[home_button()], [support_button(cfg)]])


def open_menu() -> InlineKeyboardMarkup:
    return _markup([[Button(text="📦 Открыть меню поставщика", callback_data="home")]])


def decide(user_id: int) -> InlineKeyboardMarkup:
    return _markup([[
        Button(text="✅ Принять", callback_data=f"sup_ok:{user_id}"),
        Button(text="❌ Отклонить", callback_data=f"sup_no:{user_id}"),
    ]])


def categories(nodes: list[dict], back: str) -> InlineKeyboardMarkup:
    rows = [
        [Button(
            text=shorten(node["name"]) + (" ›" if node["children"] else ""),
            callback_data=f"nc_cat:{node['id']}",
        )]
        for node in nodes
    ]
    rows.append([Button(text="⬅️ Назад", callback_data=back)])
    return _markup(rows)


def confirm_card() -> InlineKeyboardMarkup:
    return _markup([
        [Button(text="✅ Сохранить", callback_data="nc_save")],
        [Button(text="✖️ Отмена", callback_data="home")],
    ])


def cards_list(products: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [Button(text=f"{shorten(p['title'], 32)} · {p['available']} шт", callback_data=f"card:{p['id']}")]
        for p in products
    ]
    rows.append([Button(text="➕ Выставить товар", callback_data="card_new")])
    rows.append([home_button()])
    return _markup(rows)


def card(product_id: int) -> InlineKeyboardMarkup:
    return _markup([
        [Button(text="📤 Загрузить товар", callback_data=f"card_up:{product_id}")],
        [Button(text="🗑 Удалить", callback_data=f"card_del:{product_id}")],
        [Button(text="⬅️ Мои товары", callback_data="cards")],
    ])


def confirm_delete(product_id: int) -> InlineKeyboardMarkup:
    return _markup([
        [Button(text="🗑 Да, удалить", callback_data=f"card_delok:{product_id}")],
        [Button(text="⬅️ Нет", callback_data=f"card:{product_id}")],
    ])


def upload_cancel(product_id: int) -> InlineKeyboardMarkup:
    return _markup([[Button(text="✖️ Отмена", callback_data=f"card:{product_id}")]])


def after_upload(product_id: int) -> InlineKeyboardMarkup:
    return _markup([
        [Button(text="📤 Загрузить ещё", callback_data=f"card_up:{product_id}")],
        [Button(text="🏷 К карточке", callback_data=f"card:{product_id}")],
        [home_button()],
    ])
