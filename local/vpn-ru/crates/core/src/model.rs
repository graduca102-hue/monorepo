//! Core domain model. Field sets mirror the `migrations/` schema.

use serde::{Deserialize, Serialize};
use time::OffsetDateTime;
use uuid::Uuid;

/// A paying customer. Identified in Telegram by `tg_id`.
#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct User {
    pub id: Uuid,
    pub tg_id: i64,
    pub username: Option<String>,
    /// Per-user secret reused across every protocol on every node.
    pub vless_uuid: Uuid,
    /// Short-id for REALITY, hex, 2..16 chars.
    pub reality_short_id: String,
    /// Password for Hysteria2 / Trojan-style transports.
    pub proto_password: String,
    /// Opaque, rotatable public handle used in the subscription URL.
    pub sub_token: String,
    pub plan_id: Option<Uuid>,
    pub expires_at: Option<OffsetDateTime>,
    pub traffic_limit_bytes: Option<i64>,
    pub traffic_used_bytes: i64,
    pub device_limit: i32,
    pub status: UserStatus,
    pub created_at: OffsetDateTime,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, sqlx::Type)]
#[sqlx(type_name = "user_status", rename_all = "snake_case")]
#[serde(rename_all = "snake_case")]
pub enum UserStatus {
    Trial,
    Active,
    Expired,
    Suspended,
    Banned,
}

#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct Plan {
    pub id: Uuid,
    pub name: String,
    pub price_stars: i64,
    pub price_usdt: rust_decimal::Decimal,
    pub duration_days: i32,
    pub traffic_limit_bytes: Option<i64>,
    pub device_limit: i32,
    pub active: bool,
}

/// A VPS running the data plane. Rotated aggressively when it gets flagged.
#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct Node {
    pub id: Uuid,
    pub label: String,
    pub provider: String,
    pub region: String,
    pub ipv4: String,
    /// REALITY handshake target (a real foreign site we borrow the TLS from).
    pub reality_dest: String,
    pub reality_server_names: Vec<String>,
    pub reality_public_key: String,
    pub reality_private_key: String,
    /// CDN hostname fronting the XHTTP transport, if this node has one.
    pub cdn_host: Option<String>,
    pub status: NodeStatus,
    /// Rolling health score in 0..=100 from probe + agent reports.
    pub health: i16,
    pub created_at: OffsetDateTime,
    pub retired_at: Option<OffsetDateTime>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, sqlx::Type)]
#[sqlx(type_name = "node_status", rename_all = "snake_case")]
#[serde(rename_all = "snake_case")]
pub enum NodeStatus {
    /// Being provisioned, not yet in subscriptions.
    Warming,
    /// Serving traffic, included in subscriptions.
    Active,
    /// Suspected blocked; kept for existing users, excluded from new subs.
    Degraded,
    /// Confirmed blocked; being drained and replaced.
    Retiring,
    Retired,
}

/// One protocol endpoint exposed by a node.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, sqlx::Type)]
#[sqlx(type_name = "transport", rename_all = "kebab-case")]
#[serde(rename_all = "kebab-case")]
pub enum Transport {
    /// VLESS + XTLS-Vision + REALITY over TCP. Primary.
    VlessRealityTcp,
    /// VLESS + REALITY/TLS over XHTTP, usually behind a CDN. Anti-whitelist.
    VlessXhttp,
    /// Hysteria2, QUIC masquerading as HTTP/3. For lossy / throttled links.
    Hysteria2,
}

impl Transport {
    pub const ALL: [Transport; 3] = [
        Transport::VlessRealityTcp,
        Transport::VlessXhttp,
        Transport::Hysteria2,
    ];
}

/// A single reachability datapoint from a probe inside RF.
#[derive(Debug, Clone, Serialize, Deserialize, sqlx::FromRow)]
pub struct ProbeReport {
    pub probe_id: String,
    pub node_id: Uuid,
    pub transport: Transport,
    pub ok: bool,
    pub handshake_ms: Option<i32>,
    /// Throughput estimate; a sharp drop is the throttling signal.
    pub throughput_kbps: Option<i32>,
    pub observed_at: OffsetDateTime,
}
