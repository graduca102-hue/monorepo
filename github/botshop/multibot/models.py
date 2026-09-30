from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(slots=True, frozen=True)
class PartnerBot:
    id: int
    owner_id: int
    bot_token: str
    bot_username: str
    margin_percentage: int
    is_active: bool


@dataclass(slots=True, frozen=True)
class OrderCreate:
    bot_id: int
    user_id: int
    product_id: int
    purchase_price: Decimal
    final_price: Decimal
    partner_profit: Decimal


@dataclass(slots=True, frozen=True)
class PartnerSummary:
    bot_id: int
    total_orders: int
    total_revenue: Decimal
    partner_balance: Decimal
