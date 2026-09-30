//! Runs on every VPS. Listens on localhost only; the orchestrator reaches it
//! over a WireGuard management link. Responsibilities:
//!   - accept a rendered config bundle, write it, hot-reload xray + hysteria
//!   - expose /healthz with local process + socket checks
//!   - never hold long-term secrets beyond the current active user set

use std::net::SocketAddr;

use axum::{extract::State, http::StatusCode, routing::{get, post}, Json, Router};
use serde_json::Value;
use tokio::process::Command;
use vpn_core::config;

#[derive(Clone)]
struct AgentState {
    token: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    config::load_dotenv();
    config::init_tracing();

    let bind: SocketAddr = config::require_parsed("NODE_AGENT_BIND")?;
    let token = config::require("NODE_AGENT_TOKEN")?;

    let app = Router::new()
        .route("/healthz", get(healthz))
        .route("/apply", post(apply))
        .with_state(AgentState { token });

    tracing::info!(%bind, "node-agent listening");
    let listener = tokio::net::TcpListener::bind(bind).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

async fn healthz() -> Json<Value> {
    let xray = service_active("xray").await;
    let hysteria = service_active("hysteria-server").await;
    Json(serde_json::json!({
        "xray": xray,
        "hysteria": hysteria,
        "ok": xray && hysteria,
    }))
}

/// Body: output of `vpn_xray_config::node_inbounds`.
async fn apply(
    State(st): State<AgentState>,
    headers: axum::http::HeaderMap,
    Json(bundle): Json<Value>,
) -> Result<StatusCode, StatusCode> {
    let ok = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .map(|v| v == st.token)
        .unwrap_or(false);
    if !ok {
        return Err(StatusCode::UNAUTHORIZED);
    }

    if let Some(inbounds) = bundle.get("xray_inbounds") {
        let cfg = serde_json::json!({
            "log": { "loglevel": "warning" },
            "inbounds": inbounds,
            "outbounds": [{ "protocol": "freedom" }]
        });
        write_and_reload("/usr/local/etc/xray/config.json", &cfg, "xray")
            .await
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    }
    if let Some(hy) = bundle.get("hysteria2") {
        write_and_reload("/etc/hysteria/config.json", hy, "hysteria-server")
            .await
            .map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    }
    Ok(StatusCode::NO_CONTENT)
}

async fn write_and_reload(path: &str, cfg: &Value, unit: &str) -> anyhow::Result<()> {
    let tmp = format!("{path}.new");
    tokio::fs::write(&tmp, serde_json::to_vec_pretty(cfg)?).await?;
    tokio::fs::rename(&tmp, path).await?;
    let status = Command::new("systemctl").arg("reload-or-restart").arg(unit).status().await?;
    anyhow::ensure!(status.success(), "reload {unit} failed");
    Ok(())
}

async fn service_active(unit: &str) -> bool {
    Command::new("systemctl")
        .arg("is-active")
        .arg("--quiet")
        .arg(unit)
        .status()
        .await
        .map(|s| s.success())
        .unwrap_or(false)
}
