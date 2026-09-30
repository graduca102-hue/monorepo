//! The survivability loop.
//!
//! Every tick:
//!   1. pull probe + agent health into a per-node score
//!   2. mark nodes Degraded / Retiring when the score or throughput collapses
//!   3. keep at least `MIN_WARM` warmed spare nodes ready
//!   4. promote a warm node whenever an active one retires
//!   5. push the refreshed user set to every live node's agent
//!
//! Actual VPS creation is delegated to `deploy/` (Terraform + Ansible); this crate
//! only shells out to it and records the result via the `api` crate.

mod health;
mod provision;
mod rotation;

use std::time::Duration;

use clap::Parser;
use vpn_core::config;

#[derive(Parser)]
struct Cli {
    /// Seconds between control-loop ticks.
    #[arg(long, env = "ORCH_TICK_SECS", default_value_t = 30)]
    tick_secs: u64,
    /// Minimum number of warmed spare nodes to keep on hand.
    #[arg(long, env = "ORCH_MIN_WARM", default_value_t = 2)]
    min_warm: usize,
    /// Run one tick and exit (for cron / testing).
    #[arg(long)]
    once: bool,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    config::load_dotenv();
    config::init_tracing();
    let cli = Cli::parse();

    let ctx = rotation::Ctx::from_env().await?;

    loop {
        if let Err(e) = rotation::tick(&ctx, cli.min_warm).await {
            tracing::error!(error = %e, "control-loop tick failed");
        }
        if cli.once {
            break;
        }
        tokio::time::sleep(Duration::from_secs(cli.tick_secs)).await;
    }
    Ok(())
}
