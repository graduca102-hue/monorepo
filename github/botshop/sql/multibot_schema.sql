CREATE TABLE IF NOT EXISTS partners_bots (
    id BIGSERIAL PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    bot_token TEXT NOT NULL UNIQUE,
    bot_username TEXT NOT NULL,
    margin_percentage INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    bot_id BIGINT NOT NULL REFERENCES partners_bots(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    product_id INTEGER NOT NULL,
    purchase_price NUMERIC(12, 2) NOT NULL,
    final_price NUMERIC(12, 2) NOT NULL,
    partner_profit NUMERIC(12, 2) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_partners_bots_owner_id
    ON partners_bots(owner_id);

CREATE INDEX IF NOT EXISTS idx_orders_bot_id
    ON orders(bot_id);

CREATE INDEX IF NOT EXISTS idx_orders_user_id
    ON orders(user_id);
