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

    # --- Symbols ---
    trading_symbols: str = Field(
        default="XAUUSDm",
        description="Comma-separated symbols to trade, e.g. XAUUSDm,EURUSDm,GBPUSDm,USDJPYm",
    )

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
    max_total_positions: int = Field(default=5, ge=1, le=20, description="Max total positions across ALL symbols")
    capital_floor_pct: float = Field(default=90.0, ge=50.0, le=99.0)
    floating_dd_block_pct: float = Field(default=10.0, ge=1.0, le=50.0)
    breakeven_r_multiple: float = Field(default=1.0, ge=0.5, le=3.0)
    news_block_minutes: int = Field(default=30, ge=5, le=120)

    # --- ATR-Based SL/TP ---
    sl_atr_multiplier: float = Field(default=2.0, ge=1.0, le=5.0, description="ATR multiplier for SL distance")
    tp_atr_multiplier: float = Field(default=3.0, ge=1.5, le=10.0, description="ATR multiplier for TP distance")
    sl_min_distance_points: float = Field(default=300, ge=50, description="Absolute minimum SL distance in points (safety floor)")

    # --- Trailing Stop ---
    trailing_stop_mode: str = Field(default="off", description="off | atr | fixed")
    trailing_atr_multiplier: float = Field(default=3.0, ge=1.0, le=10.0)
    trailing_fixed_points: float = Field(default=0.0, ge=0.0)
    trailing_activation_r: float = Field(default=1.0, ge=0.5, le=5.0)

    # --- Manual Trade Protection ---
    protect_manual_trades: bool = Field(default=True, description="Auto-SL for magic=0 trades")
    manual_sl_pct: float = Field(default=0.02, ge=0.005, le=0.10, description="Emergency SL %")

    # --- API ---
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)

    # --- Frontend ---
    frontend_port: int = Field(default=3000)
    api_base_url: str = Field(default="http://localhost:8000")

    # --- Telegram Notifications ---
    telegram_bot_token: str = Field(default="", description="Telegram Bot Token from @BotFather")
    telegram_chat_id: str = Field(default="", description="Telegram Chat ID to send notifications")
    telegram_enabled: bool = Field(default=True, description="Enable/disable Telegram notifications")

    # --- Logging ---
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")

    # --- Self-Training ---
    training_enabled: bool = Field(default=True, description="Enable self-training system")
    training_interval_hours: float = Field(default=6.0, ge=1.0, le=24.0, description="Hours between training sessions")
    training_candles: int = Field(default=2000, ge=500, le=5000, description="Max candles for training")
    evolution_generations: int = Field(default=5, ge=2, le=20, description="GA generations per evolution")
    evolution_population: int = Field(default=10, ge=4, le=30, description="GA population size")

    # --- Shadow Backtest ---
    shadow_enabled: bool = Field(default=True, description="Run shadow strategies alongside LIVE")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance. Fails fast on invalid config."""
    return Settings()
