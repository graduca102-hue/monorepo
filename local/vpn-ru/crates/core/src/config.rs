//! Minimal env-based config helpers. Each binary reads only what it needs.

use crate::{Error, Result};

pub fn require(key: &str) -> Result<String> {
    std::env::var(key).map_err(|_| Error::Config(format!("missing env var {key}")))
}

pub fn optional(key: &str) -> Option<String> {
    std::env::var(key).ok()
}

pub fn require_parsed<T>(key: &str) -> Result<T>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    let raw = require(key)?;
    raw.parse::<T>()
        .map_err(|e| Error::Config(format!("bad value for {key}: {e}")))
}

/// Load `.env` if present. No-op when the file is missing.
pub fn load_dotenv() {
    let _ = dotenvy::dotenv();
}

pub fn init_tracing() {
    use tracing_subscriber::{fmt, EnvFilter};
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    let _ = fmt().with_env_filter(filter).try_init();
}
