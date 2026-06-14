from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str | None = None
    database_url_sync: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl: int = 300
    rate_limit_enabled: bool = True
    rate_limit_max_requests: int = 1000
    rate_limit_window_seconds: int = 60
    pool_size: int = 10
    max_overflow: int = 5
    log_level: str = "INFO"
    allowed_origins: str = "*"
    request_timeout_seconds: float = 30.0
    query_timeout_seconds: float = 10.0
    api_key: str = ""
    slow_request_threshold_ms: float = 500.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
