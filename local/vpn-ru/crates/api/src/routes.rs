use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use sqlx::PgPool;
use uuid::Uuid;
use vpn_core::model::{Node, User};
use vpn_subscription::Format;

#[derive(Clone)]
pub struct AppState {
    pub pool: PgPool,
    pub token: String,
}

pub fn router(state: AppState) -> Router {
    Router::new()
        .route("/healthz", get(|| async { "ok" }))
        // Public: clients poll their subscription by opaque token.
        .route("/sub/{token}", get(subscription))
        // Internal (Bearer API_TOKEN):
        .route("/users", post(create_user))
        .route("/users/{tg_id}", get(get_user))
        .route("/users/{tg_id}/plan", post(assign_plan))
        .route("/nodes", get(list_nodes).post(upsert_node))
        .route("/nodes/{id}/status", post(set_node_status))
        // probe fleet <-> orchestrator
        .route("/probe/targets", get(probe_targets))
        .route("/probe/report", post(probe_report))
        .route("/probe/reports", get(recent_reports))
        .with_state(state)
}

fn authed(headers: &HeaderMap, token: &str) -> Result<(), StatusCode> {
    let ok = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .map(|v| v == token)
        .unwrap_or(false);
    if ok {
        Ok(())
    } else {
        Err(StatusCode::UNAUTHORIZED)
    }
}

// --- subscription ------------------------------------------------------------

async fn subscription(
    State(st): State<AppState>,
    Path(token): Path<String>,
    headers: HeaderMap,
) -> Result<String, StatusCode> {
    let user = sqlx::query_as::<_, User>("SELECT * FROM users WHERE sub_token = $1")
        .bind(&token)
        .fetch_optional(&st.pool)
        .await
        .map_err(internal)?
        .ok_or(StatusCode::NOT_FOUND)?;

    let nodes = sqlx::query_as::<_, Node>("SELECT * FROM nodes WHERE status = 'active'")
        .fetch_all(&st.pool)
        .await
        .map_err(internal)?;

    // v2rayNG sends a User-Agent we can sniff; default to sing-box otherwise.
    let ua = headers
        .get("user-agent")
        .and_then(|v| v.to_str().ok())
        .unwrap_or_default()
        .to_lowercase();
    let format = if ua.contains("v2ray") || ua.contains("clash") {
        Format::V2rayBase64
    } else {
        Format::SingBox
    };

    Ok(vpn_subscription::render(&user, &nodes, format))
}

// --- internal --------------------------------------------------------------

#[derive(Deserialize)]
struct CreateUser {
    tg_id: i64,
    username: Option<String>,
}

async fn create_user(
    State(st): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateUser>,
) -> Result<Json<User>, StatusCode> {
    authed(&headers, &st.token)?;
    // `db::provision_user` fills UUID / short-id / password / sub_token and
    // starts the trial window. See migrations/0001_init.sql defaults.
    let user = sqlx::query_as::<_, User>(
        "INSERT INTO users (tg_id, username) VALUES ($1, $2)
         ON CONFLICT (tg_id) DO UPDATE SET username = EXCLUDED.username
         RETURNING *",
    )
    .bind(body.tg_id)
    .bind(body.username)
    .fetch_one(&st.pool)
    .await
    .map_err(internal)?;
    Ok(Json(user))
}

async fn get_user(
    State(st): State<AppState>,
    headers: HeaderMap,
    Path(tg_id): Path<i64>,
) -> Result<Json<User>, StatusCode> {
    authed(&headers, &st.token)?;
    let user = sqlx::query_as::<_, User>("SELECT * FROM users WHERE tg_id = $1")
        .bind(tg_id)
        .fetch_optional(&st.pool)
        .await
        .map_err(internal)?
        .ok_or(StatusCode::NOT_FOUND)?;
    Ok(Json(user))
}

#[derive(Deserialize)]
struct AssignPlan {
    plan_id: Uuid,
}

async fn assign_plan(
    State(st): State<AppState>,
    headers: HeaderMap,
    Path(tg_id): Path<i64>,
    Json(body): Json<AssignPlan>,
) -> Result<StatusCode, StatusCode> {
    authed(&headers, &st.token)?;
    sqlx::query("SELECT apply_plan($1, $2)")
        .bind(tg_id)
        .bind(body.plan_id)
        .execute(&st.pool)
        .await
        .map_err(internal)?;
    Ok(StatusCode::NO_CONTENT)
}

async fn list_nodes(
    State(st): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Vec<Node>>, StatusCode> {
    authed(&headers, &st.token)?;
    let nodes = sqlx::query_as::<_, Node>("SELECT * FROM nodes ORDER BY created_at")
        .fetch_all(&st.pool)
        .await
        .map_err(internal)?;
    Ok(Json(nodes))
}

#[derive(Deserialize)]
struct UpsertNode {
    label: String,
    provider: String,
    region: String,
    ipv4: String,
    reality_dest: Option<String>,
    reality_server_names: Option<Vec<String>>,
    reality_public_key: Option<String>,
    reality_private_key: Option<String>,
    cdn_host: Option<String>,
    status: Option<vpn_core::model::NodeStatus>,
}

async fn upsert_node(
    State(st): State<AppState>,
    headers: HeaderMap,
    Json(n): Json<UpsertNode>,
) -> Result<Json<Node>, StatusCode> {
    authed(&headers, &st.token)?;
    // Orchestrator pushes the row after Terraform apply + agent handshake.
    // Columns left None fall back to schema defaults on insert.
    let saved = sqlx::query_as::<_, Node>(
        "INSERT INTO nodes (label, provider, region, ipv4, reality_dest,
             reality_server_names, reality_public_key, reality_private_key,
             cdn_host, status)
         VALUES ($1,$2,$3,$4,
             COALESCE($5, 'www.microsoft.com:443'),
             COALESCE($6, ARRAY['www.microsoft.com']),
             COALESCE($7, ''), COALESCE($8, ''), $9,
             COALESCE($10, 'warming'))
         ON CONFLICT (label) DO UPDATE SET
             ipv4 = EXCLUDED.ipv4, cdn_host = EXCLUDED.cdn_host,
             status = COALESCE(EXCLUDED.status, nodes.status)
         RETURNING *",
    )
    .bind(n.label)
    .bind(n.provider)
    .bind(n.region)
    .bind(n.ipv4)
    .bind(n.reality_dest)
    .bind(n.reality_server_names.as_deref())
    .bind(n.reality_public_key)
    .bind(n.reality_private_key)
    .bind(n.cdn_host)
    .bind(n.status)
    .fetch_one(&st.pool)
    .await
    .map_err(internal)?;
    Ok(Json(saved))
}

#[derive(Deserialize)]
struct SetStatus {
    status: vpn_core::model::NodeStatus,
}

async fn set_node_status(
    State(st): State<AppState>,
    headers: HeaderMap,
    Path(id): Path<Uuid>,
    Json(body): Json<SetStatus>,
) -> Result<StatusCode, StatusCode> {
    authed(&headers, &st.token)?;
    sqlx::query("UPDATE nodes SET status = $2 WHERE id = $1")
        .bind(id)
        .bind(body.status)
        .execute(&st.pool)
        .await
        .map_err(internal)?;
    Ok(StatusCode::NO_CONTENT)
}

// --- probe fleet ----------------------------------------------------------

async fn probe_targets(State(st): State<AppState>) -> Result<Json<serde_json::Value>, StatusCode> {
    // Public but low-value: the list of node/transport pairs to measure. The
    // probe host maps each to a local proxy listener it already runs.
    let rows = sqlx::query_as::<_, (Uuid,)>(
        "SELECT id FROM nodes WHERE status IN ('active','degraded')",
    )
    .fetch_all(&st.pool)
    .await
    .map_err(internal)?;
    let targets: Vec<serde_json::Value> = rows
        .iter()
        .flat_map(|(id,)| {
            ["vless-reality-tcp", "vless-xhttp", "hysteria2"]
                .iter()
                .map(move |t| serde_json::json!({ "node_id": id, "transport": t, "local_proxy": "" }))
        })
        .collect();
    Ok(Json(serde_json::Value::Array(targets)))
}

async fn probe_report(
    State(st): State<AppState>,
    Json(reports): Json<Vec<vpn_core::model::ProbeReport>>,
) -> Result<StatusCode, StatusCode> {
    for r in reports {
        sqlx::query(
            "INSERT INTO probe_reports (probe_id, node_id, transport, ok, handshake_ms, throughput_kbps, observed_at)
             VALUES ($1,$2,$3,$4,$5,$6,$7)",
        )
        .bind(r.probe_id)
        .bind(r.node_id)
        .bind(r.transport)
        .bind(r.ok)
        .bind(r.handshake_ms.map(|v| v as i32))
        .bind(r.throughput_kbps.map(|v| v as i32))
        .bind(r.observed_at)
        .execute(&st.pool)
        .await
        .map_err(internal)?;
    }
    Ok(StatusCode::NO_CONTENT)
}

async fn recent_reports(
    State(st): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Vec<vpn_core::model::ProbeReport>>, StatusCode> {
    authed(&headers, &st.token)?;
    let rows = sqlx::query_as::<_, vpn_core::model::ProbeReport>(
        "SELECT probe_id, node_id, transport, ok, handshake_ms, throughput_kbps, observed_at
         FROM probe_reports WHERE observed_at > now() - interval '15 minutes'
         ORDER BY observed_at DESC",
    )
    .fetch_all(&st.pool)
    .await
    .map_err(internal)?;
    Ok(Json(rows))
}

fn internal(e: sqlx::Error) -> StatusCode {
    tracing::error!(error = %e, "db error");
    StatusCode::INTERNAL_SERVER_ERROR
}
