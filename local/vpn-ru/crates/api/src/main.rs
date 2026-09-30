//! Internal REST API — the single source of truth for users, plans and nodes.
//! Only other services in this workspace talk to it, over `API_TOKEN`.

mod routes;

use std::net::SocketAddr;

use vpn_core::config;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    config::load_dotenv();
    config::init_tracing();

    let database_url = config::require("DATABASE_URL")?;
    let bind: SocketAddr = config::require_parsed("API_BIND")?;
    let token = config::require("API_TOKEN")?;

    let pool = vpn_core::db_pool(&database_url).await?;
    sqlx::migrate!("../../migrations").run(&pool).await?;

    let app = routes::router(routes::AppState { pool, token });

    tracing::info!(%bind, "vpn-api listening");
    let listener = tokio::net::TcpListener::bind(bind).await?;
    axum::serve(listener, app).await?;
    Ok(())
}
