//! Runs on cheap boxes / volunteer machines *inside RF*. This is the early-warning
//! system: it dials each node/transport the way a real client would, measures
//! handshake latency and a short throughput sample, and reports home.
//!
//! It does NOT need the control-plane DB — it pulls a small signed target list
//! from the orchestrator and POSTs `ProbeReport`s back.

use std::time::{Duration, Instant};

use serde::Deserialize;
use time::OffsetDateTime;
use vpn_core::{config, model::{ProbeReport, Transport}};

#[derive(Deserialize)]
struct Target {
    node_id: uuid::Uuid,
    transport: Transport,
    /// A local proxy the probe host already runs (xray/sing-box) pointed at this
    /// endpoint, e.g. "socks5://127.0.0.1:10801". One listener per target.
    local_proxy: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    config::load_dotenv();
    config::init_tracing();

    let probe_id = config::require("PROBE_ID")?;
    let report_url = config::require("PROBE_REPORT_URL")?;
    let targets_url = report_url.replace("/probe/report", "/probe/targets");
    let interval = Duration::from_secs(config::require_parsed("PROBE_INTERVAL_SECS").unwrap_or(60));

    let http = reqwest::Client::builder().timeout(Duration::from_secs(20)).build()?;

    loop {
        match run_round(&http, &probe_id, &targets_url, &report_url).await {
            Ok(n) => tracing::info!(checked = n, "probe round done"),
            Err(e) => tracing::error!(error = %e, "probe round failed"),
        }
        tokio::time::sleep(interval).await;
    }
}

async fn run_round(
    http: &reqwest::Client,
    probe_id: &str,
    targets_url: &str,
    report_url: &str,
) -> anyhow::Result<usize> {
    let targets: Vec<Target> = http.get(targets_url).send().await?.error_for_status()?.json().await?;

    let mut reports = Vec::new();
    for t in &targets {
        reports.push(measure(probe_id, t).await);
    }
    let n = reports.len();
    http.post(report_url).json(&reports).send().await?.error_for_status()?;
    Ok(n)
}

async fn measure(probe_id: &str, t: &Target) -> ProbeReport {
    let proxy = match reqwest::Proxy::all(&t.local_proxy) {
        Ok(p) => p,
        Err(_) => return failed(probe_id, t),
    };
    let client = match reqwest::Client::builder()
        .proxy(proxy)
        .timeout(Duration::from_secs(15))
        .build()
    {
        Ok(c) => c,
        Err(_) => return failed(probe_id, t),
    };

    // 1. handshake / reachability via a 204 endpoint
    let start = Instant::now();
    let reach = client.get("https://www.gstatic.com/generate_204").send().await;
    let handshake_ms = start.elapsed().as_millis() as i32;
    if reach.map(|r| !r.status().is_success()).unwrap_or(true) {
        return failed(probe_id, t);
    }

    // 2. crude throughput sample: time a ~1 MiB download
    let dl_start = Instant::now();
    let bytes = match client
        .get("https://speed.cloudflare.com/__down?bytes=1048576")
        .send()
        .await
    {
        Ok(r) => r.bytes().await.ok(),
        Err(_) => None,
    };
    let throughput_kbps = bytes.map(|b| {
        let secs = dl_start.elapsed().as_secs_f64().max(0.001);
        ((b.len() as f64 * 8.0) / secs / 1000.0) as i32
    });

    ProbeReport {
        probe_id: probe_id.to_string(),
        node_id: t.node_id,
        transport: t.transport,
        ok: true,
        handshake_ms: Some(handshake_ms),
        throughput_kbps,
        observed_at: OffsetDateTime::now_utc(),
    }
}

fn failed(probe_id: &str, t: &Target) -> ProbeReport {
    ProbeReport {
        probe_id: probe_id.to_string(),
        node_id: t.node_id,
        transport: t.transport,
        ok: false,
        handshake_ms: None,
        throughput_kbps: None,
        observed_at: OffsetDateTime::now_utc(),
    }
}
