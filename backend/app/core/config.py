"""
Configuration — Schema-validated settings from .env.

All config is loaded once at startup and validated via Pydantic.
No config value is accessed without schema validation.
Fail-fast on missing or invalid config.
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Application settings — loaded from .env, validated by Pydantic."""

    # --- Mode ---
    trading_mode: str = Field(default="DRY_RUN", description="LIVE | DRY_RUN | REPLAY | BACKTEST")

    # --- MT5 ---
    mt5_login: Optional[int] = Field(default=None, description="MT5 account number")
    mt5_password: Optional[str] = Field(default=None, description="MT5 password")
    mt5_server: str = Field(default="Exness-MT5Real", description="MT5 broker server")
    mt5_path: str = Field(
        default=r"C:\Program Files\MetaTrader 5\terminal64.exe",
        description="Path to MT5 terminal",
    )
    mt5_timeout: int = Field(default=10000, description="MT5 connection timeout (ms)")

    # --- QuestDB ---
    questdb_host: str = Field(default="localhost")
    questdb_http_port: int = Field(default=9000)
    questdb_ilp_port: int = Field(default=9009)
    questdb_pg_port: int = Field(default=8812)
    questdb_pg_user: str = Field(default="admin")
    questdb_pg_password: str = Field(default="quest")

    # --- SQLite ---
    sqlite_db_path: str = Field(default="backend/data/sqlite/trading.db")

    # --- DuckDB ---
    duckdb_db_path: str = Field(default="backend/data/duckdb/analytics.duckdb")

    # --- Risk Engine (hard limits) ---
    max_risk_per_trade_pct: float = Field(default=2.0, ge=0.1, le=5.0)
    max_positions_per_symbol: int = Field(default=2, ge=1, le=10)
    capital_floor_pct: float = Field(default=90.0, ge=50.0, le=99.0)
    floating_dd_block_pct: float = Field(default=10.0, ge=1.0, le=50.0)
    breakeven_r_multiple: float = Field(default=1.0, ge=0.5, le=3.0)
    news_block_minutes: int = Field(default=30, ge=5, le=120)

    # --- API ---
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)

    # --- Frontend ---
    frontend_port: int = Field(default=3000)
    api_base_url: str = Field(default="http://localhost:8000")

    # --- Logging ---
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance. Fails fast on invalid config."""
    return Settings()
