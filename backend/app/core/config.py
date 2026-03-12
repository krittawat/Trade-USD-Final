"""
Configuration — Schema-validated settings from .env.

All config is loaded once at startup and validated via Pydantic.
No config value is accessed without schema validation.
Fail-fast on missing or invalid config.
"""

from functools import lru_cache
from pathlib import Path
from typing import Optional
import os

from pydantic_settings import BaseSettings
from pydantic import Field, field_validator


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolve_env_file() -> str:
    """
    Resolve which .env file to load based on TRADING_PROFILE env var.
    
    Priority:
        1. TRADING_PROFILE=cent_small → profiles/cent_small.env
        2. Fallback → .env (root)
    """
    project_root = _project_root()
    profile = os.environ.get("TRADING_PROFILE", "").strip()
    
    if profile:
        profile_path = project_root / "profiles" / f"{profile}.env"
        if profile_path.exists():
            return str(profile_path)
        # Fallback: try .env.{profile} in root (legacy)
        legacy_path = project_root / f".env.{profile}"
        if legacy_path.exists():
            return str(legacy_path)
    
    # Default: .env in project root
    return str(project_root / ".env")


class Settings(BaseSettings):
    """Application settings — loaded from .env, validated by Pydantic."""

    # --- Mode ---
    trading_mode: str = Field(default="DRY_RUN", description="LIVE | DRY_RUN | REPLAY | BACKTEST")

    # --- Account Currency ---
    account_currency: str = Field(default="AUTO", description="AUTO | USD | USC (Exness Standard Cent)")
    lot_mode: str = Field(default="AUTO", description="AUTO | STANDARD | CENT")

    @field_validator("trading_mode", mode="before")
    @classmethod
    def strip_trading_mode(cls, v: str) -> str:
        return v.strip() if isinstance(v, str) else v
    
    # --- Dry Run ---
    dry_run_initial_balance: float = Field(default=10000.0, description="Virtual balance for Dry Run")
    dry_run_slippage_points: int = Field(default=5, description="Simulated slippage in points")
    dry_run_latency_ms: int = Field(default=100, description="Simulated execution latency (ms)")

    @field_validator("mt5_login", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str) -> Optional[int]:
        if v == "":
            return None
        return v

    # --- Symbols ---
    trading_symbols: str = Field(
        default="XAUUSDc",
        description="Comma-separated symbols to trade, e.g. XAUUSDc,EURUSDc,GBPUSDc,USDJPYc",
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

    # --- QuestDB removed — tick storage via SQLite ---

    # --- SQLite ---
    sqlite_db_path: str = Field(default=str(Path(__file__).resolve().parents[2] / "data" / "sqlite" / "trading.db"))

    # --- DuckDB ---
    duckdb_db_path: str = Field(default=str(Path(__file__).resolve().parents[2] / "data" / "duckdb" / "analytics.duckdb"))

    @field_validator("sqlite_db_path", "duckdb_db_path", mode="before")
    @classmethod
    def resolve_storage_paths(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            return v
        path = Path(v.strip())
        if path.is_absolute():
            return str(path)
        return str((_project_root() / path).resolve())

    # --- Risk Engine (hard limits) ---
    max_risk_per_trade_pct: float = Field(default=2.0, ge=0.1, le=20.0)
    max_positions_per_symbol: int = Field(default=2, ge=1, le=10)
    max_total_positions: int = Field(default=5, ge=1, le=20, description="Max total positions across ALL symbols")
    capital_floor_pct: float = Field(default=90.0, ge=50.0, le=99.0)
    floating_dd_block_pct: float = Field(default=10.0, ge=1.0, le=50.0)
    floating_dd_block_usd: float = Field(default=1400.0, ge=0.0, description="Absolute Max DD block in account currency (e.g., 1400 for 14 USD in USC account)")
    breakeven_r_multiple: float = Field(default=1.0, ge=0.5, le=3.0)
    news_block_minutes: int = Field(default=30, ge=5, le=120)
    max_allowed_correlation: float = Field(default=0.8, ge=0.5, le=0.99, description="Max Pearson correlation allowed for same-direction trades")
    correlation_window: int = Field(default=60, ge=10, le=200, description="H1 bars to calculate correlation")

    # --- Risk Parity Sizing (Volatility-Targeting) ---
    risk_parity_enabled: bool = Field(default=True, description="Dynamically reduce position size when volatility is high")
    risk_parity_atr_period: int = Field(default=14, ge=5, le=50, description="Period for current ATR calculation")
    risk_parity_baseline_ema: int = Field(default=200, ge=50, le=500, description="Period for baseline ATR EMA to compare against")

    # --- ATR-Based SL/TP ---
    sl_atr_multiplier: float = Field(default=2.0, ge=1.0, le=5.0, description="ATR multiplier for SL distance")
    tp_atr_multiplier: float = Field(default=3.0, ge=1.5, le=10.0, description="ATR multiplier for TP distance")
    sl_min_distance_points: float = Field(default=300, ge=50, description="Absolute minimum SL distance in points (safety floor)")

    # --- Trailing Stop ---
    trailing_stop_mode: str = Field(default="off", description="off | atr | fixed | step | chandelier | adaptive | structure")
    trailing_atr_multiplier: float = Field(default=3.0, ge=1.0, le=10.0)
    trailing_fixed_points: float = Field(default=0.0, ge=0.0)
    trailing_activation_r: float = Field(default=1.0, ge=0.5, le=5.0)
    trailing_step_r: float = Field(default=0.5, ge=0.25, le=2.0, description="Step mode: R increment per SL move")
    trailing_chandelier_period: int = Field(default=14, ge=5, le=50, description="Chandelier lookback N candles")
    trailing_adaptive_min_mult: float = Field(default=1.0, ge=0.5, le=3.0, description="Adaptive: min ATR mult (tightest)")
    trailing_adaptive_max_mult: float = Field(default=3.0, ge=1.5, le=5.0, description="Adaptive: max ATR mult (widest)")
    trailing_adaptive_ramp_r: float = Field(default=3.0, ge=1.0, le=10.0, description="Adaptive: R to reach tightest")
    trailing_structure_fractal_window: int = Field(default=5, ge=3, le=20, description="Structure mode: Fractal window for swings")
    trailing_structure_buffer_points: float = Field(default=1.0, ge=0.5, le=5.0, description="Structure mode: Points buffer from swing")
    trailing_auto_setup: bool = Field(default=True, description="Auto-setup trailing for new positions")

    # --- TP Management ---
    tp_management_mode: str = Field(default="partial", description="off | partial | dynamic | trailing_tp")
    tp_partial_tier1_r: float = Field(default=1.5, ge=0.5, le=5.0, description="Tier 1 R target (User rule +1.5R)")
    tp_partial_tier1_pct: float = Field(default=0.3, ge=0.1, le=1.0, description="Tier 1 close % (User rule 30%)")
    tp_partial_tier2_r: float = Field(default=2.0, ge=1.0, le=10.0, description="Tier 2 R target (User rule +2R)")
    tp_partial_tier2_pct: float = Field(default=0.5, ge=0.1, le=1.0, description="Tier 2 close % (User rule 50%)")
    tp_partial_tier3_r: float = Field(default=3.0, ge=1.5, le=15.0, description="Tier 3 R target")
    tp_partial_tier3_pct: float = Field(default=1.0, ge=0.1, le=1.0, description="Tier 3 close % (1.0=all remaining)")
    tp_dynamic_atr_mult: float = Field(default=3.0, ge=1.0, le=10.0, description="Dynamic TP ATR multiplier")
    tp_trailing_distance_atr: float = Field(default=0.5, ge=0.1, le=3.0, description="Trailing TP buffer ATR multiplier")


    # --- Manual Trade Protection ---
    protect_manual_trades: bool = Field(default=True, description="Auto-SL for magic=0 trades")
    manual_sl_pct: float = Field(default=0.02, ge=0.005, le=0.10, description="Emergency SL % (fallback)")
    manual_tp_rr: float = Field(default=2.0, ge=1.0, le=5.0, description="TP = SL distance × RR ratio for manual trades")

    # --- Position Guardian (auto-enrollment for ALL positions) ---
    guardian_trailing_mode: str = Field(default="step", description="Default trailing mode for auto-enrolled positions: off|atr|fixed|step|chandelier|adaptive|structure")
    guardian_trailing_activation_r: float = Field(default=1.0, ge=0.5, le=5.0, description="R-multiple to start trailing")
    guardian_trailing_step_r: float = Field(default=0.5, ge=0.25, le=2.0, description="Step mode: R increment per SL move")
    guardian_trailing_atr_mult: float = Field(default=2.5, ge=1.0, le=10.0, description="ATR multiplier for trailing SL distance")
    guardian_trailing_structure_fractal_window: int = Field(default=5, ge=3, le=20, description="Guardian Structure mode: Fractal window for swings")
    guardian_trailing_structure_buffer_points: float = Field(default=1.0, ge=0.5, le=5.0, description="Guardian Structure mode: Points buffer from swing")
    guardian_profit_lock_enabled: bool = Field(default=True, description="Enable profit locking tiers")
    guardian_profit_lock_tiers: str = Field(default="1.0:0.0,2.0:0.5,3.0:0.75", description="R:lock_pct pairs (e.g. 2.0:0.5 = at +2R lock 50%)")
    guardian_manual_trailing: bool = Field(default=True, description="Apply trailing + profit-lock to manual trades (magic=0)")

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
    training_interval_hours: float = Field(default=1.0, ge=1.0, le=24.0, description="Hours between training sessions")
    training_timeout_seconds: float = Field(default=3600.0, ge=60.0, le=28800.0, description="Max SECONDS allowed for a full training session before timeout")
    training_candles: int = Field(default=2000, ge=500, le=5000, description="Max candles for training")
    evolution_generations: int = Field(default=5, ge=2, le=20, description="GA generations per evolution")
    evolution_population: int = Field(default=10, ge=4, le=30, description="GA population size")

    # --- Overtrading Protection ---
    cooldown_minutes_after_loss: int = Field(default=5, ge=1, le=30, description="Minutes cooldown after a loss")
    max_consecutive_losses_session: int = Field(default=2, ge=1, le=5, description="Max consecutive losses before session halt")
    max_trades_per_session: int = Field(default=3, ge=1, le=10, description="Max trades per session per symbol")
    max_daily_trades_crypto: int = Field(default=5, ge=1, le=20, description="Max trades per day for crypto assets")
    min_equity_threshold: float = Field(default=50.0, description="Minimum equity required to allow new trades")
    max_daily_loss_pct: float = Field(default=3.0, ge=1.0, le=10.0, description="Max daily loss % before kill-switch")
    daily_target_amount: float = Field(
        default=18.0,
        ge=0.0,
        description="Absolute realized daily profit target in account currency (18 ≈ 600 THB)",
    )
    daily_hard_cap: float = Field(
        default=60.0,
        ge=0.0,
        description="Absolute realized daily hard cap in account currency (60 ≈ 2000 THB)",
    )
    daily_stop_amount: float = Field(
        default=0.0,
        ge=0.0,
        description="Absolute realized daily loss stop in account currency (0=disabled)",
    )
    max_daily_trades_fx: int = Field(default=2, ge=1, le=10, description="Max trades per day for FX pairs")
    max_daily_trades_gold: int = Field(default=1, ge=1, le=5, description="Max trades per day for Gold")

    # --- Regime Filter (Relaxed for All-Weather) ---
    regime_filter_enabled: bool = Field(default=True, description="Enable NO-TRADE regime filter")
    regime_adx_min: float = Field(default=10.0, ge=0.0, le=30.0, description="Min ADX for trading")
    regime_ema_compression_pct: float = Field(default=0.05, ge=0.01, le=1.0, description="Max EMA 9/21/50 spread % for NO-TRADE")
    regime_range_threshold_pct: float = Field(default=0.2, ge=0.1, le=2.0, description="Min 30-bar range % for trading")

    # --- Risk Dampening ---
    risk_dampening_enabled: bool = Field(default=True, description="Dynamic lot reduction on loss streak")
    risk_dampener_loss1_mult: float = Field(default=0.7, ge=0.1, le=1.0, description="Lot multiplier after 1 consecutive loss")
    risk_dampener_loss2_mult: float = Field(default=0.5, ge=0.1, le=1.0, description="Lot multiplier after 2 consecutive losses")
    risk_dampener_loss3_mult: float = Field(default=0.3, ge=0.1, le=1.0, description="Lot multiplier after 3+ consecutive losses (floor)")
    risk_dampener_win_recovery: float = Field(default=0.1, ge=0.05, le=0.3, description="Lot recovery per consecutive win")

    # --- Hedging (Anti-Doi) ---
    hedge_enable: bool = Field(default=False, description="Enable auto-hedging for drawdown protection")
    hedge_threshold_dd_pct: float = Field(default=15.0, ge=1.0, le=50.0, description="Account DD % to trigger hedge")
    hedge_threshold_usd: float = Field(default=0.0, description="Symbol floating loss USD to trigger hedge (0=disabled, use DD%)")
    hedge_ratio: float = Field(default=1.0, ge=0.5, le=2.0, description="Hedge ratio (1.0 = full lock)")

    # --- Shadow Backtest ---
    shadow_enabled: bool = Field(default=True, description="Run shadow strategies alongside LIVE")
    shadow_symbols: str = Field(default="", description="Comma-separated secondary symbols for shadow trading only")

    # --- AI Brain: Internet Learning ---
    sentiment_enabled: bool = Field(default=True, description="Enable sentiment aggregation")
    sentiment_refresh_minutes: int = Field(default=15, ge=5, le=60, description="Sentiment refresh interval")
    fear_greed_enabled: bool = Field(default=True, description="Enable Fear & Greed Index")
    tradingview_enabled: bool = Field(default=True, description="Enable TradingView ratings")
    social_sentiment_enabled: bool = Field(default=True, description="Enable social media sentiment")

    # --- AI Brain: ML Pattern Learning ---
    ml_pattern_enabled: bool = Field(default=True, description="Enable ML pattern recognition")
    ml_retrain_with_training: bool = Field(default=True, description="Retrain ML model during training cycles")
    ml_min_samples: int = Field(default=50, ge=10, le=500, description="Min samples before ML predictions")

    # --- Profile ---
    profile_name: str = Field(default="default", description="Active profile name (set automatically)")

    model_config = {
        "env_file": _resolve_env_file(),
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance. Fails fast on invalid config."""
    import os
    profile = os.environ.get("TRADING_PROFILE", "default")
    settings = Settings()
    # Override profile_name from env var
    object.__setattr__(settings, "profile_name", profile)
    return settings
