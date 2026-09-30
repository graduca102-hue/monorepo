//! Thin wrapper around `deploy/` (Terraform + Ansible).
//!
//! Provider selection rotates across a configured list so we never concentrate
//! nodes in one AS — RKN blocks by subnet, not just IP.

use anyhow::Context;
use tokio::process::Command;

pub struct NewNode {
    pub label: String,
    pub provider: String,
    pub region: String,
    pub ipv4: String,
}

/// Providers we spread across, least-recently-used first. Extend from env.
pub fn provider_rotation() -> Vec<&'static str> {
    vec!["hetzner", "vultr", "greencloud", "aeza", "servarica"]
}

/// Create one VPS and run the node bootstrap playbook. Returns once the
/// node-agent has checked in. `deploy/` must be on PATH-relative `../../deploy`.
pub async fn create(provider: &str, region: &str) -> anyhow::Result<NewNode> {
    let label = format!("n-{}", &uuid::Uuid::new_v4().to_string()[..8]);

    let status = Command::new("bash")
        .arg("deploy/spin_node.sh")
        .arg(provider)
        .arg(region)
        .arg(&label)
        .status()
        .await
        .context("spawning deploy/spin_node.sh")?;
    anyhow::ensure!(status.success(), "spin_node.sh exited {status}");

    let ipv4 = read_output_ip(&label).await?;
    Ok(NewNode {
        label,
        provider: provider.to_string(),
        region: region.to_string(),
        ipv4,
    })
}

async fn read_output_ip(label: &str) -> anyhow::Result<String> {
    let out = Command::new("bash")
        .arg("deploy/node_ip.sh")
        .arg(label)
        .output()
        .await
        .context("deploy/node_ip.sh")?;
    anyhow::ensure!(out.status.success(), "node_ip.sh failed");
    Ok(String::from_utf8(out.stdout)?.trim().to_string())
}

/// Tear down a retired VPS.
pub async fn destroy(label: &str) -> anyhow::Result<()> {
    let status = Command::new("bash")
        .arg("deploy/kill_node.sh")
        .arg(label)
        .status()
        .await
        .context("deploy/kill_node.sh")?;
    anyhow::ensure!(status.success(), "kill_node.sh exited {status}");
    Ok(())
}
