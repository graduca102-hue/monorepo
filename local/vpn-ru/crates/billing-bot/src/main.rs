//! Telegram front end. Sells plans, hands out the (stable) subscription URL,
//! and reflects payment state into the `api` crate. It stores nothing itself.
//!
//! Payment rails:
//!   - Telegram Stars (XTR) via native invoices — lowest friction for RU users
//!   - USDT TRC-20 — a watcher (see `payments::poll_tron`) credits confirmed txs
//!
//! Distribution is a bot on purpose: RKN blocks websites faster than bots, and a
//! bot can re-issue links the moment the orchestrator rotates a node.

mod handlers;
mod payments;

use std::sync::Arc;

use teloxide::prelude::*;
use vpn_core::config;

pub struct BotCtx {
    pub http: reqwest::Client,
    pub api_base: String,
    pub api_token: String,
    pub sub_base: String,
    pub owner_id: i64,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    config::load_dotenv();
    config::init_tracing();

    let bot = Bot::new(config::require("BOT_TOKEN")?);
    let ctx = Arc::new(BotCtx {
        http: reqwest::Client::new(),
        api_base: config::require("API_BASE_URL")?,
        api_token: config::require("API_TOKEN")?,
        sub_base: config::require("SUB_BASE_URL")?,
        owner_id: config::require_parsed("BOT_OWNER_ID")?,
    });

    // Start the TRON payment watcher alongside the dispatcher.
    tokio::spawn(payments::poll_tron(ctx.clone()));

    tracing::info!("billing-bot up");
    handlers::dispatch(bot, ctx).await;
    Ok(())
}
