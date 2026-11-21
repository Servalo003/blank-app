"""Configuration - environment variables only. Secrets are SecretStr and never logged."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_access_token: SecretStr | None = None  # bearer token for the API (required in production)
    cors_origins: str = "http://localhost:5173"
    database_url: str = "sqlite:///./signal_lab.db"
    redis_url: str | None = None
    log_level: str = "INFO"
    git_sha: str = "dev"

    data_provider: Literal["simulator", "deriv"] = "deriv"  # live Deriv public market data
    simulator_seed: int = 7

    # Deriv. Public market data needs NO token. DERIV_API_TOKEN is accepted for
    # forward compatibility but unused by this read-only build: authenticated
    # WebSockets require an OTP issued by an endpoint whose OAuth scope is `trade`,
    # which is broader than this app needs, so it is deliberately not called.
    deriv_app_id: str | None = None
    deriv_api_token: SecretStr | None = None
    deriv_environment: Literal["public"] = "public"
    deriv_ws_url: str = "wss://api.derivws.com/trading/v1/options/ws/public"
    deriv_rate_per_minute: int = 180  # documented shared budget 220/min
    deriv_rate_per_hour: int = 12_000  # documented 14,400/hour

    # Scanning
    scan_delay_seconds: int = 5
    scan_lookback_bars: int = 600
    stale_after_bars: float = 2.0

    # Paper account and risk
    paper_equity: float = 10_000.0
    risk_per_trade_pct: float = 0.5
    max_daily_loss_pct: float = 2.0
    max_open_positions: int = 3
    research_only_alerts: bool = False
    allow_simulated_actionable: bool = False

    # Notifications
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    vapid_public_key: str | None = None
    vapid_private_key: SecretStr | None = None
    vapid_subject: str = "mailto:admin@example.invalid"
    public_app_url: str = "http://localhost:5173"

    @field_validator("deriv_ws_url")
    @classmethod
    def _wss_only(cls, v: str) -> str:
        if not v.startswith("wss://"):
            raise ValueError("DERIV_WS_URL must use wss://")
        return v

    @model_validator(mode="after")
    def _prod_requirements(self) -> Settings:
        if self.app_env == "production":
            tok = self.app_access_token.get_secret_value() if self.app_access_token else ""
            if len(tok) < 24:
                raise ValueError("APP_ACCESS_TOKEN (>= 24 chars) is required in production")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
