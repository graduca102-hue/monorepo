use thiserror::Error;

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Error)]
pub enum Error {
    #[error("database: {0}")]
    Db(#[from] sqlx::Error),

    #[error("config: {0}")]
    Config(String),

    #[error("serialization: {0}")]
    Serde(#[from] serde_json::Error),

    #[error("not found: {0}")]
    NotFound(String),

    #[error("upstream panel: {0}")]
    Panel(String),

    #[error(transparent)]
    Other(#[from] anyhow::Error),
}
