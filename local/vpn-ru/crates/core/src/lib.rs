//! Shared domain types, config loading and errors for the vpn-ru control plane.

pub mod config;
pub mod error;
pub mod model;

pub use error::{Error, Result};

/// Connect to Postgres and run migrations bundled in the `api` crate.
pub async fn db_pool(database_url: &str) -> Result<sqlx::PgPool> {
    let pool = sqlx::postgres::PgPoolOptions::new()
        .max_connections(10)
        .connect(database_url)
        .await?;
    Ok(pool)
}
