//! Turn raw probe reports into a rolling per-node health score.

use time::{Duration, OffsetDateTime};
use uuid::Uuid;
use vpn_core::model::ProbeReport;

/// Weighted verdict for one node over a recent window of probe reports.
pub struct Verdict {
    pub node_id: Uuid,
    pub score: i16,
    /// True when a throttling pattern is seen: connects fine, throughput tanks.
    pub throttled: bool,
}

const WINDOW: Duration = Duration::minutes(10);

pub fn verdict(node_id: Uuid, reports: &[ProbeReport]) -> Verdict {
    let now = OffsetDateTime::now_utc();
    let recent: Vec<&ProbeReport> = reports
        .iter()
        .filter(|r| r.node_id == node_id && now - r.observed_at < WINDOW)
        .collect();

    if recent.is_empty() {
        return Verdict { node_id, score: 50, throttled: false };
    }

    let ok = recent.iter().filter(|r| r.ok).count();
    let score = ((ok * 100) / recent.len()) as i16;

    let connects = recent.iter().filter(|r| r.ok).count();
    let fast = recent
        .iter()
        .filter(|r| r.throughput_kbps.map(|k| k >= 2_000).unwrap_or(false))
        .count();
    let throttled = connects >= 3 && fast * 2 < connects;

    Verdict { node_id, score, throttled }
}
