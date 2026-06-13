from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/dmo"
    database_url_sync: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/dmo"
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl: int = 300
    rate_limit_enabled: bool = True
    rate_limit_max_requests: int = 1000
    rate_limit_window_seconds: int = 60
    pool_size: int = 20
    max_overflow: int = 10
    log_level: str = "INFO"
    allowed_origins: str = "*"
    request_timeout_seconds: float = 30.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
