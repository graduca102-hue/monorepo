-- vpn-ru initial schema

CREATE TYPE user_status AS ENUM ('trial', 'active', 'expired', 'suspended', 'banned');
CREATE TYPE node_status AS ENUM ('warming', 'active', 'degraded', 'retiring', 'retired');
CREATE TYPE transport   AS ENUM ('vless-reality-tcp', 'vless-xhttp', 'hysteria2');

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE plans (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name                 text NOT NULL,
    price_stars          bigint NOT NULL,
    price_usdt           numeric(12,2) NOT NULL,
    duration_days        int NOT NULL,
    traffic_limit_bytes  bigint,
    device_limit         int NOT NULL DEFAULT 3,
    active               boolean NOT NULL DEFAULT true
);

CREATE TABLE users (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tg_id                bigint NOT NULL UNIQUE,
    username             text,
    -- per-user secrets, reused across every node/transport
    vless_uuid           uuid NOT NULL DEFAULT gen_random_uuid(),
    reality_short_id     text NOT NULL DEFAULT encode(gen_random_bytes(4), 'hex'),
    proto_password       text NOT NULL DEFAULT encode(gen_random_bytes(12), 'hex'),
    -- opaque, rotatable public handle for the subscription URL
    sub_token            text NOT NULL DEFAULT encode(gen_random_bytes(16), 'hex') UNIQUE,
    plan_id              uuid REFERENCES plans(id),
    expires_at           timestamptz,
    traffic_limit_bytes  bigint,
    traffic_used_bytes   bigint NOT NULL DEFAULT 0,
    device_limit         int NOT NULL DEFAULT 3,
    status               user_status NOT NULL DEFAULT 'trial',
    created_at           timestamptz NOT NULL DEFAULT now()
);

-- new users get a 3-day trial window
ALTER TABLE users ALTER COLUMN expires_at SET DEFAULT now() + interval '3 days';

CREATE TABLE nodes (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    label                 text NOT NULL UNIQUE,
    provider              text NOT NULL,
    region                text NOT NULL,
    ipv4                  text NOT NULL,
    reality_dest          text NOT NULL DEFAULT 'www.microsoft.com:443',
    reality_server_names  text[] NOT NULL DEFAULT ARRAY['www.microsoft.com'],
    reality_public_key    text NOT NULL DEFAULT '',
    reality_private_key   text NOT NULL DEFAULT '',
    cdn_host              text,
    status                node_status NOT NULL DEFAULT 'warming',
    health                smallint NOT NULL DEFAULT 50,
    created_at            timestamptz NOT NULL DEFAULT now(),
    retired_at            timestamptz
);

CREATE TABLE probe_reports (
    id            bigserial PRIMARY KEY,
    probe_id      text NOT NULL,
    node_id       uuid NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    transport     transport NOT NULL,
    ok            boolean NOT NULL,
    handshake_ms  int,
    throughput_kbps int,
    observed_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX probe_reports_node_time ON probe_reports (node_id, observed_at DESC);

CREATE TABLE orders (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_id     uuid NOT NULL REFERENCES plans(id),
    method      text NOT NULL,            -- 'stars' | 'usdt-trc20'
    amount      numeric(12,2) NOT NULL,
    memo        text NOT NULL DEFAULT encode(gen_random_bytes(4), 'hex'),
    status      text NOT NULL DEFAULT 'pending',  -- pending | paid | expired
    created_at  timestamptz NOT NULL DEFAULT now(),
    paid_at     timestamptz
);

-- Extend a user's plan. Called by api on confirmed payment.
CREATE FUNCTION apply_plan(p_tg_id bigint, p_plan_id uuid) RETURNS void AS $$
DECLARE
    d int;
    lim bigint;
    dev int;
BEGIN
    SELECT duration_days, traffic_limit_bytes, device_limit
      INTO d, lim, dev
      FROM plans WHERE id = p_plan_id;

    UPDATE users
       SET plan_id = p_plan_id,
           status  = 'active',
           expires_at = GREATEST(expires_at, now()) + make_interval(days => d),
           traffic_limit_bytes = lim,
           traffic_used_bytes = 0,
           device_limit = dev
     WHERE tg_id = p_tg_id;
END;
$$ LANGUAGE plpgsql;

INSERT INTO plans (name, price_stars, price_usdt, duration_days, traffic_limit_bytes) VALUES
    ('1 месяц',   150,  2.00, 30,  NULL),
    ('3 месяца',  400,  5.00, 90,  NULL),
    ('1 год',    1400, 18.00, 365, NULL);
