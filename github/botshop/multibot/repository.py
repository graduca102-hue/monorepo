from __future__ import annotations

from decimal import Decimal

import asyncpg

from .models import OrderCreate, PartnerBot, PartnerSummary


class PartnerRepository:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @staticmethod
    def _map_partner(row: asyncpg.Record) -> PartnerBot:
        return PartnerBot(
            id=row["id"],
            owner_id=row["owner_id"],
            bot_token=row["bot_token"],
            bot_username=row["bot_username"],
            margin_percentage=row["margin_percentage"],
            is_active=row["is_active"],
        )

    async def fetch_active_partner_bots(self) -> list[PartnerBot]:
        query = """
        SELECT id, owner_id, bot_token, bot_username, margin_percentage, is_active
        FROM partners_bots
        WHERE is_active = TRUE
        ORDER BY id ASC
        """
        async with self.pool.acquire() as connection:
            rows = await connection.fetch(query)
        return [self._map_partner(row) for row in rows]

    async def get_partner_bot(self, bot_id: int) -> PartnerBot | None:
        query = """
        SELECT id, owner_id, bot_token, bot_username, margin_percentage, is_active
        FROM partners_bots
        WHERE id = $1
        """
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(query, bot_id)
        return self._map_partner(row) if row else None

    async def upsert_partner_bot(
        self,
        owner_id: int,
        bot_token: str,
        bot_username: str,
        margin_percentage: int = 0,
        is_active: bool = True,
    ) -> PartnerBot:
        query = """
        INSERT INTO partners_bots (
            owner_id,
            bot_token,
            bot_username,
            margin_percentage,
            is_active
        )
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (bot_token) DO UPDATE
        SET
            owner_id = EXCLUDED.owner_id,
            bot_username = EXCLUDED.bot_username,
            margin_percentage = EXCLUDED.margin_percentage,
            is_active = EXCLUDED.is_active
        RETURNING id, owner_id, bot_token, bot_username, margin_percentage, is_active
        """
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(
                query,
                owner_id,
                bot_token,
                bot_username,
                margin_percentage,
                is_active,
            )
        return self._map_partner(row)

    async def set_partner_bot_active(self, bot_id: int, is_active: bool) -> None:
        query = """
        UPDATE partners_bots
        SET is_active = $2
        WHERE id = $1
        """
        async with self.pool.acquire() as connection:
            await connection.execute(query, bot_id, is_active)

    async def create_order(self, payload: OrderCreate) -> int:
        query = """
        INSERT INTO orders (
            bot_id,
            user_id,
            product_id,
            purchase_price,
            final_price,
            partner_profit
        )
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """
        async with self.pool.acquire() as connection:
            order_id = await connection.fetchval(
                query,
                payload.bot_id,
                payload.user_id,
                payload.product_id,
                payload.purchase_price,
                payload.final_price,
                payload.partner_profit,
            )
        return int(order_id)

    async def get_partner_summary(self, bot_id: int) -> PartnerSummary:
        query = """
        SELECT
            $1::INTEGER AS bot_id,
            COUNT(*)::INTEGER AS total_orders,
            COALESCE(SUM(final_price), 0)::NUMERIC AS total_revenue,
            COALESCE(SUM(partner_profit), 0)::NUMERIC AS partner_balance
        FROM orders
        WHERE bot_id = $1
        """
        async with self.pool.acquire() as connection:
            row = await connection.fetchrow(query, bot_id)

        return PartnerSummary(
            bot_id=bot_id,
            total_orders=row["total_orders"],
            total_revenue=Decimal(row["total_revenue"]),
            partner_balance=Decimal(row["partner_balance"]),
        )
