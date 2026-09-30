//! The control-loop tick: read health, retire, top up warm pool, push configs.

use anyhow::Context;
use reqwest::Client;
use vpn_core::{config, model::{Node, NodeStatus, ProbeReport}};

use crate::{health, provision};

pub struct Ctx {
    pub http: Client,
    pub api_base: String,
    pub api_token: String,
}

impl Ctx {
    pub async fn from_env() -> anyhow::Result<Self> {
        Ok(Self {
            http: Client::builder().build()?,
            api_base: config::require("API_BASE_URL")?,
            api_token: config::require("API_TOKEN")?,
        })
    }

    fn bearer(&self) -> String {
        format!("Bearer {}", self.api_token)
    }
}

pub async fn tick(ctx: &Ctx, min_warm: usize) -> anyhow::Result<()> {
    let nodes: Vec<Node> = ctx
        .http
        .get(format!("{}/nodes", ctx.api_base))
        .header("authorization", ctx.bearer())
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;

    let reports = fetch_recent_reports(ctx).await.unwrap_or_default();

    let mut active = 0usize;
    let mut warm = 0usize;

    for node in &nodes {
        match node.status {
            NodeStatus::Active => active += 1,
            NodeStatus::Warming => warm += 1,
            _ => {}
        }

        let v = health::verdict(node.id, &reports);
        let next = decide(node.status, v.score, v.throttled);
        if next != node.status {
            tracing::warn!(node = %node.label, from = ?node.status, to = ?next, score = v.score, "status change");
            set_status(ctx, node, next).await?;
            if next == NodeStatus::Retiring {
                // promote a warm node in the same tick so users never lose coverage
                if let Some(w) = nodes.iter().find(|n| n.status == NodeStatus::Warming) {
                    set_status(ctx, w, NodeStatus::Active).await?;
                }
            }
        }
    }

    tracing::info!(active, warm, "pool state");

    // Keep the warm pool topped up.
    let mut providers = provision::provider_rotation().into_iter().cycle();
    while warm < min_warm {
        let provider = providers.next().unwrap();
        match provision::create(provider, "eu").await {
            Ok(n) => {
                tracing::info!(node = %n.label, provider, "warmed new node");
                register_node(ctx, n).await?;
                warm += 1;
            }
            Err(e) => {
                tracing::error!(error = %e, provider, "provision failed");
                break;
            }
        }
    }

    push_configs(ctx, &nodes).await?;
    Ok(())
}

/// Hysteresis so a single bad probe window doesn't flap a node.
fn decide(current: NodeStatus, score: i16, throttled: bool) -> NodeStatus {
    match current {
        NodeStatus::Active if score < 25 => NodeStatus::Retiring,
        NodeStatus::Active if score < 60 || throttled => NodeStatus::Degraded,
        NodeStatus::Degraded if score >= 80 && !throttled => NodeStatus::Active,
        NodeStatus::Degraded if score < 20 => NodeStatus::Retiring,
        other => other,
    }
}

async fn fetch_recent_reports(ctx: &Ctx) -> anyhow::Result<Vec<ProbeReport>> {
    Ok(ctx
        .http
        .get(format!("{}/probe/reports?window=10m", ctx.api_base))
        .header("authorization", ctx.bearer())
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?)
}

async fn set_status(ctx: &Ctx, node: &Node, status: NodeStatus) -> anyhow::Result<()> {
    ctx.http
        .post(format!("{}/nodes/{}/status", ctx.api_base, node.id))
        .header("authorization", ctx.bearer())
        .json(&serde_json::json!({ "status": status }))
        .send()
        .await?
        .error_for_status()?;
    Ok(())
}

async fn register_node(ctx: &Ctx, n: provision::NewNode) -> anyhow::Result<()> {
    ctx.http
        .post(format!("{}/nodes", ctx.api_base))
        .header("authorization", ctx.bearer())
        .json(&serde_json::json!({
            "label": n.label,
            "provider": n.provider,
            "region": n.region,
            "ipv4": n.ipv4,
            "status": "warming",
        }))
        .send()
        .await?
        .error_for_status()
        .context("registering node with api")?;
    Ok(())
}

async fn push_configs(_ctx: &Ctx, _nodes: &[Node]) -> anyhow::Result<()> {
    // TODO: for each live node, fetch its active user set from the api, render
    // inbounds via vpn_xray_config::node_inbounds, POST to the node-agent, and
    // trigger a hot reload. Kept as a stub until the agent endpoint lands.
    Ok(())
}
