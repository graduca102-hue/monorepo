//! USDT TRC-20 watcher. Polls a public TRON API for inbound transfers to our
//! receive address, matches them to pending orders by exact amount + memo, and
//! calls the `api` crate to extend the buyer's plan.
//!
//! Telegram Stars are handled inline in `handlers` via `PreCheckoutQuery` /
//! `SuccessfulPayment` and don't need a watcher.

use std::{sync::Arc, time::Duration};

use crate::BotCtx;

pub async fn poll_tron(ctx: Arc<BotCtx>) {
    let address = match std::env::var("USDT_TRON_ADDRESS") {
        Ok(a) if !a.is_empty() => a,
        _ => {
            tracing::warn!("USDT_TRON_ADDRESS unset — TRON watcher disabled");
            return;
        }
    };

    loop {
        if let Err(e) = poll_once(&ctx, &address).await {
            tracing::error!(error = %e, "tron poll failed");
        }
        tokio::time::sleep(Duration::from_secs(30)).await;
    }
}

async fn poll_once(_ctx: &BotCtx, address: &str) -> anyhow::Result<()> {
    // TODO:
    //   GET https://apilist.tronscanapi.com/api/token_trc20/transfers?toAddress={address}
    //   for each confirmed transfer newer than the last cursor:
    //     - find pending order with matching amount (+/- 0 tolerance) and memo
    //     - POST {api_base}/users/{tg_id}/plan { plan_id }
    //     - notify the buyer, advance cursor
    tracing::debug!(%address, "tron poll tick (stub)");
    Ok(())
}
