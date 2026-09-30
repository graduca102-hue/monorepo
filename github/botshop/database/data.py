import os
import hashlib
import secrets
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from dotenv import load_dotenv

from i18n import DEFAULT_LANGUAGE, normalize_language_code


load_dotenv()

DB_PATH = "data1.db"
# The main bot shares half of its actual profit from a referred user's
# completed purchase.  Existing referral relationships are intentionally not
# migrated or recreated: only the reward calculation for future purchases
# uses this value.
DEFAULT_REFERRAL_PERCENT = 50.0
DEFAULT_PARTNER_REFERRAL_PERCENT = 25.0
# A partner owner receives a fixed share of the profit earned by every
# partner owner they personally invited to SousPartners.
PARTNER_OWNER_REFERRAL_PERCENT = 15.0
LEGACY_TOPUP_REFERRAL_PERCENT = 25.0
LEGACY_REFERRAL_MODEL = "legacy_topup_25"
MARKET_PROFIT_REFERRAL_MODEL = "market_profit_50"
DEFAULT_PARTNER_MARGIN_PERCENT = int(float(os.getenv("PARTNER_DEFAULT_MARGIN_PERCENT", "50") or 50))
PARTNER_TOPUP_SHARE_PERCENT = min(max(float(os.getenv("PARTNER_TOPUP_SHARE_PERCENT", "25") or 25), 0.0), 100.0)
MIN_PARTNER_REFERRAL_PERCENT = 10.0
MAX_PARTNER_REFERRAL_PERCENT = 100.0
PARTNER_MIN_WITHDRAW_AMOUNT = max(
    0.01,
    float(os.getenv("PARTNER_MIN_WITHDRAW_AMOUNT", "5") or 5),
)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _begin_immediate(conn: sqlite3.Connection) -> sqlite3.Cursor:
    cursor = conn.cursor()
    cursor.execute("BEGIN IMMEDIATE")
    return cursor


def _rollback_quietly(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        pass


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def _legacy_provider_order_column_name() -> str:
    return "".join(["proxy", "soxy", "_order_id"])


def _ensure_orders_provider_column(cursor) -> None:
    current_name = "provider_order_id"
    legacy_name = _legacy_provider_order_column_name()
    if _column_exists(cursor, "orders", current_name):
        return
    if _column_exists(cursor, "orders", legacy_name):
        cursor.execute(f"ALTER TABLE orders RENAME COLUMN {legacy_name} TO {current_name}")
        return
    cursor.execute(f"ALTER TABLE orders ADD COLUMN {current_name} INTEGER")


def _generate_referral_code(user_id: int) -> str:
    return str(user_id)


def _normalize_partner_referral_percent(value: float | int | None) -> float:
    return min(max(float(value or 0.0), MIN_PARTNER_REFERRAL_PERCENT), MAX_PARTNER_REFERRAL_PERCENT)


def _now_text() -> str:
    return datetime.now().strftime("%H:%M:%S %d-%m-%Y")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M:%S %d-%m-%Y")
    except ValueError:
        return None


def create_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS users
        (
            user_id INTEGER PRIMARY KEY
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS orders
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            proxy_kind TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            category_name TEXT NOT NULL,
            item_id INTEGER NOT NULL,
            protocol TEXT NOT NULL,
            product_title TEXT,
            quantity INTEGER NOT NULL,
            unit_price REAL NOT NULL,
            total_price REAL NOT NULL,
            payment_provider TEXT,
            payment_asset TEXT,
            xrocket_invoice_id TEXT,
            xrocket_client_invoice_id TEXT,
            xrocket_pay_url TEXT,
            provider_order_id INTEGER,
            supplier_order_uuid TEXT,
            supplier_order_number TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            delivery_text TEXT,
            reminder_sent INTEGER NOT NULL DEFAULT 0,
            reminder_sent_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS topups
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            payment_provider TEXT,
            payment_asset TEXT,
            partner_bot_id INTEGER,
            partner_credit_accrued INTEGER NOT NULL DEFAULT 0,
            balance_kind TEXT NOT NULL DEFAULT 'main',
            credited_user_id INTEGER,
            xrocket_invoice_id TEXT,
            xrocket_pay_url TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            reminder_sent INTEGER NOT NULL DEFAULT 0,
            reminder_sent_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS payment_invoices
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            payment_provider TEXT NOT NULL,
            client_invoice_id TEXT,
            invoice_id TEXT NOT NULL,
            pay_url TEXT,
            payment_asset TEXT,
            created_at TEXT NOT NULL,
            created_at_unix INTEGER NOT NULL,
            UNIQUE(entity_type, payment_provider, invoice_id)
        )
        """
    )
    _ensure_orders_provider_column(cursor)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS partners_bots
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            bot_token TEXT NOT NULL UNIQUE,
            bot_username TEXT NOT NULL,
            shop_markup_percentage INTEGER,
            margin_percentage INTEGER NOT NULL DEFAULT 50,
            goods_markup_percentage INTEGER,
            proxy_markup_percentage INTEGER,
            sms_markup_percentage INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1,
            partner_earnings REAL NOT NULL DEFAULT 0.0,
            partner_withdrawn REAL NOT NULL DEFAULT 0.0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_bot_users
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(bot_id, user_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_withdrawals
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_bot_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            asset TEXT NOT NULL,
            xrocket_transfer_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_referral_withdrawals
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            asset TEXT NOT NULL,
            xrocket_transfer_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_payout_requests
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_bot_id INTEGER NOT NULL,
            owner_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            asset TEXT NOT NULL DEFAULT 'USDT',
            destination TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            admin_note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_referral_whitelist
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_bot_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            referral_percent REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(partner_bot_id, user_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_bot_referrals
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_bot_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            referred_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(partner_bot_id, user_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_bot_starts
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            start_param TEXT,
            utm_source TEXT,
            utm_medium TEXT,
            utm_campaign TEXT,
            utm_content TEXT,
            utm_term TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(bot_id, user_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_bot_utm_links
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id INTEGER NOT NULL,
            source_word TEXT NOT NULL,
            start_param TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(bot_id, source_word),
            UNIQUE(bot_id, start_param)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_earning_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_bot_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            amount REAL NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(source_type, source_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_owner_referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_owner_id INTEGER NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            UNIQUE(referrer_id, referred_owner_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS partner_owner_referral_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_owner_id INTEGER NOT NULL,
            partner_bot_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            amount REAL NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(source_type, source_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS app_migrations (
            migration_key TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS promo_codes
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            reward_type TEXT NOT NULL,
            reward_value REAL NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            activations_count INTEGER NOT NULL DEFAULT 0,
            max_activations INTEGER,
            created_by INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS promo_code_activations
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            promo_code_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reward_type TEXT NOT NULL,
            reward_value REAL NOT NULL,
            activated_at TEXT NOT NULL,
            UNIQUE(promo_code_id, user_id)
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS main_bot_settings
        (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            subscription_enabled INTEGER NOT NULL DEFAULT 0,
            subscription_channel_id TEXT,
            subscription_channel_url TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sales_log_events
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_kind TEXT NOT NULL,
            order_id INTEGER,
            topup_id INTEGER,
            user_id INTEGER,
            shop_name TEXT,
            event_label TEXT,
            message_text TEXT NOT NULL,
            telegram_status TEXT NOT NULL DEFAULT 'pending',
            telegram_error TEXT,
            telegram_message_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS email_activations
        (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_key TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            partner_bot_id INTEGER,
            site TEXT NOT NULL,
            domain TEXT NOT NULL,
            provider_activation_id TEXT,
            email TEXT,
            supplier_price_usd REAL NOT NULL,
            sale_price_usd REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'ordering',
            message_code TEXT,
            message_text TEXT,
            error_code TEXT,
            charged INTEGER NOT NULL DEFAULT 1,
            refunded INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, request_key)
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_email_activations_user ON email_activations(user_id, id DESC)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_email_activations_provider "
        "ON email_activations(provider_activation_id) WHERE provider_activation_id IS NOT NULL"
    )

    migrations = {
        "balance": "ALTER TABLE users ADD COLUMN balance REAL NOT NULL DEFAULT 0.0",
        "purchases_count": "ALTER TABLE users ADD COLUMN purchases_count INTEGER NOT NULL DEFAULT 0",
        "purchases_total": "ALTER TABLE users ADD COLUMN purchases_total REAL NOT NULL DEFAULT 0.0",
        "topups_total": "ALTER TABLE users ADD COLUMN topups_total REAL NOT NULL DEFAULT 0.0",
        "referral_earnings": "ALTER TABLE users ADD COLUMN referral_earnings REAL NOT NULL DEFAULT 0.0",
        "partner_referral_earnings": "ALTER TABLE users ADD COLUMN partner_referral_earnings REAL NOT NULL DEFAULT 0.0",
        "partner_referral_withdrawn": "ALTER TABLE users ADD COLUMN partner_referral_withdrawn REAL NOT NULL DEFAULT 0.0",
        "registered_at": "ALTER TABLE users ADD COLUMN registered_at TEXT",
        "referral_code": "ALTER TABLE users ADD COLUMN referral_code TEXT",
        "referred_by": "ALTER TABLE users ADD COLUMN referred_by INTEGER",
        # The default deliberately marks every relation created before this
        # migration as legacy. New main-bot relations are explicitly switched
        # to MARKET_PROFIT_REFERRAL_MODEL in add_user().
        "referral_reward_model": (
            "ALTER TABLE users ADD COLUMN referral_reward_model "
            f"TEXT NOT NULL DEFAULT '{LEGACY_REFERRAL_MODEL}'"
        ),
        "start_param": "ALTER TABLE users ADD COLUMN start_param TEXT",
        "utm_source": "ALTER TABLE users ADD COLUMN utm_source TEXT",
        "utm_medium": "ALTER TABLE users ADD COLUMN utm_medium TEXT",
        "utm_campaign": "ALTER TABLE users ADD COLUMN utm_campaign TEXT",
        "utm_content": "ALTER TABLE users ADD COLUMN utm_content TEXT",
        "utm_term": "ALTER TABLE users ADD COLUMN utm_term TEXT",
        "active_discount_percent": "ALTER TABLE users ADD COLUMN active_discount_percent REAL NOT NULL DEFAULT 0.0",
        "active_discount_promo_id": "ALTER TABLE users ADD COLUMN active_discount_promo_id INTEGER",
        "active_discount_code": "ALTER TABLE users ADD COLUMN active_discount_code TEXT",
        "language_code": f"ALTER TABLE users ADD COLUMN language_code TEXT NOT NULL DEFAULT '{DEFAULT_LANGUAGE}'",
        "language_locked": "ALTER TABLE users ADD COLUMN language_locked INTEGER NOT NULL DEFAULT 0",
    }

    for column_name, statement in migrations.items():
        if not _column_exists(cursor, "users", column_name):
            cursor.execute(statement)

    order_migrations = {
        "payment_provider": "ALTER TABLE orders ADD COLUMN payment_provider TEXT",
        "product_title": "ALTER TABLE orders ADD COLUMN product_title TEXT",
        "partner_bot_id": "ALTER TABLE orders ADD COLUMN partner_bot_id INTEGER",
        "purchase_unit_price": "ALTER TABLE orders ADD COLUMN purchase_unit_price REAL NOT NULL DEFAULT 0.0",
        "partner_margin_percentage": "ALTER TABLE orders ADD COLUMN partner_margin_percentage INTEGER NOT NULL DEFAULT 0",
        "partner_profit_amount": "ALTER TABLE orders ADD COLUMN partner_profit_amount REAL NOT NULL DEFAULT 0.0",
        "partner_profit_accrued": "ALTER TABLE orders ADD COLUMN partner_profit_accrued INTEGER NOT NULL DEFAULT 0",
        "supplier_order_uuid": "ALTER TABLE orders ADD COLUMN supplier_order_uuid TEXT",
        "supplier_order_number": "ALTER TABLE orders ADD COLUMN supplier_order_number TEXT",
        "reminder_sent": "ALTER TABLE orders ADD COLUMN reminder_sent INTEGER NOT NULL DEFAULT 0",
        "reminder_sent_at": "ALTER TABLE orders ADD COLUMN reminder_sent_at TEXT",
        "original_unit_price": "ALTER TABLE orders ADD COLUMN original_unit_price REAL",
        "original_total_price": "ALTER TABLE orders ADD COLUMN original_total_price REAL",
        "promo_discount_percent": "ALTER TABLE orders ADD COLUMN promo_discount_percent REAL NOT NULL DEFAULT 0.0",
        "promo_code_id": "ALTER TABLE orders ADD COLUMN promo_code_id INTEGER",
        "promo_code_text": "ALTER TABLE orders ADD COLUMN promo_code_text TEXT",
    }
    for column_name, statement in order_migrations.items():
        if not _column_exists(cursor, "orders", column_name):
            cursor.execute(statement)

    topup_migrations = {
        "payment_provider": "ALTER TABLE topups ADD COLUMN payment_provider TEXT",
        "partner_bot_id": "ALTER TABLE topups ADD COLUMN partner_bot_id INTEGER",
        "partner_credit_accrued": "ALTER TABLE topups ADD COLUMN partner_credit_accrued INTEGER NOT NULL DEFAULT 0",
        "balance_kind": "ALTER TABLE topups ADD COLUMN balance_kind TEXT NOT NULL DEFAULT 'main'",
        "reminder_sent": "ALTER TABLE topups ADD COLUMN reminder_sent INTEGER NOT NULL DEFAULT 0",
        "reminder_sent_at": "ALTER TABLE topups ADD COLUMN reminder_sent_at TEXT",
        "credited_user_id": "ALTER TABLE topups ADD COLUMN credited_user_id INTEGER",
    }
    for column_name, statement in topup_migrations.items():
        if not _column_exists(cursor, "topups", column_name):
            cursor.execute(statement)

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS api_accounts (
            user_id INTEGER PRIMARY KEY,
            api_user_id INTEGER UNIQUE,
            status TEXT NOT NULL DEFAULT 'pending',
            api_key TEXT,
            api_key_hash TEXT,
            api_key_prefix TEXT,
            balance REAL NOT NULL DEFAULT 0.0,
            requested_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    if not _column_exists(cursor, "api_accounts", "api_user_id"):
        cursor.execute("ALTER TABLE api_accounts ADD COLUMN api_user_id INTEGER")
    if not _column_exists(cursor, "api_accounts", "api_key"):
        cursor.execute("ALTER TABLE api_accounts ADD COLUMN api_key TEXT")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_api_accounts_key_hash ON api_accounts(api_key_hash) WHERE api_key_hash IS NOT NULL")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS web_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            email TEXT NOT NULL COLLATE NOCASE UNIQUE,
            username TEXT NOT NULL,
            password_salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS web_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY(account_id) REFERENCES web_accounts(id) ON DELETE CASCADE
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_web_sessions_account ON web_sessions(account_id)")

    partner_migrations = {
        "shop_markup_percentage": "ALTER TABLE partners_bots ADD COLUMN shop_markup_percentage INTEGER",
        "margin_percentage": "ALTER TABLE partners_bots ADD COLUMN margin_percentage INTEGER NOT NULL DEFAULT 0",
        "goods_markup_percentage": "ALTER TABLE partners_bots ADD COLUMN goods_markup_percentage INTEGER",
        "proxy_markup_percentage": "ALTER TABLE partners_bots ADD COLUMN proxy_markup_percentage INTEGER",
        "sms_markup_percentage": "ALTER TABLE partners_bots ADD COLUMN sms_markup_percentage INTEGER",
        "is_active": "ALTER TABLE partners_bots ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
        "partner_earnings": "ALTER TABLE partners_bots ADD COLUMN partner_earnings REAL NOT NULL DEFAULT 0.0",
        "partner_withdrawn": "ALTER TABLE partners_bots ADD COLUMN partner_withdrawn REAL NOT NULL DEFAULT 0.0",
        "subscription_enabled": "ALTER TABLE partners_bots ADD COLUMN subscription_enabled INTEGER NOT NULL DEFAULT 0",
        "subscription_channel_id": "ALTER TABLE partners_bots ADD COLUMN subscription_channel_id TEXT",
        "subscription_channel_url": "ALTER TABLE partners_bots ADD COLUMN subscription_channel_url TEXT",
        "franchise_news_url": "ALTER TABLE partners_bots ADD COLUMN franchise_news_url TEXT",
        "franchise_support_url": "ALTER TABLE partners_bots ADD COLUMN franchise_support_url TEXT",
        "franchise_usage_url": "ALTER TABLE partners_bots ADD COLUMN franchise_usage_url TEXT",
        "franchise_privacy_url": "ALTER TABLE partners_bots ADD COLUMN franchise_privacy_url TEXT",
        "franchise_info_brand": "ALTER TABLE partners_bots ADD COLUMN franchise_info_brand TEXT NOT NULL DEFAULT 'MARKET'",
        "franchise_hide_create": "ALTER TABLE partners_bots ADD COLUMN franchise_hide_create INTEGER NOT NULL DEFAULT 0",
        "referral_enabled": "ALTER TABLE partners_bots ADD COLUMN referral_enabled INTEGER NOT NULL DEFAULT 1",
        "referral_percent": "ALTER TABLE partners_bots ADD COLUMN referral_percent REAL NOT NULL DEFAULT 20",
        "created_at": "ALTER TABLE partners_bots ADD COLUMN created_at TEXT",
        "updated_at": "ALTER TABLE partners_bots ADD COLUMN updated_at TEXT",
    }
    for column_name, statement in partner_migrations.items():
        if not _column_exists(cursor, "partners_bots", column_name):
            cursor.execute(statement)

    cursor.execute(
        """
        UPDATE partners_bots
        SET goods_markup_percentage = COALESCE(goods_markup_percentage, margin_percentage),
            proxy_markup_percentage = COALESCE(proxy_markup_percentage, margin_percentage),
            sms_markup_percentage = COALESCE(sms_markup_percentage, margin_percentage)
        """
    )

    promo_migrations = {
        "max_activations": "ALTER TABLE promo_codes ADD COLUMN max_activations INTEGER",
    }
    for column_name, statement in promo_migrations.items():
        if not _column_exists(cursor, "promo_codes", column_name):
            cursor.execute(statement)

    now_value = _now_text()
    cursor.execute(
        """
        INSERT OR IGNORE INTO main_bot_settings (
            id,
            subscription_enabled,
            updated_at
        )
        VALUES (1, 0, ?)
        """,
        (now_value,),
    )
    cursor.execute(
        "UPDATE users SET registered_at = COALESCE(registered_at, ?)",
        (now_value,),
    )
    cursor.execute(
        "UPDATE users SET language_code = COALESCE(NULLIF(language_code, ''), ?)",
        (DEFAULT_LANGUAGE,),
    )

    cursor.execute("SELECT user_id FROM users WHERE referral_code IS NULL OR referral_code = ''")
    for row in cursor.fetchall():
        referral_code = _generate_referral_code(row["user_id"])
        cursor.execute(
            "UPDATE users SET referral_code = ? WHERE user_id = ?",
            (referral_code, row["user_id"]),
        )

    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_xrocket_client_invoice_id ON orders(xrocket_client_invoice_id)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_topups_xrocket_invoice_id ON topups(xrocket_invoice_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_payment_invoices_entity "
        "ON payment_invoices(entity_type, entity_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_payment_invoices_recent "
        "ON payment_invoices(created_at_unix DESC)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_partners_bots_token ON partners_bots(bot_token)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_bot_users_unique ON partner_bot_users(bot_id, user_id)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_referral_whitelist_unique "
        "ON partner_referral_whitelist(partner_bot_id, user_id)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_bot_referrals_unique "
        "ON partner_bot_referrals(partner_bot_id, user_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_partner_owner_referrals_referrer "
        "ON partner_owner_referrals(referrer_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_partner_owner_referral_events_referrer "
        "ON partner_owner_referral_events(referrer_id)"
    )
    # The referral button is part of a newly connected franchise by default;
    # an owner can hide it from the cabinet at any time.
    migration_key = "partner_owner_referral_button_visible_v1"
    if cursor.execute("SELECT 1 FROM app_migrations WHERE migration_key = ?", (migration_key,)).fetchone() is None:
        cursor.execute(
            "UPDATE partners_bots SET franchise_hide_create = 0 "
            "WHERE lower(COALESCE(bot_username, '')) != 'souspartnersbot'"
        )
        cursor.execute(
            "INSERT INTO app_migrations(migration_key, applied_at) VALUES (?, ?)",
            (migration_key, _now_text()),
        )
    # Preserve the referral relations that existed before referrals became
    # franchise-specific.  New relations are recorded per bot below.
    cursor.execute(
        """
        INSERT OR IGNORE INTO partner_bot_referrals (
            partner_bot_id,
            user_id,
            referred_by,
            created_at
        )
        SELECT pbu.bot_id, pbu.user_id, u.referred_by, COALESCE(pbu.created_at, ?)
        FROM partner_bot_users AS pbu
        JOIN users AS u ON u.user_id = pbu.user_id
        WHERE u.referred_by IS NOT NULL
          AND u.referred_by != u.user_id
        """,
        (now_value,),
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_bot_starts_unique "
        "ON partner_bot_starts(bot_id, user_id)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_bot_utm_links_unique "
        "ON partner_bot_utm_links(bot_id, source_word)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_promo_codes_code ON promo_codes(code)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_promo_code_activations_unique "
        "ON promo_code_activations(promo_code_id, user_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sales_log_events_order_id ON sales_log_events(order_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sales_log_events_topup_id ON sales_log_events(topup_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sales_log_events_user_id ON sales_log_events(user_id)"
    )
    # API credentials must never be recoverable from the database. Existing
    # hashes remain valid for authentication, while the clear-text column is
    # scrubbed on every startup for accounts created by older builds.
    cursor.execute("UPDATE api_accounts SET api_key = NULL WHERE api_key IS NOT NULL")
    # Older builds credited a percentage of franchise deposits in addition to
    # the markup earned from purchases. Reverse that historical credit once.
    migration_key = "remove_partner_topup_earnings_v1"
    if cursor.execute("SELECT 1 FROM app_migrations WHERE migration_key = ?", (migration_key,)).fetchone() is None:
        cursor.execute(
            """
            SELECT partner_bot_id, COALESCE(SUM(amount), 0) AS paid_amount
            FROM topups
            WHERE partner_bot_id IS NOT NULL
              AND status = 'paid'
              AND partner_credit_accrued = 1
            GROUP BY partner_bot_id
            """
        )
        for row in cursor.fetchall():
            reversal = round(float(row["paid_amount"] or 0.0) * PARTNER_TOPUP_SHARE_PERCENT / 100.0, 6)
            cursor.execute(
                """UPDATE partners_bots
                   SET partner_earnings = MAX(partner_earnings - ?, partner_withdrawn), updated_at = ?
                   WHERE id = ?""",
                (reversal, _now_text(), int(row["partner_bot_id"])),
            )
        cursor.execute(
            "INSERT INTO app_migrations(migration_key, applied_at) VALUES (?, ?)",
            (migration_key, _now_text()),
        )
    conn.commit()
    conn.close()


def get_user_by_referral_code(referral_code: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE referral_code = ?", (referral_code,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def normalize_promo_code(code: str | None) -> str:
    return "".join((code or "").strip().upper().split())


def create_promo_code(
    code: str,
    reward_type: str,
    reward_value: float,
    created_by: int,
    max_activations: int | None = None,
):
    normalized_code = normalize_promo_code(code)
    normalized_reward_type = (reward_type or "").strip().lower()
    if not normalized_code:
        return None, "empty_code"
    if normalized_reward_type not in {"balance", "discount"}:
        return None, "invalid_type"

    value = round(float(reward_value or 0.0), 2)
    if value <= 0:
        return None, "invalid_value"
    if max_activations is not None and int(max_activations) <= 0:
        return None, "invalid_limit"

    now_value = _now_text()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM promo_codes WHERE code = ?", (normalized_code,))
    existing = cursor.fetchone()
    if existing is not None:
        conn.close()
        return None, "already_exists"

    cursor.execute(
        """
        INSERT INTO promo_codes (
            code,
            reward_type,
            reward_value,
            is_active,
            activations_count,
            max_activations,
            created_by,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, 1, 0, ?, ?, ?, ?)
        """,
        (
            normalized_code,
            normalized_reward_type,
            value,
            int(max_activations) if max_activations is not None else None,
            created_by,
            now_value,
            now_value,
        ),
    )
    promo_id = cursor.lastrowid
    conn.commit()
    cursor.execute("SELECT * FROM promo_codes WHERE id = ?", (promo_id,))
    row = cursor.fetchone()
    conn.close()
    return (dict(row) if row else None), None


def list_recent_promo_codes(limit: int = 10) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM promo_codes
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_promo_code(code: str):
    normalized_code = normalize_promo_code(code)
    if not normalized_code:
        return None, "invalid_code"

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM promo_codes WHERE code = ?", (normalized_code,))
    promo_row = cursor.fetchone()
    if promo_row is None:
        conn.close()
        return None, "promo_not_found"
    if int(promo_row["is_active"] or 0) != 1:
        conn.close()
        return dict(promo_row), "already_inactive"

    now_value = _now_text()
    cursor.execute(
        """
        UPDATE promo_codes
        SET
            is_active = 0,
            updated_at = ?
        WHERE id = ?
        """,
        (now_value, promo_row["id"]),
    )
    # Manual deletion also revokes waiting discount activations tied to this promo.
    cursor.execute(
        """
        UPDATE users
        SET
            active_discount_percent = 0.0,
            active_discount_promo_id = NULL,
            active_discount_code = NULL
        WHERE active_discount_promo_id = ?
        """,
        (promo_row["id"],),
    )
    conn.commit()
    cursor.execute("SELECT * FROM promo_codes WHERE id = ?", (promo_row["id"],))
    updated_row = cursor.fetchone()
    conn.close()
    return (dict(updated_row) if updated_row else dict(promo_row)), None


def activate_promo_code(user_id: int, code: str):
    normalized_code = normalize_promo_code(code)
    if not normalized_code:
        return None, "invalid_code"

    conn = get_connection()
    try:
        cursor = _begin_immediate(conn)
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user_row = cursor.fetchone()
        if user_row is None:
            return None, "user_not_found"

        cursor.execute(
            "SELECT * FROM promo_codes WHERE code = ?",
            (normalized_code,),
        )
        promo_row = cursor.fetchone()
        if promo_row is None:
            return None, "promo_not_found"
        max_activations = promo_row["max_activations"]
        max_activations = int(max_activations) if max_activations is not None else None
        if int(promo_row["is_active"] or 0) != 1:
            if max_activations is not None and int(promo_row["activations_count"] or 0) >= max_activations:
                return None, "limit_reached"
            return None, "promo_not_found"

        cursor.execute(
            """
            SELECT 1
            FROM promo_code_activations
            WHERE promo_code_id = ? AND user_id = ?
            """,
            (promo_row["id"], user_id),
        )
        if cursor.fetchone() is not None:
            return None, "already_used"

        reward_type = (promo_row["reward_type"] or "").strip().lower()
        reward_value = round(float(promo_row["reward_value"] or 0.0), 2)
        if reward_type == "discount" and float(user_row["active_discount_percent"] or 0.0) > 0:
            return None, "discount_already_active"
        if reward_type not in {"balance", "discount"}:
            return None, "invalid_type"

        now_value = _now_text()
        cursor.execute(
            """
            UPDATE promo_codes
            SET
                activations_count = activations_count + 1,
                is_active = CASE
                    WHEN max_activations IS NOT NULL AND activations_count + 1 >= max_activations THEN 0
                    ELSE is_active
                END,
                updated_at = ?
            WHERE
                id = ?
                AND is_active = 1
                AND (max_activations IS NULL OR activations_count < max_activations)
            """,
            (now_value, promo_row["id"]),
        )
        if cursor.rowcount == 0:
            cursor.execute("SELECT * FROM promo_codes WHERE id = ?", (promo_row["id"],))
            current_promo = cursor.fetchone()
            if current_promo is None:
                return None, "promo_not_found"
            current_max_activations = current_promo["max_activations"]
            current_max_activations = (
                int(current_max_activations) if current_max_activations is not None else None
            )
            if current_max_activations is not None and int(current_promo["activations_count"] or 0) >= current_max_activations:
                return None, "limit_reached"
            return None, "promo_not_found"

        if reward_type == "balance":
            cursor.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (reward_value, user_id),
            )
        else:
            cursor.execute(
                """
                UPDATE users
                SET
                    active_discount_percent = ?,
                    active_discount_promo_id = ?,
                    active_discount_code = ?
                WHERE user_id = ?
                """,
                (reward_value, promo_row["id"], promo_row["code"], user_id),
            )

        cursor.execute(
            """
            INSERT INTO promo_code_activations (
                promo_code_id,
                user_id,
                reward_type,
                reward_value,
                activated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                promo_row["id"],
                user_id,
                reward_type,
                reward_value,
                now_value,
            ),
        )
        conn.commit()

        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        updated_user = cursor.fetchone()
        return {
            "promo_code": dict(promo_row),
            "user": dict(updated_user) if updated_user else None,
        }, None
    except sqlite3.IntegrityError:
        _rollback_quietly(conn)
        return None, "already_used"
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def add_user(
    user_id: int,
    referred_by: int | None = None,
    partner_bot_id: int | None = None,
    language_code: str | None = None,
):
    normalized_language_code = normalize_language_code(language_code) if language_code else DEFAULT_LANGUAGE
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT referred_by, referral_reward_model, language_locked FROM users WHERE user_id = ?",
        (user_id,),
    )
    existing_user = cursor.fetchone()
    created = False

    if existing_user is None:
        global_referrer_id = (
            referred_by
            if partner_bot_id is None and referred_by != user_id
            else None
        )
        cursor.execute(
            """
            INSERT INTO users (
                user_id,
                balance,
                purchases_count,
                purchases_total,
                topups_total,
                referral_earnings,
                registered_at,
                referral_code,
                referred_by,
                referral_reward_model,
                language_code,
                language_locked
            )
            VALUES (?, 0.0, 0, 0.0, 0.0, 0.0, ?, ?, ?, ?, ?, 2)
            """,
            (
                user_id,
                _now_text(),
                _generate_referral_code(user_id),
                global_referrer_id,
                (
                    MARKET_PROFIT_REFERRAL_MODEL
                    if global_referrer_id is not None
                    else LEGACY_REFERRAL_MODEL
                ),
                normalized_language_code,
            ),
        )
        created = True
    else:
        if (
            partner_bot_id is None
            and existing_user["referred_by"] is None
            and referred_by not in (None, user_id)
        ):
            cursor.execute(
                """
                UPDATE users
                SET referred_by = ?, referral_reward_model = ?
                WHERE user_id = ?
                """,
                (referred_by, MARKET_PROFIT_REFERRAL_MODEL, user_id),
            )
        if language_code and int(existing_user["language_locked"] or 0) == 0:
            # One-time migration for users created before initial Telegram
            # language persistence was fixed. Value 2 means auto-initialized;
            # value 1 remains an explicit choice from the language menu.
            cursor.execute(
                "UPDATE users SET language_code = ?, language_locked = 2 WHERE user_id = ?",
                (normalized_language_code, user_id),
            )

    if (
        partner_bot_id is not None
        and referred_by not in (None, user_id)
    ):
        cursor.execute(
            """
            INSERT OR IGNORE INTO partner_bot_referrals (
                partner_bot_id,
                user_id,
                referred_by,
                created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (int(partner_bot_id), int(user_id), int(referred_by), _now_text()),
        )

    conn.commit()
    conn.close()
    return created


def sync_user_language(user_id: int, telegram_language_code: str | None):
    if not telegram_language_code:
        return

    normalized_language_code = normalize_language_code(telegram_language_code)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
    user_row = cursor.fetchone()

    if user_row is None:
        conn.close()
        add_user(user_id, language_code=normalized_language_code)
        return

    # Telegram's language is only the initial preference. Existing users keep
    # their stored choice until they explicitly change it in the language menu.
    conn.close()


def set_user_language(user_id: int, language_code: str):
    normalized_language_code = normalize_language_code(language_code)
    add_user(user_id, language_code=normalized_language_code)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE users
        SET language_code = ?, language_locked = 1
        WHERE user_id = ?
        """,
        (normalized_language_code, user_id),
    )
    conn.commit()
    conn.close()


def get_user_language(user_id: int) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT language_code FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return DEFAULT_LANGUAGE
    return normalize_language_code(row["language_code"])


def adjust_user_balance(user_id: int, amount: float, *, precision: int = 2):
    balance_precision = max(2, min(int(precision), 6))
    normalized_amount = round(float(amount or 0.0), balance_precision)
    if normalized_amount == 0:
        return None, "zero_amount"

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user_row = cursor.fetchone()

    if user_row is None:
        now_value = _now_text()
        cursor.execute(
            """
            INSERT INTO users (
                user_id,
                balance,
                purchases_count,
                purchases_total,
                topups_total,
                referral_earnings,
                registered_at,
                referral_code,
                referred_by
            )
            VALUES (?, 0.0, 0, 0.0, 0.0, 0.0, ?, ?, NULL)
            """,
            (
                user_id,
                now_value,
                _generate_referral_code(int(user_id)),
            ),
        )
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user_row = cursor.fetchone()

    current_balance = round(float(user_row["balance"] or 0.0), balance_precision)
    new_balance = round(current_balance + normalized_amount, balance_precision)
    if new_balance < 0:
        conn.close()
        return None, "insufficient_balance"

    cursor.execute(
        "UPDATE users SET balance = ? WHERE user_id = ?",
        (new_balance, user_id),
    )
    conn.commit()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    updated_user = cursor.fetchone()
    conn.close()
    return (dict(updated_user) if updated_user else None), None


def get_user_profile(user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()

    if row is None:
        conn.close()
        return None

    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,))
    referrals_count = cursor.fetchone()[0]
    profile = dict(row)
    profile["referrals_count"] = referrals_count
    conn.close()
    return profile


def get_money_referral_stats(user_id: int) -> dict:
    """Referral contest stats for 22–25 Aug 2026, 17:00 Moscow (UTC+3)."""
    start_utc = datetime(2026, 8, 22, 14, 0, 0)
    end_utc = datetime(2026, 8, 25, 14, 0, 0)
    conn = get_connection()
    rows = conn.execute(
        "SELECT referred_by, registered_at FROM users WHERE referred_by IS NOT NULL"
    ).fetchall()
    profile = conn.execute("SELECT referral_code FROM users WHERE user_id = ?", (int(user_id),)).fetchone()
    conn.close()

    counts: Counter[int] = Counter()
    for row in rows:
        registered_at = parse_datetime(row["registered_at"])
        if registered_at is None or not (start_utc <= registered_at < end_utc):
            continue
        referrer_id = int(row["referred_by"] or 0)
        if referrer_id:
            counts[referrer_id] += 1

    leaders = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    return {
        "leaders": [{"user_id": leader_id, "count": count} for leader_id, count in leaders],
        "personal_count": int(counts.get(int(user_id), 0)),
        "referral_code": str(profile["referral_code"] if profile else _generate_referral_code(int(user_id))),
    }


def get_money_referral_admin_report() -> list[dict]:
    """Detailed top-five contest data for the private owner report."""
    start_utc = datetime(2026, 8, 22, 14, 0, 0)
    end_utc = datetime(2026, 8, 25, 14, 0, 0)
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            invited.user_id AS invited_user_id,
            invited.referred_by AS referrer_id,
            invited.language_code AS invited_language_code,
            invited.registered_at,
            referrer.language_code AS referrer_language_code
        FROM users AS invited
        LEFT JOIN users AS referrer ON referrer.user_id = invited.referred_by
        WHERE invited.referred_by IS NOT NULL
        """
    ).fetchall()
    conn.close()

    referrals_by_user: dict[int, list[dict]] = {}
    referrer_languages: dict[int, str] = {}
    for row in rows:
        registered_at = parse_datetime(row["registered_at"])
        if registered_at is None or not (start_utc <= registered_at < end_utc):
            continue
        referrer_id = int(row["referrer_id"] or 0)
        invited_user_id = int(row["invited_user_id"] or 0)
        if referrer_id <= 0 or invited_user_id <= 0:
            continue
        referrer_languages[referrer_id] = normalize_language_code(row["referrer_language_code"])
        referrals_by_user.setdefault(referrer_id, []).append(
            {
                "user_id": invited_user_id,
                "language_code": normalize_language_code(row["invited_language_code"]),
                "registered_at": row["registered_at"],
                "registered_at_msk": (registered_at + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M"),
            }
        )

    leaders = sorted(
        referrals_by_user.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )[:5]
    report: list[dict] = []
    for position, (referrer_id, invited_users) in enumerate(leaders, start=1):
        invited_users.sort(
            key=lambda item: (
                parse_datetime(item["registered_at"]) or datetime.max,
                int(item["user_id"]),
            )
        )
        report.append(
            {
                "position": position,
                "user_id": referrer_id,
                "language_code": referrer_languages.get(referrer_id, DEFAULT_LANGUAGE),
                "count": len(invited_users),
                "invited_users": invited_users,
            }
        )
    return report


def get_api_account(user_id: int):
    conn = get_connection()
    row = conn.execute(
        """
        SELECT a.*, COALESCE(u.balance, 0.0) AS api_balance
        FROM api_accounts a LEFT JOIN users u ON u.user_id = a.api_user_id
        WHERE a.user_id = ?
        """,
        (int(user_id),),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    result = dict(row)
    result["balance"] = float(result.pop("api_balance", 0.0) or 0.0)
    return result


def submit_api_application(user_id: int):
    add_user(int(user_id))
    api_user_id = -abs(int(user_id))
    add_user(api_user_id)
    now_value = _now_text()
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO api_accounts (user_id,api_user_id,status,balance,requested_at,created_at,updated_at)
        VALUES (?, ?, 'pending', 0.0, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            api_user_id = COALESCE(api_accounts.api_user_id, excluded.api_user_id),
            status = CASE WHEN api_accounts.status = 'approved' THEN 'approved' ELSE 'pending' END,
            requested_at = CASE WHEN api_accounts.status = 'approved' THEN api_accounts.requested_at ELSE excluded.requested_at END,
            updated_at = excluded.updated_at
        """,
        (int(user_id), api_user_id, now_value, now_value, now_value),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM api_accounts WHERE user_id = ?", (int(user_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_pending_api_applications(limit: int = 100):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM api_accounts WHERE status = 'pending' ORDER BY requested_at ASC LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def review_api_application(user_id: int, approved: bool, admin_id: int):
    now_value = _now_text()
    conn = get_connection()
    cursor = conn.execute(
        """
        UPDATE api_accounts
        SET status = ?, reviewed_at = ?, reviewed_by = ?, updated_at = ?
        WHERE user_id = ? AND status = 'pending'
        """,
        ("approved" if approved else "rejected", now_value, int(admin_id), now_value, int(user_id)),
    )
    conn.commit()
    updated = cursor.rowcount == 1
    conn.close()
    return updated


def generate_api_key(user_id: int):
    account = get_api_account(int(user_id))
    if account is None or str(account.get("status")) != "approved":
        return None
    raw_key = "sous_" + secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    now_value = _now_text()
    conn = get_connection()
    conn.execute(
        # Store only a hash. The raw key is returned once to the owner and is
        # never persisted or shown again by the bot.
        "UPDATE api_accounts SET api_key = NULL, api_key_hash = ?, api_key_prefix = ?, updated_at = ? WHERE user_id = ? AND status = 'approved'",
        (key_hash, raw_key[:13], now_value, int(user_id)),
    )
    conn.commit()
    conn.close()
    return raw_key


def get_api_account_by_key(raw_key: str):
    key_hash = hashlib.sha256(str(raw_key or "").encode("utf-8")).hexdigest()
    conn = get_connection()
    row = conn.execute(
        """
        SELECT a.*, COALESCE(u.balance, 0.0) AS api_balance
        FROM api_accounts a LEFT JOIN users u ON u.user_id = a.api_user_id
        WHERE a.api_key_hash = ? AND a.status = 'approved'
        """,
        (key_hash,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    result = dict(row)
    result["balance"] = float(result.pop("api_balance", 0.0) or 0.0)
    return result


def _hash_web_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260_000).hex()


def create_web_account(email: str, username: str, password: str):
    normalized_email = str(email or "").strip().casefold()
    clean_username = str(username or "").strip()
    salt = secrets.token_bytes(16)
    now_value = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO web_accounts
                (email, username, password_salt, password_hash, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_email,
                clean_username,
                salt.hex(),
                _hash_web_password(password, salt),
                now_value,
                now_value,
            ),
        )
        account_id = int(cursor.lastrowid)
        user_id = -(2_000_000_000 + account_id)
        conn.execute("UPDATE web_accounts SET user_id = ? WHERE id = ?", (user_id, account_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        return None, "email_exists"
    conn.close()
    add_user(user_id)
    return get_web_account_by_id(account_id), None


def get_web_account_by_id(account_id: int):
    conn = get_connection()
    row = conn.execute("SELECT * FROM web_accounts WHERE id = ?", (int(account_id),)).fetchone()
    conn.close()
    return dict(row) if row else None


def authenticate_web_account(email: str, password: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM web_accounts WHERE email = ? COLLATE NOCASE",
        (str(email or "").strip().casefold(),),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    account = dict(row)
    try:
        salt = bytes.fromhex(str(account["password_salt"]))
    except ValueError:
        return None
    supplied_hash = _hash_web_password(str(password or ""), salt)
    return account if secrets.compare_digest(supplied_hash, str(account["password_hash"])) else None


def create_web_session(account_id: int, lifetime_days: int = 30) -> str:
    raw_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = datetime.now()
    conn = get_connection()
    conn.execute("DELETE FROM web_sessions WHERE expires_at <= ?", (now.isoformat(timespec="seconds"),))
    conn.execute(
        "INSERT INTO web_sessions (account_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (
            int(account_id),
            token_hash,
            now.isoformat(timespec="seconds"),
            (now + timedelta(days=max(1, int(lifetime_days)))).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    return raw_token


def get_web_account_by_session(raw_token: str):
    if not raw_token:
        return None
    token_hash = hashlib.sha256(str(raw_token).encode("utf-8")).hexdigest()
    now_value = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    row = conn.execute(
        """
        SELECT a.* FROM web_sessions s
        JOIN web_accounts a ON a.id = s.account_id
        WHERE s.token_hash = ? AND s.expires_at > ?
        """,
        (token_hash, now_value),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_web_session(raw_token: str) -> bool:
    if not raw_token:
        return False
    token_hash = hashlib.sha256(str(raw_token).encode("utf-8")).hexdigest()
    conn = get_connection()
    cursor = conn.execute("DELETE FROM web_sessions WHERE token_hash = ?", (token_hash,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def complete_web_login_token(login_hash: str, user_id: int, username: str = "") -> bool:
    """Complete a one-time web login when the optional login-token table exists."""
    if not login_hash:
        return False
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE web_login_tokens SET user_id = ?, username = ?, used_at = ? "
            "WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?",
            (int(user_id), str(username or ""), _now_text(), str(login_hash), _now_text()),
        )
        conn.commit()
        return cursor.rowcount > 0
    except sqlite3.Error:
        conn.rollback()
        return False
    finally:
        conn.close()


def create_web_login_token(user_id: int | None = None, lifetime_seconds: int = 300) -> str | None:
    """Create a one-time web-login token when the optional table is available."""
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    now = datetime.now()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO web_login_tokens (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token_hash, int(user_id) if user_id else None, now.isoformat(timespec="seconds"), (now + timedelta(seconds=lifetime_seconds)).isoformat(timespec="seconds")),
        )
        conn.commit()
        return raw_token
    except sqlite3.Error:
        conn.rollback()
        return None
    finally:
        conn.close()


def consume_web_login_token(login_hash: str):
    """Consume a web login token, returning the linked web account if present."""
    if not login_hash:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT a.* FROM web_login_tokens t JOIN web_accounts a ON a.id = t.user_id "
            "WHERE t.token_hash = ? AND t.used_at IS NULL AND t.expires_at > ?",
            (str(login_hash), _now_text()),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE web_login_tokens SET used_at = ? WHERE token_hash = ?", (_now_text(), str(login_hash)))
        conn.commit()
        return dict(row)
    except sqlite3.Error:
        conn.rollback()
        return None
    finally:
        conn.close()




def _find_partner_owner_referrer(cursor: sqlite3.Cursor, referred_owner_id: int) -> int | None:
    """Resolve the user whose SousPartners deep link invited this owner."""
    owner_id = int(referred_owner_id)
    cursor.execute("SELECT referred_by FROM users WHERE user_id = ?", (owner_id,))
    row = cursor.fetchone()
    if row is not None and row["referred_by"] not in (None, owner_id):
        return int(row["referred_by"])

    # SousPartnersBot is stored as a partner bot too.  Its deep-link starts
    # are recorded in the per-bot referral table by the normal /start flow.
    cursor.execute(
        """
        SELECT pbr.referred_by
        FROM partner_bot_referrals AS pbr
        JOIN partners_bots AS pb ON pb.id = pbr.partner_bot_id
        WHERE pbr.user_id = ?
          AND lower(COALESCE(pb.bot_username, '')) = 'souspartnersbot'
          AND pbr.referred_by != ?
        ORDER BY pbr.id ASC
        LIMIT 1
        """,
        (owner_id, owner_id),
    )
    row = cursor.fetchone()
    return int(row["referred_by"]) if row is not None else None


def _register_partner_owner_referral_cursor(
    cursor: sqlite3.Cursor,
    referred_owner_id: int,
) -> int | None:
    referrer_id = _find_partner_owner_referrer(cursor, referred_owner_id)
    if referrer_id is None or referrer_id == int(referred_owner_id):
        return None
    cursor.execute(
        """
        INSERT OR IGNORE INTO partner_owner_referrals
            (referrer_id, referred_owner_id, created_at)
        VALUES (?, ?, ?)
        """,
        (referrer_id, int(referred_owner_id), _now_text()),
    )
    return referrer_id


def register_partner_owner_referral(referred_owner_id: int) -> int | None:
    """Persist an owner's inviter when they connect a partner bot."""
    conn = get_connection()
    try:
        cursor = _begin_immediate(conn)
        referrer_id = _register_partner_owner_referral_cursor(cursor, referred_owner_id)
        conn.commit()
        return referrer_id
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def upsert_partner_bot(
    owner_id: int,
    bot_token: str,
    bot_username: str,
    margin_percentage: int = DEFAULT_PARTNER_MARGIN_PERCENT,
    shop_markup_percentage: int | None = None,
    is_active: bool = True,
):
    conn = get_connection()
    cursor = conn.cursor()
    now_value = _now_text()
    cursor.execute("BEGIN IMMEDIATE")
    cursor.execute("SELECT id FROM partners_bots WHERE bot_token = ?", (bot_token,))
    existing = cursor.fetchone()

    if is_active:
        existing_id = int(existing["id"]) if existing is not None else 0
        cursor.execute(
            "SELECT COUNT(*) FROM partners_bots "
            "WHERE owner_id = ? AND is_active = 1 AND id != ?",
            (int(owner_id), existing_id),
        )
        if int(cursor.fetchone()[0] or 0) >= 2:
            conn.rollback()
            conn.close()
            raise ValueError("Во франшизе можно создать не больше 2 ботов.")

    if existing is None:
        cursor.execute(
            """
            INSERT INTO partners_bots (
                owner_id,
                bot_token,
                bot_username,
                shop_markup_percentage,
                margin_percentage,
                is_active,
                partner_earnings,
                referral_enabled,
                referral_percent,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0.0, 1, ?, ?, ?)
            """,
            (
                owner_id,
                bot_token,
                bot_username,
                shop_markup_percentage,
                margin_percentage,
                1 if is_active else 0,
                DEFAULT_PARTNER_REFERRAL_PERCENT,
                now_value,
                now_value,
            ),
        )
        partner_id = cursor.lastrowid
    else:
        partner_id = existing["id"]
        cursor.execute(
            """
            UPDATE partners_bots
            SET
                owner_id = ?,
                bot_username = ?,
                shop_markup_percentage = ?,
                margin_percentage = ?,
                is_active = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                owner_id,
                bot_username,
                shop_markup_percentage,
                margin_percentage,
                1 if is_active else 0,
                now_value,
                partner_id,
            ),
        )

    # Capture the owner's SousPartners inviter at connection time.  The
    # relation is immutable even if this bot is later paused or reconfigured.
    _register_partner_owner_referral_cursor(cursor, int(owner_id))

    conn.commit()
    cursor.execute("SELECT * FROM partners_bots WHERE id = ?", (partner_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_partner_bot_by_id(partner_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM partners_bots WHERE id = ?", (partner_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def update_partner_franchise_setting(partner_id: int, field: str, value) -> bool:
    allowed = {
        "franchise_news_url",
        "franchise_support_url",
        "franchise_usage_url",
        "franchise_privacy_url",
        "franchise_info_brand",
        "franchise_hide_create",
        "subscription_enabled",
        "subscription_channel_id",
        "subscription_channel_url",
        "referral_percent",
        "referral_enabled",
        "is_active",
    }
    if field not in allowed:
        return False
    conn = get_connection()
    cursor = conn.execute(
        f"UPDATE partners_bots SET {field} = ?, updated_at = ? WHERE id = ?",
        (value, _now_text(), int(partner_id)),
    )
    conn.commit()
    changed = cursor.rowcount > 0
    conn.close()
    return changed


def get_partner_bot_by_token(bot_token: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM partners_bots WHERE bot_token = ?", (bot_token,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_partner_bot_by_username(bot_username: str):
    normalized_username = (bot_username or "").strip().lstrip("@")
    if not normalized_username:
        return None

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM partners_bots WHERE lower(bot_username) = lower(?)",
        (normalized_username,),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_active_partner_bots():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM partners_bots
        WHERE is_active = 1
        ORDER BY id ASC
        """
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_partner_bots_by_owner(owner_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM partners_bots
        WHERE owner_id = ?
        ORDER BY id DESC
        """,
        (owner_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_partner_bot_margin(partner_id: int, owner_id: int, margin_percentage: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE partners_bots
        SET
            margin_percentage = ?,
            updated_at = ?
        WHERE id = ? AND owner_id = ?
        """,
        (margin_percentage, _now_text(), partner_id, owner_id),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def update_partner_bot_markup(partner_id: int, owner_id: int, kind: str, margin_percentage: int):
    column_by_kind = {
        "goods": "goods_markup_percentage",
        "proxy": "proxy_markup_percentage",
        "sms": "sms_markup_percentage",
    }
    column_name = column_by_kind.get(str(kind or "").strip().lower())
    if column_name is None:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE partners_bots SET {column_name} = ?, updated_at = ? WHERE id = ? AND owner_id = ?",
        (max(0, min(int(margin_percentage), 500)), _now_text(), int(partner_id), int(owner_id)),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def deactivate_partner_bot(partner_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE partners_bots
        SET
            is_active = 0,
            updated_at = ?
        WHERE id = ?
        """,
        (_now_text(), partner_id),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def register_partner_bot_user(bot_id: int, user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO partner_bot_users (
            bot_id,
            user_id,
            created_at
        )
        VALUES (?, ?, ?)
        """,
        (bot_id, user_id, _now_text()),
    )
    conn.commit()
    conn.close()


def count_partner_bot_users(bot_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM partner_bot_users WHERE bot_id = ?", (bot_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count


def list_partner_bot_user_ids(bot_id: int) -> list[int]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT user_id
        FROM partner_bot_users
        WHERE bot_id = ?
        ORDER BY id ASC
        """,
        (bot_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [int(row["user_id"]) for row in rows]


def create_partner_payout_request(partner_bot_id: int, owner_id: int, amount: float, asset: str, destination: str):
    amount = round(float(amount or 0), 2)
    destination = str(destination or '').strip()
    if amount < PARTNER_MIN_WITHDRAW_AMOUNT or not destination:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT partner_earnings, partner_withdrawn FROM partners_bots WHERE id = ? AND owner_id = ?", (partner_bot_id, owner_id))
    row = cursor.fetchone()
    if row is None or amount > max(float(row['partner_earnings'] or 0) - float(row['partner_withdrawn'] or 0), 0):
        conn.close()
        return None
    now_value = _now_text()
    cursor.execute("INSERT INTO partner_payout_requests (partner_bot_id, owner_id, amount, asset, destination, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (partner_bot_id, owner_id, amount, (asset or 'USDT').upper(), destination, now_value, now_value))
    request_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return int(request_id)


def add_partner_earnings(partner_id: int, amount: float):
    if amount <= 0:
        return

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE partners_bots
        SET
            partner_earnings = partner_earnings + ?,
            updated_at = ?
        WHERE id = ?
        """,
        (amount, _now_text(), partner_id),
    )
    conn.commit()
    conn.close()


def _accrue_partner_owner_referral_cursor(
    cursor: sqlite3.Cursor,
    partner_bot_id: int,
    source_type: str,
    source_id: str | int,
    partner_amount: float,
) -> float:
    """Credit the inviter of a franchise owner exactly once."""
    normalized_amount = round(float(partner_amount or 0.0), 6)
    if normalized_amount <= 0:
        return 0.0
    cursor.execute(
        "SELECT owner_id FROM partners_bots WHERE id = ?",
        (int(partner_bot_id),),
    )
    owner_row = cursor.fetchone()
    if owner_row is None:
        return 0.0
    owner_id = int(owner_row["owner_id"])
    cursor.execute(
        "SELECT referrer_id FROM partner_owner_referrals WHERE referred_owner_id = ?",
        (owner_id,),
    )
    relation = cursor.fetchone()
    if relation is None or int(relation["referrer_id"] or 0) == owner_id:
        return 0.0
    referrer_id = int(relation["referrer_id"])
    reward = round(normalized_amount * PARTNER_OWNER_REFERRAL_PERCENT / 100.0, 6)
    if reward <= 0:
        return 0.0
    cursor.execute(
        """
        INSERT OR IGNORE INTO partner_owner_referral_events
            (referrer_id, referred_owner_id, partner_bot_id, source_type,
             source_id, amount, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            referrer_id,
            owner_id,
            int(partner_bot_id),
            str(source_type),
            str(source_id),
            reward,
            _now_text(),
        ),
    )
    if cursor.rowcount != 1:
        return 0.0
    cursor.execute(
        """
        UPDATE users
        SET balance = balance + ?,
            partner_referral_earnings = partner_referral_earnings + ?
        WHERE user_id = ?
        """,
        (reward, reward, referrer_id),
    )
    return reward if cursor.rowcount == 1 else 0.0


def accrue_partner_earning_once(partner_id: int, source_type: str, source_id: str | int, amount: float) -> bool:
    """Accrue one purchase profit exactly once, including recovery retries."""
    normalized_amount = round(float(amount or 0.0), 6)
    if normalized_amount <= 0:
        return False
    conn = get_connection()
    try:
        cursor = _begin_immediate(conn)
        cursor.execute(
            """INSERT OR IGNORE INTO partner_earning_events
               (partner_bot_id, source_type, source_id, amount, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (int(partner_id), str(source_type), str(source_id), normalized_amount, _now_text()),
        )
        if cursor.rowcount != 1:
            conn.commit()
            return False
        cursor.execute(
            """UPDATE partners_bots
               SET partner_earnings = partner_earnings + ?, updated_at = ?
               WHERE id = ?""",
            (normalized_amount, _now_text(), int(partner_id)),
        )
        _accrue_partner_owner_referral_cursor(
            cursor,
            int(partner_id),
            source_type,
            source_id,
            normalized_amount,
        )
        conn.commit()
        return cursor.rowcount == 1
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def get_partner_program_stats() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM partners_bots WHERE is_active = 1")
    total_bots = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM partner_bot_users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(partner_earnings), 0) FROM partners_bots WHERE is_active = 1")
    total_earned = float(cursor.fetchone()[0] or 0)
    conn.close()
    return {
        "total_bots": total_bots,
        "total_users": total_users,
        "total_earned_rub": total_earned,
    }


def get_all_franchises_stats() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM partners_bots WHERE is_active = 1")
    total_bots = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM partner_bot_users
        WHERE bot_id IN (
            SELECT id FROM partners_bots WHERE is_active = 1
        )
        """
    )
    total_users = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM orders
        WHERE partner_bot_id IN (
            SELECT id FROM partners_bots WHERE is_active = 1
        ) AND status = 'delivered'
        """
    )
    delivered_orders = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT COALESCE(SUM(total_price), 0)
        FROM orders
        WHERE partner_bot_id IN (
            SELECT id FROM partners_bots WHERE is_active = 1
        ) AND status = 'delivered'
        """
    )
    delivered_revenue = float(cursor.fetchone()[0] or 0.0)
    cursor.execute(
        """
        SELECT COALESCE(SUM(partner_profit_amount), 0)
        FROM orders
        WHERE partner_bot_id IN (
            SELECT id FROM partners_bots WHERE is_active = 1
        ) AND status = 'delivered'
        """
    )
    total_profit = float(cursor.fetchone()[0] or 0.0)
    cursor.execute(
        """
        SELECT
            pb.id,
            pb.bot_username,
            COUNT(DISTINCT pbu.user_id) AS users_count,
            COALESCE(pb.partner_earnings, 0) AS partner_earnings
        FROM partners_bots pb
        LEFT JOIN partner_bot_users pbu ON pbu.bot_id = pb.id
        WHERE pb.is_active = 1
        GROUP BY pb.id, pb.bot_username, pb.partner_earnings
        ORDER BY users_count DESC, partner_earnings DESC, pb.bot_username COLLATE NOCASE ASC
        """
    )
    bots = []
    for row in cursor.fetchall():
        bot_username = str(row["bot_username"] or "").strip()
        if bot_username and not bot_username.startswith("@"):
            bot_username = f"@{bot_username}"
        bots.append(
            {
                "id": int(row["id"]),
                "bot_username": bot_username or f"ID {int(row['id'])}",
                "users_count": int(row["users_count"] or 0),
                "partner_earnings": round(float(row["partner_earnings"] or 0.0), 2),
            }
        )
    conn.close()
    return {
        "total_bots": total_bots,
        "total_users": total_users,
        "delivered_orders": delivered_orders,
        "delivered_revenue": round(delivered_revenue, 2),
        "profit": round(total_profit, 2),
        "bots": bots,
    }


def get_partner_owner_summary(owner_id: int) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM partners_bots WHERE owner_id = ? AND is_active = 1", (owner_id,))
    total_bots = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM partner_bot_users
        WHERE bot_id IN (
            SELECT id FROM partners_bots WHERE owner_id = ? AND is_active = 1
        )
        """,
        (owner_id,),
    )
    total_users = cursor.fetchone()[0]
    cursor.execute(
        "SELECT COALESCE(SUM(partner_earnings), 0) FROM partners_bots WHERE owner_id = ?",
        (owner_id,),
    )
    total_earned = float(cursor.fetchone()[0] or 0)
    cursor.execute(
        "SELECT COALESCE(SUM(partner_withdrawn), 0) FROM partners_bots WHERE owner_id = ?",
        (owner_id,),
    )
    total_withdrawn = float(cursor.fetchone()[0] or 0)
    cursor.execute(
        "SELECT COALESCE(partner_referral_earnings, 0) FROM users WHERE user_id = ?",
        (owner_id,),
    )
    partner_referral_row = cursor.fetchone()
    partner_referral_earned = float(partner_referral_row[0] or 0.0) if partner_referral_row else 0.0
    cursor.execute(
        "SELECT COALESCE(partner_referral_withdrawn, 0) FROM users WHERE user_id = ?",
        (owner_id,),
    )
    partner_referral_withdrawn_row = cursor.fetchone()
    partner_referral_withdrawn = (
        float(partner_referral_withdrawn_row[0] or 0.0)
        if partner_referral_withdrawn_row
        else 0.0
    )
    cursor.execute(
        "SELECT COUNT(*) FROM partner_owner_referrals WHERE referrer_id = ?",
        (owner_id,),
    )
    referred_partners = int(cursor.fetchone()[0] or 0)
    conn.close()
    total_earned += partner_referral_earned
    return {
        "total_bots": total_bots,
        "total_users": total_users,
        "total_earned": total_earned,
        "total_withdrawn": total_withdrawn + partner_referral_withdrawn,
        "partner_referral_earned": partner_referral_earned,
        "partner_referral_withdrawn": partner_referral_withdrawn,
        "referred_partners": referred_partners,
        "available_balance": round(
            max(total_earned - total_withdrawn - partner_referral_withdrawn, 0.0),
            2,
        ),
    }


def get_partner_bot_stats(bot_id: int) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM partner_bot_users WHERE bot_id = ?", (bot_id,))
    total_users = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM orders
        WHERE partner_bot_id = ? AND status = 'delivered'
        """,
        (bot_id,),
    )
    delivered_orders = cursor.fetchone()[0]
    cursor.execute(
        """
        SELECT COALESCE(SUM(total_price), 0)
        FROM orders
        WHERE partner_bot_id = ? AND status = 'delivered'
        """,
        (bot_id,),
    )
    delivered_revenue = float(cursor.fetchone()[0] or 0.0)
    cursor.execute(
        """
        SELECT COALESCE(SUM(partner_profit_amount), 0)
        FROM orders
        WHERE partner_bot_id = ? AND status = 'delivered'
        """,
        (bot_id,),
    )
    profit = float(cursor.fetchone()[0] or 0.0)
    conn.close()
    return {
        "total_users": total_users,
        "delivered_orders": delivered_orders,
        "delivered_revenue": round(delivered_revenue, 2),
        "profit": round(profit, 2),
    }


def get_partner_top_buyers(bot_id: int, limit: int = 10) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            user_id,
            COUNT(*) AS orders_count,
            COALESCE(SUM(total_price), 0) AS total_spent
        FROM orders
        WHERE partner_bot_id = ? AND status = 'delivered'
        GROUP BY user_id
        ORDER BY total_spent DESC, orders_count DESC, user_id ASC
        LIMIT ?
        """,
        (bot_id, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_partner_bot_available_balance(partner_bot: dict) -> float:
    earned = float(partner_bot.get("partner_earnings") or 0.0)
    withdrawn = float(partner_bot.get("partner_withdrawn") or 0.0)
    return round(max(earned - withdrawn, 0.0), 2)


def update_partner_subscription_settings(
    partner_id: int,
    owner_id: int,
    enabled: bool,
    channel_id: str | None = None,
    channel_url: str | None = None,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE partners_bots
        SET
            subscription_enabled = ?,
            subscription_channel_id = ?,
            subscription_channel_url = ?,
            updated_at = ?
        WHERE id = ? AND owner_id = ?
        """,
        (
            1 if enabled else 0,
            channel_id.strip() if channel_id else None,
            channel_url.strip() if channel_url else None,
            _now_text(),
            partner_id,
            owner_id,
        ),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def get_main_bot_settings() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM main_bot_settings WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {
        "id": 1,
        "subscription_enabled": 0,
        "subscription_channel_id": None,
        "subscription_channel_url": None,
        "updated_at": _now_text(),
    }


def update_main_bot_subscription_settings(
    enabled: bool,
    channel_id: str | None = None,
    channel_url: str | None = None,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE main_bot_settings
        SET
            subscription_enabled = ?,
            subscription_channel_id = ?,
            subscription_channel_url = ?,
            updated_at = ?
        WHERE id = 1
        """,
        (
            1 if enabled else 0,
            channel_id.strip() if channel_id else None,
            channel_url.strip() if channel_url else None,
            _now_text(),
        ),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def update_partner_referral_settings(
    partner_id: int,
    owner_id: int,
    enabled: bool,
    referral_percent: float,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE partners_bots
        SET
            referral_enabled = ?,
            referral_percent = ?,
            updated_at = ?
        WHERE id = ? AND owner_id = ?
        """,
        (
            1 if enabled else 0,
            _normalize_partner_referral_percent(referral_percent),
            _now_text(),
            partner_id,
            owner_id,
        ),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated


def list_partner_referral_whitelist(partner_bot_id: int, limit: int = 20) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM partner_referral_whitelist
        WHERE partner_bot_id = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (partner_bot_id, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def upsert_partner_referral_whitelist(
    partner_bot_id: int,
    user_id: int,
    referral_percent: float,
) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    now_value = _now_text()
    cursor.execute(
        """
        INSERT INTO partner_referral_whitelist (
            partner_bot_id,
            user_id,
            referral_percent,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(partner_bot_id, user_id)
        DO UPDATE SET
            referral_percent = excluded.referral_percent,
            updated_at = excluded.updated_at
        """,
        (
            partner_bot_id,
            user_id,
            _normalize_partner_referral_percent(referral_percent),
            now_value,
            now_value,
        ),
    )
    conn.commit()
    conn.close()
    return True


def remove_partner_referral_whitelist(partner_bot_id: int, user_id: int) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        DELETE FROM partner_referral_whitelist
        WHERE partner_bot_id = ? AND user_id = ?
        """,
        (partner_bot_id, user_id),
    )
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return deleted


def _get_partner_referral_percent_for_referrer(
    cursor,
    partner_bot_id: int,
    referrer_user_id: int,
    default_percent: float,
) -> float:
    cursor.execute(
        """
        SELECT referral_percent
        FROM partner_referral_whitelist
        WHERE partner_bot_id = ? AND user_id = ?
        """,
        (partner_bot_id, referrer_user_id),
    )
    row = cursor.fetchone()
    if row is None:
        return max(float(default_percent or 0.0), 0.0)
    return max(float(row["referral_percent"] or 0.0), 0.0)


def record_partner_withdrawal(
    partner_bot_id: int,
    owner_id: int,
    amount: float,
    asset: str,
    xrocket_transfer_id: str,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE partners_bots
        SET
            partner_withdrawn = partner_withdrawn + ?,
            updated_at = ?
        WHERE id = ? AND owner_id = ?
        """,
        (amount, _now_text(), partner_bot_id, owner_id),
    )
    if cursor.rowcount <= 0:
        conn.rollback()
        conn.close()
        return False

    cursor.execute(
        """
        INSERT INTO partner_withdrawals (
            partner_bot_id,
            owner_id,
            amount,
            asset,
            xrocket_transfer_id,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (partner_bot_id, owner_id, amount, asset, xrocket_transfer_id, _now_text()),
    )
    conn.commit()
    conn.close()
    return True


def record_partner_referral_withdrawal(
    owner_id: int,
    amount: float,
    asset: str,
    xrocket_transfer_id: str,
) -> bool:
    amount = round(float(amount or 0.0), 2)
    if amount <= 0:
        return False
    conn = get_connection()
    try:
        cursor = _begin_immediate(conn)
        cursor.execute(
            """
            UPDATE users
            SET partner_referral_withdrawn = partner_referral_withdrawn + ?
            WHERE user_id = ?
              AND partner_referral_earnings - partner_referral_withdrawn >= ?
            """,
            (amount, int(owner_id), amount),
        )
        if cursor.rowcount <= 0:
            _rollback_quietly(conn)
            return False
        cursor.execute(
            """
            INSERT INTO partner_referral_withdrawals
                (owner_id, amount, asset, xrocket_transfer_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(owner_id), amount, (asset or "USDT").upper(), xrocket_transfer_id, _now_text()),
        )
        conn.commit()
        return True
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def save_user_start_data(user_id: int, start_param: str | None, utm_data: dict | None = None):
    if not start_param and not utm_data:
        return

    utm_data = utm_data or {}
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE users
        SET
            start_param = COALESCE(NULLIF(start_param, ''), ?),
            utm_source = COALESCE(NULLIF(utm_source, ''), ?),
            utm_medium = COALESCE(NULLIF(utm_medium, ''), ?),
            utm_campaign = COALESCE(NULLIF(utm_campaign, ''), ?),
            utm_content = COALESCE(NULLIF(utm_content, ''), ?),
            utm_term = COALESCE(NULLIF(utm_term, ''), ?)
        WHERE user_id = ?
        """,
        (
            start_param,
            utm_data.get("utm_source"),
            utm_data.get("utm_medium"),
            utm_data.get("utm_campaign"),
            utm_data.get("utm_content"),
            utm_data.get("utm_term"),
            user_id,
        ),
    )
    conn.commit()
    conn.close()


def save_partner_bot_start_data(
    bot_id: int,
    user_id: int,
    start_param: str | None,
    utm_data: dict | None = None,
):
    if bot_id <= 0:
        return

    utm_data = utm_data or {}
    now_value = _now_text()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO partner_bot_starts (
            bot_id,
            user_id,
            start_param,
            utm_source,
            utm_medium,
            utm_campaign,
            utm_content,
            utm_term,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(bot_id, user_id)
        DO UPDATE SET
            start_param = COALESCE(NULLIF(partner_bot_starts.start_param, ''), excluded.start_param),
            utm_source = COALESCE(NULLIF(partner_bot_starts.utm_source, ''), excluded.utm_source),
            utm_medium = COALESCE(NULLIF(partner_bot_starts.utm_medium, ''), excluded.utm_medium),
            utm_campaign = COALESCE(NULLIF(partner_bot_starts.utm_campaign, ''), excluded.utm_campaign),
            utm_content = COALESCE(NULLIF(partner_bot_starts.utm_content, ''), excluded.utm_content),
            utm_term = COALESCE(NULLIF(partner_bot_starts.utm_term, ''), excluded.utm_term),
            updated_at = excluded.updated_at
        """,
        (
            bot_id,
            user_id,
            start_param,
            utm_data.get("utm_source"),
            utm_data.get("utm_medium"),
            utm_data.get("utm_campaign"),
            utm_data.get("utm_content"),
            utm_data.get("utm_term"),
            now_value,
            now_value,
        ),
    )
    conn.commit()
    conn.close()


def _pop_user_active_discount(cursor, user_id: int, quantity: int, unit_price: float, total_price: float) -> dict:
    cursor.execute(
        """
        SELECT active_discount_percent, active_discount_promo_id, active_discount_code, topups_total
        FROM users
        WHERE user_id = ?
        """,
        (user_id,),
    )
    user_row = cursor.fetchone()
    if user_row is None:
        return {
            "unit_price": round(float(unit_price or 0.0), 4),
            "total_price": round(float(total_price or 0.0), 2),
            "original_unit_price": None,
            "original_total_price": None,
            "promo_discount_percent": 0.0,
            "promo_code_id": None,
            "promo_code_text": None,
            "discount_multiplier": 1.0,
        }

    topups_total = float(user_row["topups_total"] or 0.0)
    loyalty_discount = 7.0 if topups_total >= 1000 else 5.0 if topups_total >= 500 else 3.0 if topups_total >= 250 else 0.0
    promo_discount = max(min(float(user_row["active_discount_percent"] or 0.0), 100.0), 0.0)
    discount_percent = max(loyalty_discount, promo_discount)
    normalized_unit_price = round(float(unit_price or 0.0), 4)
    normalized_total_price = round(float(total_price or 0.0), 2)
    if discount_percent <= 0:
        return {
            "unit_price": normalized_unit_price,
            "total_price": normalized_total_price,
            "original_unit_price": None,
            "original_total_price": None,
            "promo_discount_percent": 0.0,
            "promo_code_id": None,
            "promo_code_text": None,
            "discount_multiplier": 1.0,
        }

    discount_multiplier = max(0.0, 1 - (discount_percent / 100))
    discounted_total_price = round(normalized_total_price * discount_multiplier, 2)
    discounted_unit_price = (
        round(discounted_total_price / max(quantity, 1), 4)
        if quantity > 0
        else round(normalized_unit_price * discount_multiplier, 4)
    )

    # A loyalty discount is permanent. A one-time promo is consumed only when
    # it actually provides the applied (equal or larger) discount.
    if promo_discount >= loyalty_discount and promo_discount > 0:
        cursor.execute(
            """UPDATE users SET active_discount_percent=0,
               active_discount_promo_id=NULL,active_discount_code=NULL WHERE user_id=?""",
            (user_id,),
        )
    return {
        "unit_price": discounted_unit_price,
        "total_price": discounted_total_price,
        "original_unit_price": normalized_unit_price,
        "original_total_price": normalized_total_price,
        "promo_discount_percent": discount_percent,
        "promo_code_id": user_row["active_discount_promo_id"] if promo_discount >= loyalty_discount else None,
        "promo_code_text": user_row["active_discount_code"] if promo_discount >= loyalty_discount else "LOYALTY",
        "discount_multiplier": discount_multiplier,
    }


def get_loyalty_discount_percent(user_id: int) -> float:
    """Permanent discount based on the user's cumulative paid top-ups."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT topups_total FROM users WHERE user_id=?", (int(user_id),)).fetchone()
        total = float(row["topups_total"] or 0.0) if row else 0.0
    finally:
        conn.close()
    return 7.0 if total >= 1000 else 5.0 if total >= 500 else 3.0 if total >= 250 else 0.0


def get_loyalty_discount_info(user_id: int) -> dict:
    conn = get_connection()
    try:
        row = conn.execute("SELECT topups_total FROM users WHERE user_id=?", (int(user_id),)).fetchone()
        total = float(row["topups_total"] or 0.0) if row else 0.0
    finally:
        conn.close()
    percent = 7.0 if total >= 1000 else 5.0 if total >= 500 else 3.0 if total >= 250 else 0.0
    return {"topups_total": total, "discount_percent": percent}


def create_order(
    user_id: int,
    proxy_kind: str,
    category_id: int,
    category_name: str,
    item_id: int,
    protocol: str,
    quantity: int,
    unit_price: float,
    total_price: float,
    product_title: str | None = None,
    partner_bot_id: int | None = None,
    purchase_unit_price: float = 0.0,
    partner_margin_percentage: int = 0,
    partner_profit_amount: float = 0.0,
):
    conn = get_connection()
    cursor = conn.cursor()
    now_value = _now_text()
    discount_data = _pop_user_active_discount(cursor, user_id, quantity, unit_price, total_price)
    applied_partner_profit_amount = round(
        float(partner_profit_amount or 0.0) * float(discount_data["discount_multiplier"]),
        4,
    )
    cursor.execute(
        """
        INSERT INTO orders (
            user_id,
            proxy_kind,
            category_id,
            category_name,
            item_id,
            protocol,
            product_title,
            partner_bot_id,
            purchase_unit_price,
            partner_margin_percentage,
            partner_profit_amount,
            quantity,
            unit_price,
            total_price,
            original_unit_price,
            original_total_price,
            promo_discount_percent,
            promo_code_id,
            promo_code_text,
            status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
        """,
        (
            user_id,
            proxy_kind,
            category_id,
            category_name,
            item_id,
            protocol,
            product_title,
            partner_bot_id,
            purchase_unit_price,
            partner_margin_percentage,
            applied_partner_profit_amount,
            quantity,
            discount_data["unit_price"],
            discount_data["total_price"],
            discount_data["original_unit_price"],
            discount_data["original_total_price"],
            discount_data["promo_discount_percent"],
            discount_data["promo_code_id"],
            discount_data["promo_code_text"],
            now_value,
            now_value,
        ),
    )
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return order_id


def create_topup(user_id: int, amount: float, partner_bot_id: int | None = None, balance_kind: str = "main"):
    conn = get_connection()
    cursor = conn.cursor()
    now_value = _now_text()
    normalized_balance_kind = "api" if str(balance_kind).lower() == "api" else "main"
    credited_user_id = int(user_id)
    if normalized_balance_kind == "api":
        api_account = get_api_account(int(user_id))
        if api_account is None or str(api_account.get("status")) != "approved" or not api_account.get("api_user_id"):
            conn.close()
            raise ValueError("API account is not approved")
        credited_user_id = int(api_account["api_user_id"])
    cursor.execute(
        """
        INSERT INTO topups (
            user_id,
            amount,
            partner_bot_id,
            balance_kind,
            credited_user_id,
            status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)
        """,
        (
            user_id,
            amount,
            int(partner_bot_id) if partner_bot_id else None,
            normalized_balance_kind,
            credited_user_id,
            now_value,
            now_value,
        ),
    )
    topup_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return topup_id


def _record_payment_invoice(
    cursor: sqlite3.Cursor,
    *,
    entity_type: str,
    entity_id: int,
    payment_provider: str,
    client_invoice_id: str | None,
    invoice_id: str | None,
    pay_url: str | None,
    payment_asset: str | None,
) -> None:
    normalized_invoice_id = str(invoice_id or "").strip()
    if entity_type not in {"order", "topup"} or not normalized_invoice_id:
        return
    cursor.execute(
        """
        INSERT OR IGNORE INTO payment_invoices (
            entity_type,
            entity_id,
            payment_provider,
            client_invoice_id,
            invoice_id,
            pay_url,
            payment_asset,
            created_at,
            created_at_unix
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            int(entity_id),
            str(payment_provider or "xrocket").strip().lower() or "xrocket",
            str(client_invoice_id or "").strip() or None,
            normalized_invoice_id,
            str(pay_url or "").strip() or None,
            str(payment_asset or "").strip() or None,
            _now_text(),
            int(datetime.now().timestamp()),
        ),
    )


def update_topup_invoice(
    topup_id: int,
    invoice_id: str,
    pay_url: str | None,
    payment_asset: str,
    payment_provider: str = "xrocket",
):
    conn = get_connection()
    cursor = conn.cursor()
    _record_payment_invoice(
        cursor,
        entity_type="topup",
        entity_id=topup_id,
        payment_provider=payment_provider,
        client_invoice_id=None,
        invoice_id=invoice_id,
        pay_url=pay_url,
        payment_asset=payment_asset,
    )
    cursor.execute(
        """
        UPDATE topups
        SET
            payment_provider = ?,
            xrocket_invoice_id = ?,
            xrocket_pay_url = ?,
            payment_asset = ?,
            status = 'waiting_payment',
            reminder_sent = 0,
            reminder_sent_at = NULL,
            updated_at = ?
        WHERE id = ?
        """,
        (
            payment_provider,
            invoice_id,
            pay_url,
            payment_asset,
            _now_text(),
            topup_id,
        ),
    )
    conn.commit()
    conn.close()


def get_topup(topup_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM topups WHERE id = ?", (topup_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def create_sales_log_event(
    event_kind: str,
    message_text: str,
    *,
    order_id: int | None = None,
    topup_id: int | None = None,
    user_id: int | None = None,
    shop_name: str | None = None,
    event_label: str | None = None,
):
    normalized_kind = (event_kind or "").strip().lower() or "generic"
    now_value = _now_text()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO sales_log_events (
            event_kind,
            order_id,
            topup_id,
            user_id,
            shop_name,
            event_label,
            message_text,
            telegram_status,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
        """,
        (
            normalized_kind,
            order_id,
            topup_id,
            user_id,
            shop_name,
            event_label,
            message_text,
            now_value,
            now_value,
        ),
    )
    event_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return int(event_id)


def mark_sales_log_event_sent(event_id: int, telegram_message_id: int | None = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE sales_log_events
        SET
            telegram_status = 'sent',
            telegram_error = NULL,
            telegram_message_id = COALESCE(?, telegram_message_id),
            updated_at = ?
        WHERE id = ?
        """,
        (
            telegram_message_id,
            _now_text(),
            event_id,
        ),
    )
    conn.commit()
    conn.close()


def mark_sales_log_event_failed(event_id: int, error_text: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE sales_log_events
        SET
            telegram_status = 'failed',
            telegram_error = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            str(error_text or "")[:1000],
            _now_text(),
            event_id,
        ),
    )
    conn.commit()
    conn.close()


def list_pending_invoice_reminders(history_max_age_seconds: int = 48 * 60 * 60):
    conn = get_connection()
    cursor = conn.cursor()
    history_cutoff = int(datetime.now().timestamp()) - max(int(history_max_age_seconds), 60)
    cursor.execute(
        """
        SELECT * FROM (
            SELECT
                'order' AS entity_type,
                orders.id,
                orders.user_id,
                invoices.payment_provider,
                invoices.client_invoice_id,
                invoices.invoice_id,
                invoices.pay_url,
                invoices.payment_asset,
                orders.reminder_sent,
                invoices.created_at AS invoice_created_at
            FROM payment_invoices AS invoices
            JOIN orders
              ON invoices.entity_type = 'order'
             AND invoices.entity_id = orders.id
            WHERE orders.status = 'waiting_payment'
              AND invoices.created_at_unix >= ?
            UNION ALL
            SELECT
                'topup' AS entity_type,
                topups.id,
                topups.user_id,
                invoices.payment_provider,
                invoices.client_invoice_id,
                invoices.invoice_id,
                invoices.pay_url,
                invoices.payment_asset,
                topups.reminder_sent,
                invoices.created_at AS invoice_created_at
            FROM payment_invoices AS invoices
            JOIN topups
              ON invoices.entity_type = 'topup'
             AND invoices.entity_id = topups.id
            WHERE topups.status = 'waiting_payment'
              AND invoices.created_at_unix >= ?
            UNION ALL
            SELECT
                'order' AS entity_type,
                orders.id,
                orders.user_id,
                COALESCE(orders.payment_provider, 'xrocket') AS payment_provider,
                orders.xrocket_client_invoice_id AS client_invoice_id,
                orders.xrocket_invoice_id AS invoice_id,
                orders.xrocket_pay_url AS pay_url,
                orders.payment_asset,
                orders.reminder_sent,
                orders.updated_at AS invoice_created_at
            FROM orders
            WHERE orders.status = 'waiting_payment'
              AND orders.xrocket_invoice_id IS NOT NULL
              AND orders.xrocket_invoice_id != ''
              AND NOT EXISTS (
                  SELECT 1 FROM payment_invoices AS current_invoice
                  WHERE current_invoice.entity_type = 'order'
                    AND current_invoice.entity_id = orders.id
                    AND current_invoice.payment_provider = COALESCE(orders.payment_provider, 'xrocket')
                    AND current_invoice.invoice_id = orders.xrocket_invoice_id
              )
            UNION ALL
            SELECT
                'topup' AS entity_type,
                topups.id,
                topups.user_id,
                COALESCE(topups.payment_provider, 'xrocket') AS payment_provider,
                NULL AS client_invoice_id,
                topups.xrocket_invoice_id AS invoice_id,
                topups.xrocket_pay_url AS pay_url,
                topups.payment_asset,
                topups.reminder_sent,
                topups.updated_at AS invoice_created_at
            FROM topups
            WHERE topups.status = 'waiting_payment'
              AND topups.xrocket_invoice_id IS NOT NULL
              AND topups.xrocket_invoice_id != ''
              AND NOT EXISTS (
                  SELECT 1 FROM payment_invoices AS current_invoice
                  WHERE current_invoice.entity_type = 'topup'
                    AND current_invoice.entity_id = topups.id
                    AND current_invoice.payment_provider = COALESCE(topups.payment_provider, 'xrocket')
                    AND current_invoice.invoice_id = topups.xrocket_invoice_id
              )
        ) AS pending_invoices
        ORDER BY reminder_sent ASC, id DESC
        """
        ,
        (history_cutoff, history_cutoff),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def mark_invoice_reminder_sent(entity_type: str, entity_id: int):
    if entity_type not in {"order", "topup"}:
        return

    table_name = "orders" if entity_type == "order" else "topups"
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"UPDATE {table_name} SET reminder_sent = 1, reminder_sent_at = ? WHERE id = ?",
        (_now_text(), entity_id),
    )
    conn.commit()
    conn.close()


def complete_topup_payment(topup_id: int):
    conn = get_connection()
    try:
        cursor = _begin_immediate(conn)
        cursor.execute("SELECT * FROM topups WHERE id = ?", (topup_id,))
        topup = cursor.fetchone()
        if topup is None:
            return None

        if topup["status"] == "paid":
            return dict(topup)

        now_value = _now_text()
        cursor.execute(
            """
            UPDATE topups
            SET status = 'paid', updated_at = ?
            WHERE id = ? AND status != 'paid'
            """,
            (now_value, topup_id),
        )
        if cursor.rowcount == 0:
            cursor.execute("SELECT * FROM topups WHERE id = ?", (topup_id,))
            updated_topup = cursor.fetchone()
            return dict(updated_topup) if updated_topup else None

        cursor.execute(
            """
            INSERT OR IGNORE INTO users (
                user_id,
                balance,
                purchases_count,
                purchases_total,
                topups_total,
                referral_earnings,
                registered_at,
                referral_code,
                referred_by
            )
            VALUES (?, 0.0, 0, 0.0, 0.0, 0.0, ?, ?, NULL)
            """,
            (
                topup["user_id"],
                now_value,
                _generate_referral_code(int(topup["user_id"])),
            ),
        )
        balance_user_id = int(topup["credited_user_id"] or topup["user_id"])
        if str(topup["balance_kind"] or "main") == "api":
            cursor.execute(
                "UPDATE users SET balance = balance + ? WHERE user_id = ?",
                (topup["amount"], balance_user_id),
            )
        else:
            cursor.execute(
                """
                UPDATE users
                SET balance = balance + ?, topups_total = topups_total + ?
                WHERE user_id = ?
                """,
                (topup["amount"], topup["amount"], balance_user_id),
            )
            # Relations that existed before the marketplace-profit program
            # retain their original reward: 25% of each paid main balance
            # top-up. New relations are rewarded only when a purchase finishes.
            cursor.execute(
                """
                SELECT referred_by, referral_reward_model
                FROM users
                WHERE user_id = ?
                """,
                (int(topup["user_id"]),),
            )
            referral = cursor.fetchone()
            if (
                referral
                and referral["referred_by"] is not None
                and str(referral["referral_reward_model"] or LEGACY_REFERRAL_MODEL)
                == LEGACY_REFERRAL_MODEL
                and not topup["partner_bot_id"]
            ):
                referral_amount = round(
                    float(topup["amount"] or 0.0) * LEGACY_TOPUP_REFERRAL_PERCENT / 100.0,
                    6,
                )
                if referral_amount > 0:
                    cursor.execute(
                        """
                        UPDATE users
                        SET balance = balance + ?,
                            referral_earnings = referral_earnings + ?
                        WHERE user_id = ?
                        """,
                        (referral_amount, referral_amount, int(referral["referred_by"])),
                    )
        # Franchise owners earn only from delivered purchases. Deposits are
        # customer funds and must never increase franchise earnings.
        if topup["partner_bot_id"] and not int(topup["partner_credit_accrued"] or 0):
            cursor.execute(
                "UPDATE topups SET partner_credit_accrued = 1 WHERE id = ?",
                (topup_id,),
            )
        conn.commit()
        cursor.execute("SELECT * FROM topups WHERE id = ?", (topup_id,))
        updated_topup = cursor.fetchone()
        return dict(updated_topup) if updated_topup else None
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def update_order_invoice(
    order_id: int,
    client_invoice_id: str,
    invoice_id: str | None,
    pay_url: str | None,
    payment_asset: str,
    payment_provider: str = "xrocket",
):
    conn = get_connection()
    cursor = conn.cursor()
    _record_payment_invoice(
        cursor,
        entity_type="order",
        entity_id=order_id,
        payment_provider=payment_provider,
        client_invoice_id=client_invoice_id,
        invoice_id=invoice_id,
        pay_url=pay_url,
        payment_asset=payment_asset,
    )
    cursor.execute(
        """
        UPDATE orders
        SET
            payment_provider = ?,
            xrocket_client_invoice_id = ?,
            xrocket_invoice_id = ?,
            xrocket_pay_url = ?,
            payment_asset = ?,
            status = 'waiting_payment',
            reminder_sent = 0,
            reminder_sent_at = NULL,
            updated_at = ?
        WHERE id = ?
        """,
        (
            payment_provider,
            client_invoice_id,
            invoice_id,
            pay_url,
            payment_asset,
            _now_text(),
            order_id,
        ),
    )
    conn.commit()
    conn.close()


def update_order_supplier_data(
    order_id: int,
    supplier_order_uuid: str,
    supplier_order_number: str | None = None,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE orders
        SET
            supplier_order_uuid = ?,
            supplier_order_number = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            supplier_order_uuid,
            supplier_order_number,
            _now_text(),
            order_id,
        ),
    )
    conn.commit()
    conn.close()


def mark_order_paid(order_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE orders SET status = 'paid', updated_at = ? WHERE id = ?",
        (_now_text(), order_id),
    )
    conn.commit()
    conn.close()


def mark_order_paid_from_invoice(order_id: int, payment_invoice: dict):
    """Confirm an order using any invoice ever issued for that order."""
    provider = str(payment_invoice.get("payment_provider") or "xrocket").strip().lower() or "xrocket"
    invoice_id = str(payment_invoice.get("invoice_id") or "").strip()
    if not invoice_id:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE orders
        SET payment_provider = ?,
            xrocket_client_invoice_id = ?,
            xrocket_invoice_id = ?,
            xrocket_pay_url = COALESCE(?, xrocket_pay_url),
            payment_asset = COALESCE(?, payment_asset),
            status = 'paid',
            updated_at = ?
        WHERE id = ? AND status = 'waiting_payment'
        """,
        (
            provider,
            str(payment_invoice.get("client_invoice_id") or "").strip() or invoice_id,
            invoice_id,
            str(payment_invoice.get("pay_url") or "").strip() or None,
            str(payment_invoice.get("payment_asset") or "").strip() or None,
            _now_text(),
            int(order_id),
        ),
    )
    conn.commit()
    cursor.execute("SELECT * FROM orders WHERE id = ?", (int(order_id),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def mark_order_delivery_pending(order_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE orders
        SET status = 'delivery_pending', updated_at = ?
        WHERE id = ? AND status NOT IN ('delivered', 'credited')
        """,
        (_now_text(), order_id),
    )
    conn.commit()
    conn.close()


def set_proxy_order_provider_reference(order_id: int, provider_order_id: int, status: str = "delivery_pending"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE orders
        SET
            provider_order_id = ?,
            status = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            provider_order_id,
            status,
            _now_text(),
            order_id,
        ),
    )
    conn.commit()
    conn.close()


def charge_user_balance_for_order(order_id: int):
    conn = get_connection()
    try:
        cursor = _begin_immediate(conn)
        cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        order = cursor.fetchone()
        if order is None:
            return None, "order_not_found"

        terminal_statuses = {"paid", "delivery_pending", "delivered", "credited"}
        if order["status"] in terminal_statuses:
            return dict(order), None

        now_value = _now_text()
        cursor.execute(
            """
            UPDATE orders
            SET
                status = 'paid',
                payment_provider = 'balance',
                payment_asset = 'BALANCE',
                updated_at = ?
            WHERE
                id = ?
                AND status NOT IN ('paid', 'delivery_pending', 'delivered', 'credited')
            """,
            (now_value, order_id),
        )
        if cursor.rowcount == 0:
            cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
            current_order = cursor.fetchone()
            _rollback_quietly(conn)
            return (dict(current_order) if current_order else None), None

        cursor.execute(
            """
            UPDATE users
            SET balance = balance - ?
            WHERE user_id = ? AND balance >= ?
            """,
            (order["total_price"], order["user_id"], order["total_price"]),
        )
        if cursor.rowcount == 0:
            cursor.execute("SELECT 1 FROM users WHERE user_id = ?", (order["user_id"],))
            user_exists = cursor.fetchone() is not None
            _rollback_quietly(conn)
            if not user_exists:
                return None, "user_not_found"
            return None, "insufficient_balance"

        conn.commit()
        cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        updated_order = cursor.fetchone()
        return (dict(updated_order) if updated_order else None), None
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def mark_order_failed(order_id: int, status: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now_text(), order_id),
    )
    conn.commit()
    conn.close()


def _get_completed_order_referral_reward(cursor, order: sqlite3.Row) -> tuple[int | None, float]:
    """Return the referrer and reward funded by this order's actual profit.

    Main-bot rewards are based on the shop's profit.  Franchise rewards are
    based only on that franchise's profit and are therefore deducted from the
    amount accrued to its owner later in the same transaction.
    """
    if order["partner_bot_id"]:
        cursor.execute(
            """
            SELECT referred_by
            FROM partner_bot_referrals
            WHERE partner_bot_id = ? AND user_id = ?
            """,
            (int(order["partner_bot_id"]), int(order["user_id"])),
        )
        referred_by_row = cursor.fetchone()
    else:
        cursor.execute(
            "SELECT referred_by, referral_reward_model FROM users WHERE user_id = ?",
            (order["user_id"],),
        )
        referred_by_row = cursor.fetchone()
    if not referred_by_row or referred_by_row["referred_by"] is None:
        return None, 0.0

    referrer_id = int(referred_by_row["referred_by"])
    if (
        not order["partner_bot_id"]
        and str(referred_by_row["referral_reward_model"] or LEGACY_REFERRAL_MODEL)
        == LEGACY_REFERRAL_MODEL
    ):
        return None, 0.0
    referral_percent = DEFAULT_REFERRAL_PERCENT
    referral_profit_base = 0.0
    if order["partner_bot_id"]:
        cursor.execute(
            """
            SELECT referral_enabled, referral_percent
            FROM partners_bots
            WHERE id = ?
            """,
            (order["partner_bot_id"],),
        )
        partner_bot = cursor.fetchone()
        if partner_bot is None or int(partner_bot["referral_enabled"] or 0) != 1:
            return None, 0.0
        referral_percent = _get_partner_referral_percent_for_referrer(
            cursor,
            int(order["partner_bot_id"]),
            referrer_id,
            float(partner_bot["referral_percent"] or 0.0),
        )
        referral_profit_base = max(float(order["partner_profit_amount"] or 0.0), 0.0)
    else:
        quantity = max(int(order["quantity"] or 0), 0)
        supplier_cost_total = float(order["purchase_unit_price"] or 0.0) * quantity
        referral_profit_base = max(float(order["total_price"] or 0.0) - supplier_cost_total, 0.0)

    referral_amount = referral_profit_base * max(referral_percent, 0.0) / 100
    return referrer_id, max(referral_amount, 0.0)


def _apply_completed_order_side_effects(cursor, order: sqlite3.Row):
    cursor.execute(
        """
        UPDATE users
        SET
            purchases_count = purchases_count + 1,
            purchases_total = purchases_total + ?
        WHERE user_id = ?
        """,
        (order["total_price"], order["user_id"]),
    )

    referrer_id, referral_amount = _get_completed_order_referral_reward(cursor, order)
    if referrer_id is not None and referral_amount > 0:
        cursor.execute(
            """
            UPDATE users
            SET
                balance = balance + ?,
                referral_earnings = referral_earnings + ?
            WHERE user_id = ?
            """,
            (
                referral_amount,
                referral_amount,
                referrer_id,
            ),
        )


def _accrue_partner_profit_for_order(cursor, order_id: int):
    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    if order is None:
        return
    if not order["partner_bot_id"] or order["partner_profit_accrued"]:
        return

    amount = float(order["partner_profit_amount"] or 0.0)
    # A franchise's referral program is paid from the franchise owner's own
    # income, rather than being an additional platform-funded reward.
    if amount > 0:
        _, referral_amount = _get_completed_order_referral_reward(cursor, order)
        amount = max(amount - referral_amount, 0.0)
    if amount > 0:
        cursor.execute(
            """
            UPDATE partners_bots
            SET
                partner_earnings = partner_earnings + ?,
                updated_at = ?
            WHERE id = ?
            """,
            (amount, _now_text(), order["partner_bot_id"]),
        )
        _accrue_partner_owner_referral_cursor(
            cursor,
            int(order["partner_bot_id"]),
            "order",
            int(order_id),
            amount,
        )
    cursor.execute(
        "UPDATE orders SET partner_profit_accrued = 1 WHERE id = ?",
        (order_id,),
    )


def credit_order_to_user_balance(order_id: int, status: str = "credited"):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    if order is None:
        conn.close()
        return None

    if order["status"] in {"credited", "delivered"}:
        result = dict(order)
        conn.close()
        return result

    cursor.execute(
        """
        UPDATE users
        SET
            balance = balance + ?
        WHERE user_id = ?
        """,
        (order["total_price"], order["user_id"]),
    )
    cursor.execute(
        "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now_text(), order_id),
    )
    conn.commit()

    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    updated_order = cursor.fetchone()
    conn.close()
    return dict(updated_order) if updated_order else None


def complete_order(order_id: int, provider_order_id: int, delivery_text: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    if order is None:
        conn.close()
        return

    cursor.execute(
        """
        UPDATE orders
        SET
            provider_order_id = ?,
            delivery_text = ?,
            status = 'delivered',
            updated_at = ?
        WHERE id = ?
        """,
        (
            provider_order_id,
            delivery_text,
            _now_text(),
            order_id,
        ),
    )
    _apply_completed_order_side_effects(cursor, order)
    _accrue_partner_profit_for_order(cursor, order_id)

    conn.commit()
    conn.close()


def complete_market_order(
    order_id: int,
    supplier_order_uuid: str,
    supplier_order_number: str | None,
    delivery_text: str,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    order = cursor.fetchone()
    if order is None:
        conn.close()
        return

    cursor.execute(
        """
        UPDATE orders
        SET
            supplier_order_uuid = ?,
            supplier_order_number = ?,
            delivery_text = ?,
            status = 'delivered',
            updated_at = ?
        WHERE id = ?
        """,
        (
            supplier_order_uuid,
            supplier_order_number,
            delivery_text,
            _now_text(),
            order_id,
        ),
    )
    _apply_completed_order_side_effects(cursor, order)
    _accrue_partner_profit_for_order(cursor, order_id)

    conn.commit()
    conn.close()


def get_order(order_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_user_orders(user_id: int, limit: int = 10, offset: int = 0):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM orders
        WHERE user_id = ? AND status = 'delivered'
        ORDER BY
            id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def count_user_orders(user_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM orders WHERE user_id = ? AND status = 'delivered'",
        (user_id,),
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count


def list_user_orders_any_status(user_id: int, limit: int = 10, offset: int = 0):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM orders
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def count_user_orders_any_status(user_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM orders WHERE user_id = ?",
        (user_id,),
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count


def list_pending_market_delivery_orders(limit: int = 100):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM orders
        WHERE proxy_kind = 'account' AND status IN ('paid', 'delivery_pending')
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_pending_proxy_delivery_orders(limit: int = 100):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM orders
        WHERE proxy_kind = 'static'
          AND status IN ('paid', 'delivery_pending')
          AND provider_order_id IS NOT NULL
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_proxy_catalog_fallback_items(limit: int = 50):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            category_id,
            category_name,
            item_id,
            protocol,
            MAX(id) AS last_order_id
        FROM orders
        WHERE proxy_kind = 'static'
        GROUP BY category_id, category_name, item_id, protocol
        ORDER BY last_order_id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_user_topups(user_id: int, limit: int = 20, offset: int = 0):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM topups
        WHERE user_id = ? AND status = 'paid'
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (user_id, limit, offset),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_order_by_client_invoice_id(client_invoice_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM orders WHERE xrocket_client_invoice_id = ?",
        (client_invoice_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def give_all():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    result = cursor.fetchall()
    conn.close()
    return [row[0] for row in result]


def get_count():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def get_orders_stats() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM orders")
    total_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'delivered'")
    delivered_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'waiting_payment'")
    waiting_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM orders WHERE status = 'credited'")
    credited_orders = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(total_price), 0) FROM orders WHERE status = 'delivered'")
    delivered_revenue = float(cursor.fetchone()[0] or 0)
    cursor.execute("SELECT COALESCE(SUM(total_price), 0) FROM orders")
    gross_revenue = float(cursor.fetchone()[0] or 0)
    conn.close()
    return {
        "total_orders": total_orders,
        "delivered_orders": delivered_orders,
        "waiting_orders": waiting_orders,
        "credited_orders": credited_orders,
        "delivered_revenue": delivered_revenue,
        "gross_revenue": gross_revenue,
    }


def get_crm_stats() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE purchases_count > 0")
    buyers = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE purchases_count > 1")
    repeat_buyers = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM users WHERE referred_by IS NOT NULL")
    referred_users = cursor.fetchone()[0]
    cursor.execute("SELECT COALESCE(SUM(balance), 0) FROM users")
    balances_total = float(cursor.fetchone()[0] or 0)
    cursor.execute("SELECT COALESCE(SUM(topups_total), 0) FROM users")
    topups_total = float(cursor.fetchone()[0] or 0)
    cursor.execute("SELECT COALESCE(SUM(purchases_total), 0) FROM users")
    purchases_total = float(cursor.fetchone()[0] or 0)
    cursor.execute("SELECT COUNT(*) FROM topups WHERE status = 'paid'")
    paid_topups = cursor.fetchone()[0]
    conn.close()
    return {
        "total_users": total_users,
        "buyers": buyers,
        "repeat_buyers": repeat_buyers,
        "referred_users": referred_users,
        "balances_total": balances_total,
        "topups_total": topups_total,
        "purchases_total": purchases_total,
        "paid_topups": paid_topups,
    }


def list_users(limit: int = 100, offset: int = 0):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM users
        ORDER BY user_id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_orders(limit: int = 100, offset: int = 0):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM orders
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def list_topups(limit: int = 100, offset: int = 0):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM topups
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_crm_dashboard_stats() -> dict:
    users = list_users(limit=100000, offset=0)
    orders = list_orders(limit=100000, offset=0)
    topups = list_topups(limit=100000, offset=0)
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=6)

    def in_period(moment: datetime | None, start: datetime) -> bool:
        return moment is not None and moment >= start

    def build_period_metrics(start: datetime) -> dict:
        metrics = {
            "new_users": 0,
            "buyers": 0,
            "repeat_buyers": 0,
            "orders_paid": 0,
            "orders_delivered": 0,
            "credited_orders": 0,
            "gross_revenue": 0.0,
            "delivered_revenue": 0.0,
            "gross_profit": 0.0,
            "platform_profit": 0.0,
            "partner_profit": 0.0,
            "credited_amount": 0.0,
            "paid_topups": 0,
            "topups_amount": 0.0,
        }

        buyer_ids: set[int] = set()
        repeat_buyer_ids: set[int] = set()

        for user in users:
            registered_at = parse_datetime(user.get("registered_at"))
            if in_period(registered_at, start):
                metrics["new_users"] += 1

        for order in orders:
            updated_at = parse_datetime(order.get("updated_at"))
            created_at = parse_datetime(order.get("created_at"))
            status = str(order.get("status") or "")
            quantity = max(int(order.get("quantity") or 0), 0)
            total_price = float(order.get("total_price") or 0.0)
            supplier_cost_total = float(order.get("purchase_unit_price") or 0.0) * quantity
            gross_profit = max(total_price - supplier_cost_total, 0.0)
            partner_profit = max(float(order.get("partner_profit_amount") or 0.0), 0.0)
            platform_profit = max(gross_profit - partner_profit, 0.0)

            if status != "waiting_payment" and in_period(created_at, start):
                metrics["orders_paid"] += 1
                metrics["gross_revenue"] += total_price

            if status == "delivered" and in_period(updated_at, start):
                metrics["orders_delivered"] += 1
                metrics["delivered_revenue"] += total_price
                metrics["gross_profit"] += gross_profit
                metrics["platform_profit"] += platform_profit
                metrics["partner_profit"] += partner_profit
                user_id = int(order.get("user_id") or 0)
                if user_id:
                    if user_id in buyer_ids:
                        repeat_buyer_ids.add(user_id)
                    buyer_ids.add(user_id)
            elif status == "credited" and in_period(updated_at, start):
                metrics["credited_orders"] += 1
                metrics["credited_amount"] += total_price

        metrics["buyers"] = len(buyer_ids)
        metrics["repeat_buyers"] = len(repeat_buyer_ids)

        for topup in topups:
            if str(topup.get("status") or "") != "paid":
                continue
            if in_period(parse_datetime(topup.get("updated_at")), start):
                metrics["paid_topups"] += 1
                metrics["topups_amount"] += float(topup.get("amount") or 0.0)

        return {
            key: round(value, 2) if isinstance(value, float) else value
            for key, value in metrics.items()
        }

    order_status_counts = Counter(str(order.get("status") or "unknown") for order in orders)
    order_provider_counts: Counter[str] = Counter()
    topup_provider_counts: Counter[str] = Counter()
    delivered_revenue_total = 0.0
    gross_revenue_total = 0.0
    gross_profit_total = 0.0
    platform_profit_total = 0.0
    partner_profit_total = 0.0
    credited_amount_total = 0.0
    delivered_orders_total = 0
    credited_orders_total = 0
    delivered_order_rows: list[dict] = []

    for order in orders:
        status = str(order.get("status") or "")
        total_price = float(order.get("total_price") or 0.0)
        quantity = max(int(order.get("quantity") or 0), 0)
        supplier_cost_total = float(order.get("purchase_unit_price") or 0.0) * quantity
        gross_profit = max(total_price - supplier_cost_total, 0.0)
        partner_profit = max(float(order.get("partner_profit_amount") or 0.0), 0.0)
        platform_profit = max(gross_profit - partner_profit, 0.0)
        provider_label = str(order.get("payment_provider") or "unknown").strip().lower() or "unknown"

        gross_revenue_total += total_price
        if status == "delivered":
            delivered_orders_total += 1
            delivered_revenue_total += total_price
            gross_profit_total += gross_profit
            platform_profit_total += platform_profit
            partner_profit_total += partner_profit
            order_provider_counts[provider_label] += 1
            delivered_order_rows.append(
                {
                    "id": int(order.get("id") or 0),
                    "revenue": round(total_price, 2),
                    "gross_profit": round(gross_profit, 2),
                    "platform_profit": round(platform_profit, 2),
                    "partner_profit": round(partner_profit, 2),
                    "payment_provider": provider_label,
                    "payment_asset": str(order.get("payment_asset") or "").strip(),
                    "updated_at": order.get("updated_at") or "",
                    "is_partner_order": bool(order.get("partner_bot_id")),
                }
            )
        elif status == "credited":
            credited_orders_total += 1
            credited_amount_total += total_price

    paid_topups_total = 0
    paid_topups_amount_total = 0.0
    for topup in topups:
        if str(topup.get("status") or "") != "paid":
            continue
        paid_topups_total += 1
        paid_topups_amount_total += float(topup.get("amount") or 0.0)
        provider_label = str(topup.get("payment_provider") or "unknown").strip().lower() or "unknown"
        topup_provider_counts[provider_label] += 1

    delivered_order_rows.sort(
        key=lambda row: parse_datetime(row.get("updated_at")) or datetime.min,
        reverse=True,
    )

    total_users = len(users)
    buyers = sum(1 for user in users if int(user.get("purchases_count") or 0) > 0)
    repeat_buyers = sum(1 for user in users if int(user.get("purchases_count") or 0) > 1)
    referred_users = sum(1 for user in users if user.get("referred_by") is not None)
    balances_total = round(sum(float(user.get("balance") or 0.0) for user in users), 2)
    topups_total = round(sum(float(user.get("topups_total") or 0.0) for user in users), 2)
    purchases_total = round(sum(float(user.get("purchases_total") or 0.0) for user in users), 2)
    referral_earnings_total = round(sum(float(user.get("referral_earnings") or 0.0) for user in users), 2)
    conversion_rate = round((buyers / total_users) * 100, 2) if total_users else 0.0
    repeat_rate = round((repeat_buyers / buyers) * 100, 2) if buyers else 0.0
    average_check = round(delivered_revenue_total / delivered_orders_total, 2) if delivered_orders_total else 0.0

    franchise_stats = get_all_franchises_stats()
    total_franchises = int(franchise_stats.get("total_bots") or 0)
    franchise_profit = round(float(franchise_stats.get("profit") or 0.0), 2)

    return {
        "generated_at": now.strftime("%H:%M:%S %d-%m-%Y"),
        "summary": {
            "total_users": total_users,
            "buyers": buyers,
            "repeat_buyers": repeat_buyers,
            "referred_users": referred_users,
            "balances_total": balances_total,
            "topups_total": topups_total,
            "purchases_total": purchases_total,
            "paid_topups": paid_topups_total,
            "paid_topups_amount": round(paid_topups_amount_total, 2),
            "conversion_rate": conversion_rate,
            "repeat_rate": repeat_rate,
            "average_check": average_check,
            "referral_earnings_total": referral_earnings_total,
        },
        "today": build_period_metrics(today_start),
        "week": build_period_metrics(week_start),
        "orders": {
            "total_orders": len(orders),
            "delivered_orders": delivered_orders_total,
            "credited_orders": credited_orders_total,
            "gross_revenue": round(gross_revenue_total, 2),
            "delivered_revenue": round(delivered_revenue_total, 2),
            "gross_profit": round(gross_profit_total, 2),
            "platform_profit": round(platform_profit_total, 2),
            "partner_profit": round(partner_profit_total, 2),
            "credited_amount": round(credited_amount_total, 2),
            "status_breakdown": [
                {"status": status, "count": count}
                for status, count in sorted(order_status_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
            "payment_breakdown": [
                {"provider": provider, "count": count}
                for provider, count in sorted(order_provider_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
        },
        "topups": {
            "paid_count": paid_topups_total,
            "paid_amount": round(paid_topups_amount_total, 2),
            "payment_breakdown": [
                {"provider": provider, "count": count}
                for provider, count in sorted(topup_provider_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
        },
        "franchises": {
            **franchise_stats,
            "total_franchises": total_franchises,
            "active_franchises": total_franchises,
            "total_buyers": int(franchise_stats.get("total_users") or 0),
            "total_revenue": round(float(franchise_stats.get("delivered_revenue") or 0.0), 2),
            "total_profit": franchise_profit,
            "average_profit_per_franchise": round(franchise_profit / total_franchises, 2) if total_franchises else 0.0,
        },
        "recent_deliveries": delivered_order_rows[:12],
    }


def get_utm_stats(limit: int | None = None, source_limit: int | None = None) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COALESCE(NULLIF(utm_source, ''), start_param, 'unknown') AS utm_source, COUNT(*) AS total,
               COALESCE(SUM(purchases_total), 0) AS purchases_total
        FROM users
        WHERE start_param IS NOT NULL AND start_param != ''
        GROUP BY COALESCE(NULLIF(utm_source, ''), start_param, 'unknown')
        ORDER BY total DESC, utm_source ASC
        """,
    )
    sources = [dict(row) for row in cursor.fetchall()]
    watched_params = ("cartel",)
    source_names = {str(row.get("utm_source") or "") for row in sources}
    for watched_param in watched_params:
        if watched_param in source_names:
            continue
        cursor.execute(
            """
            SELECT ? AS utm_source, COUNT(*) AS total,
                   COALESCE(SUM(purchases_total), 0) AS purchases_total
            FROM users
            WHERE start_param = ?
            HAVING COUNT(*) > 0
            """,
            (watched_param, watched_param),
        )
        row = cursor.fetchone()
        if row:
            sources.append(dict(row))
    cursor.execute(
        """
        SELECT COALESCE(utm_campaign, 'unknown') AS utm_campaign, COUNT(*) AS total,
               COALESCE(SUM(purchases_total), 0) AS purchases_total
        FROM users
        WHERE start_param IS NOT NULL AND start_param != ''
        GROUP BY COALESCE(utm_campaign, 'unknown')
        ORDER BY total DESC, utm_campaign ASC
        """,
    )
    campaigns = [dict(row) for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT COALESCE(start_param, 'unknown') AS start_param, COUNT(*) AS total,
               COALESCE(SUM(purchases_total), 0) AS purchases_total
        FROM users
        WHERE start_param IS NOT NULL AND start_param != ''
        GROUP BY COALESCE(start_param, 'unknown')
        ORDER BY total DESC, start_param ASC
        """,
    )
    params = [dict(row) for row in cursor.fetchall()]
    param_names = {str(row.get("start_param") or "") for row in params}
    for watched_param in watched_params:
        if watched_param in param_names:
            continue
        cursor.execute(
            """
            SELECT start_param, COUNT(*) AS total,
                   COALESCE(SUM(purchases_total), 0) AS purchases_total
            FROM users
            WHERE start_param = ?
            GROUP BY start_param
            """,
            (watched_param,),
        )
        row = cursor.fetchone()
        if row:
            params.append(dict(row))
    conn.close()
    return {
        "sources": sources,
        "campaigns": campaigns,
        "params": params,
    }


def get_partner_bot_utm_stats(bot_id: int, limit: int = 10) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COALESCE(NULLIF(s.utm_source, ''), s.start_param, 'unknown') AS utm_source, COUNT(*) AS total,
               COALESCE(SUM(COALESCE(p.purchases_total, 0)), 0) AS purchases_total
        FROM partner_bot_starts AS s
        LEFT JOIN (SELECT user_id, SUM(total_price) AS purchases_total FROM orders
                   WHERE partner_bot_id = ? AND status IN ('delivered', 'credited') GROUP BY user_id) AS p
          ON p.user_id = s.user_id
        WHERE s.bot_id = ? AND s.start_param IS NOT NULL AND s.start_param != ''
        GROUP BY COALESCE(NULLIF(s.utm_source, ''), s.start_param, 'unknown')
        ORDER BY total DESC, utm_source ASC
        LIMIT ?
        """,
        (bot_id, bot_id, limit),
    )
    sources = [dict(row) for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT COALESCE(s.utm_campaign, 'unknown') AS utm_campaign, COUNT(*) AS total,
               COALESCE(SUM(COALESCE(p.purchases_total, 0)), 0) AS purchases_total
        FROM partner_bot_starts AS s
        LEFT JOIN (SELECT user_id, SUM(total_price) AS purchases_total FROM orders
                   WHERE partner_bot_id = ? AND status IN ('delivered', 'credited') GROUP BY user_id) AS p
          ON p.user_id = s.user_id
        WHERE s.bot_id = ? AND s.start_param IS NOT NULL AND s.start_param != ''
        GROUP BY COALESCE(s.utm_campaign, 'unknown')
        ORDER BY total DESC, utm_campaign ASC
        LIMIT ?
        """,
        (bot_id, bot_id, limit),
    )
    campaigns = [dict(row) for row in cursor.fetchall()]
    cursor.execute(
        """
        SELECT COALESCE(s.start_param, 'unknown') AS start_param, COUNT(*) AS total,
               COALESCE(SUM(COALESCE(p.purchases_total, 0)), 0) AS purchases_total
        FROM partner_bot_starts AS s
        LEFT JOIN (SELECT user_id, SUM(total_price) AS purchases_total FROM orders
                   WHERE partner_bot_id = ? AND status IN ('delivered', 'credited') GROUP BY user_id) AS p
          ON p.user_id = s.user_id
        WHERE s.bot_id = ? AND s.start_param IS NOT NULL AND s.start_param != ''
        GROUP BY COALESCE(s.start_param, 'unknown')
        ORDER BY total DESC, start_param ASC
        LIMIT ?
        """,
        (bot_id, bot_id, limit),
    )
    params = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return {
        "sources": sources,
        "campaigns": campaigns,
        "params": params,
    }


def upsert_partner_bot_utm_link(bot_id: int, source_word: str, start_param: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    now_value = _now_text()
    cursor.execute(
        """
        INSERT INTO partner_bot_utm_links (
            bot_id,
            source_word,
            start_param,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(bot_id, source_word)
        DO UPDATE SET
            start_param = excluded.start_param,
            updated_at = excluded.updated_at
        """,
        (bot_id, source_word, start_param, now_value, now_value),
    )
    conn.commit()
    cursor.execute(
        """
        SELECT *
        FROM partner_bot_utm_links
        WHERE bot_id = ? AND source_word = ?
        """,
        (bot_id, source_word),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_partner_bot_utm_links(bot_id: int, limit: int = 10) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM partner_bot_utm_links
        WHERE bot_id = ?
        ORDER BY updated_at DESC, id DESC
        LIMIT ?
        """,
        (bot_id, limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def reserve_email_activation(
    *,
    user_id: int,
    request_key: str,
    site: str,
    domain: str,
    supplier_price_usd: float,
    sale_price_usd: float,
    partner_bot_id: int | None = None,
) -> tuple[dict | None, str | None, bool]:
    normalized_sale_price = round(max(float(sale_price_usd or 0.0), 0.0), 6)
    normalized_supplier_price = round(max(float(supplier_price_usd or 0.0), 0.0), 6)
    if normalized_sale_price <= 0:
        return None, "invalid_price", False

    conn = get_connection()
    try:
        cursor = _begin_immediate(conn)
        cursor.execute(
            "SELECT * FROM email_activations WHERE user_id = ? AND request_key = ?",
            (int(user_id), str(request_key)),
        )
        existing = cursor.fetchone()
        if existing is not None:
            conn.commit()
            return dict(existing), None, False

        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (int(user_id),))
        user_row = cursor.fetchone()
        if user_row is None:
            _rollback_quietly(conn)
            return None, "user_not_found", False
        current_balance = round(float(user_row["balance"] or 0.0), 6)
        if current_balance + 0.0000001 < normalized_sale_price:
            _rollback_quietly(conn)
            return None, "insufficient_balance", False

        now_value = _now_text()
        cursor.execute(
            "UPDATE users SET balance = ? WHERE user_id = ?",
            (round(current_balance - normalized_sale_price, 6), int(user_id)),
        )
        cursor.execute(
            """
            INSERT INTO email_activations (
                request_key,
                user_id,
                partner_bot_id,
                site,
                domain,
                supplier_price_usd,
                sale_price_usd,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'ordering', ?, ?)
            """,
            (
                str(request_key),
                int(user_id),
                int(partner_bot_id) if partner_bot_id else None,
                str(site),
                str(domain),
                normalized_supplier_price,
                normalized_sale_price,
                now_value,
                now_value,
            ),
        )
        activation_id = int(cursor.lastrowid)
        conn.commit()
        cursor.execute("SELECT * FROM email_activations WHERE id = ?", (activation_id,))
        row = cursor.fetchone()
        return (dict(row) if row else None), None, True
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def finalize_email_activation(local_id: int, provider_activation_id: str, email: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE email_activations
        SET provider_activation_id = ?, email = ?, status = 'waiting', error_code = NULL, updated_at = ?
        WHERE id = ? AND status = 'ordering'
        """,
        (str(provider_activation_id), str(email), _now_text(), int(local_id)),
    )
    conn.commit()
    cursor.execute("SELECT * FROM email_activations WHERE id = ?", (int(local_id),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def fail_email_activation_and_refund(local_id: int, error_code: str) -> dict | None:
    conn = get_connection()
    try:
        cursor = _begin_immediate(conn)
        cursor.execute("SELECT * FROM email_activations WHERE id = ?", (int(local_id),))
        row = cursor.fetchone()
        if row is None:
            _rollback_quietly(conn)
            return None
        if int(row["charged"] or 0) == 1 and int(row["refunded"] or 0) == 0:
            cursor.execute(
                "UPDATE users SET balance = ROUND(balance + ?, 6) WHERE user_id = ?",
                (float(row["sale_price_usd"] or 0.0), int(row["user_id"])),
            )
        cursor.execute(
            """
            UPDATE email_activations
            SET status = 'failed', error_code = ?, refunded = 1, updated_at = ?
            WHERE id = ?
            """,
            (str(error_code or "provider_error"), _now_text(), int(local_id)),
        )
        conn.commit()
        cursor.execute("SELECT * FROM email_activations WHERE id = ?", (int(local_id),))
        updated = cursor.fetchone()
        return dict(updated) if updated else None
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def update_email_activation_message(local_id: int, message_code: str, message_text: str) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE email_activations
        SET status = 'received', message_code = ?, message_text = ?, error_code = NULL, updated_at = ?
        WHERE id = ? AND status IN ('waiting', 'received')
        """,
        (str(message_code or ""), str(message_text or ""), _now_text(), int(local_id)),
    )
    conn.commit()
    cursor.execute("SELECT * FROM email_activations WHERE id = ?", (int(local_id),))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def cancel_email_activation_and_refund(local_id: int) -> tuple[dict | None, str | None]:
    conn = get_connection()
    try:
        cursor = _begin_immediate(conn)
        cursor.execute("SELECT * FROM email_activations WHERE id = ?", (int(local_id),))
        row = cursor.fetchone()
        if row is None:
            _rollback_quietly(conn)
            return None, "not_found"
        if str(row["status"] or "") == "received":
            _rollback_quietly(conn)
            return dict(row), "already_received"
        if str(row["status"] or "") == "canceled":
            conn.commit()
            return dict(row), None
        if int(row["charged"] or 0) == 1 and int(row["refunded"] or 0) == 0:
            cursor.execute(
                "UPDATE users SET balance = ROUND(balance + ?, 6) WHERE user_id = ?",
                (float(row["sale_price_usd"] or 0.0), int(row["user_id"])),
            )
        cursor.execute(
            """
            UPDATE email_activations
            SET status = 'canceled', refunded = 1, updated_at = ?
            WHERE id = ?
            """,
            (_now_text(), int(local_id)),
        )
        conn.commit()
        cursor.execute("SELECT * FROM email_activations WHERE id = ?", (int(local_id),))
        updated = cursor.fetchone()
        return (dict(updated) if updated else None), None
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def get_email_activation(local_id: int, user_id: int | None = None) -> dict | None:
    conn = get_connection()
    cursor = conn.cursor()
    if user_id is None:
        cursor.execute("SELECT * FROM email_activations WHERE id = ?", (int(local_id),))
    else:
        cursor.execute(
            "SELECT * FROM email_activations WHERE id = ? AND user_id = ?",
            (int(local_id), int(user_id)),
        )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def list_email_activations(user_id: int, limit: int = 30, offset: int = 0) -> list[dict]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT *
        FROM email_activations
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        (int(user_id), max(min(int(limit), 100), 1), max(int(offset), 0)),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def count_email_activations(user_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM email_activations WHERE user_id = ?", (int(user_id),))
    value = int(cursor.fetchone()[0] or 0)
    conn.close()
    return value


def list_recent_unavailable_email_domains(
    site: str,
    cooldown_seconds: int = 1800,
    limit: int = 300,
) -> set[str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT domain, updated_at
        FROM email_activations
        WHERE site = ? AND error_code = 'no emails'
        ORDER BY id DESC
        LIMIT ?
        """,
        (str(site or "").strip().lower(), max(min(int(limit), 1000), 1)),
    )
    rows = cursor.fetchall()
    conn.close()

    now = datetime.now()
    failure_signals: dict[str, dict] = {}
    for row in rows:
        domain = str(row["domain"] or "").strip().lower()
        if not domain:
            continue
        failed_at = parse_datetime(row["updated_at"])
        if failed_at is None:
            continue
        signal = failure_signals.setdefault(domain, {"latest": failed_at, "count": 0})
        signal["count"] += 1
        if failed_at > signal["latest"]:
            signal["latest"] = failed_at

    unavailable: set[str] = set()
    base_cooldown = max(int(cooldown_seconds), 0)
    for domain, signal in failure_signals.items():
        elapsed = (now - signal["latest"]).total_seconds()
        effective_cooldown = base_cooldown * min(max(int(signal["count"]), 1), 12)
        if 0 <= elapsed <= effective_cooldown:
            unavailable.add(domain)
    return unavailable
