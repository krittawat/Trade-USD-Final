"""
XAG (Silver) Strategy Tournament — Train Bot to Find the Best Silver Strategy (USC Mode)
=========================================================================================
Runs a comprehensive tournament across multiple Silver-capable strategies and parameter
sets using M5 data from MT5. Saves the best configuration to brain.db.

Strategies Tested:
  1. GoldScalpPro     — Institutional VWAP + SuperTrend (adapted for Silver)
  2. GoldScalpWR60    — High Win-Rate variant
  3. SniperPro        — HTF-Confirmed Pullback (has native XAG config)
  4. SmartFusion      — MACD + FVG + VSA (auto-tunes for Silver)
  5. GoldSniperMini   — Fast EMA Cross Sniper (has XAG tuning)
  6. OmniscientOracle — SMC Order Blocks + FVG (has XAGUSD tuning)

Usage:
    python backend/scripts/train_xag_best.py
    python backend/scripts/train_xag_best.py --days 200 --equity 500
"""

import sys
import os
import time
import json
import logging
import random
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional
import pandas as pd
import numpy as np

# Adjust path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Initialize MT5
import MetaTrader5 as mt5

# Project imports
from app.core.logging import get_logger
from app.execution.backtester import Backtester, BacktestResult
from app.brain.memory_store import MemoryStore
from app.strategy.templates.gold_scalp_pro import GoldScalpProStrategy
from app.strategy.templates.gold_scalp_wr60 import GoldScalpWR60Strategy
from app.strategy.templates.sniper_pro import SniperProStrategy
from app.strategy.templates.smart_fusion import SmartFusionStrategy
from app.strategy.templates.gold_sniper_mini import GoldSniperMiniStrategy
from app.strategy.templates.btc_ultimate_strategy import OmniscientOracleStrategy
from app.strategy.templates.silver_mean_rev import SilverMeanRevStrategy
from scripts.backtest_full import FullFeatureBacktester

# ─── Logging: Console Only ───
logging.getLogger().handlers = []
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
logging.getLogger().addHandler(handler)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("TrainXAGBest")

# Remove file handlers to avoid PermissionError
def remove_file_handlers():
    for name in logging.root.manager.loggerDict:
        lg = logging.getLogger(name)
        to_remove = [h for h in lg.handlers if isinstance(h, (logging.FileHandler, logging.handlers.RotatingFileHandler))]
        for h in to_remove:
            lg.removeHandler(h)

remove_file_handlers()

# Monkey-patch to prevent file handlers from re-appearing
import app.core.logging
_orig_get_logger = app.core.logging.get_logger
def _patched_get_logger(name, level="INFO"):
    lg = _orig_get_logger(name, level)
    for h in [h for h in lg.handlers if isinstance(h, (logging.FileHandler, logging.handlers.RotatingFileHandler))]:
        lg.removeHandler(h)
    return lg
app.core.logging.get_logger = _patched_get_logger

# Suppress noisy loggers
logging.getLogger("app.core.logging").setLevel(logging.WARNING)
logging.getLogger("app.risk").setLevel(logging.WARNING)

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION — XAG (Silver) Specs
# ═══════════════════════════════════════════════════════════════════

SYMBOL = "XAGUSDc"          # USC (Cent) account symbol for Silver
TIMEFRAME = mt5.TIMEFRAME_M5
CONTRACT_SIZE = 50.0        # Exness Cent: 50 oz per lot (NOT standard 5000)
POINT_VALUE = 0.001
DIGITS = 3

# ═══════════════════════════════════════════════════════════════════
# STRATEGY + PARAM GRID — COMPOUND GROWTH FOCUS
# ═══════════════════════════════════════════════════════════════════
# เป้าหมาย: เทรดบ่อย + กำไรเล็กๆ สม่ำเสมอ + ปล่อย compound
# Silver ATR ≈ $0.10-0.30 (M5), ราคา ~$32
# MIN_SL_DISTANCE ต้องต่ำสำหรับ Silver (ไม่ใช่ Gold scale)

CONTESTANTS: List[Dict[str, Any]] = [
    # ─────────────────────────────────────────────────────────────
    # GoldScalpPro — Active Compound Variants
    # (ลด ADX threshold, ลด SuperTrend period, SL/TP แคบ)
    # ─────────────────────────────────────────────────────────────
    {
        "strategy_class": "GoldScalpPro",
        "label": "COMPOUND_SCALP_V1",
        "params": {
            "SL_ATR_MULT": 1.5, "TP_ATR_MULT": 1.2, "MIN_SL_DISTANCE": 0.08,
            "SUPERTREND_LEN": 5, "SUPERTREND_MUL": 1.5,
            "RSI_PERIOD": 10, "ADX_PERIOD": 14, "ATR_PERIOD": 10,
            "CHANDELIER_MULT": 1.2, "PROFIT_LOCK_1": 0.3, "PROFIT_LOCK_2": 0.6,
        }
    },
    {
        "strategy_class": "GoldScalpPro",
        "label": "COMPOUND_SCALP_V2",
        "params": {
            "SL_ATR_MULT": 1.2, "TP_ATR_MULT": 1.0, "MIN_SL_DISTANCE": 0.05,
            "SUPERTREND_LEN": 5, "SUPERTREND_MUL": 1.2,
            "RSI_PERIOD": 8, "ADX_PERIOD": 10, "ATR_PERIOD": 10,
            "CHANDELIER_MULT": 1.0, "PROFIT_LOCK_1": 0.2, "PROFIT_LOCK_2": 0.4,
        }
    },
    {
        "strategy_class": "GoldScalpPro",
        "label": "COMPOUND_SCALP_V3",
        "params": {
            "SL_ATR_MULT": 1.8, "TP_ATR_MULT": 1.5, "MIN_SL_DISTANCE": 0.10,
            "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0,
            "RSI_PERIOD": 10, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
            "CHANDELIER_MULT": 1.5, "PROFIT_LOCK_1": 0.4, "PROFIT_LOCK_2": 0.8,
        }
    },
    {
        "strategy_class": "GoldScalpPro",
        "label": "COMPOUND_CASHCOW",
        "params": {
            # Cash Cow TP แคบมาก = ปิดเร็ว WR สูง
            "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 0.8, "MIN_SL_DISTANCE": 0.08,
            "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 1.5,
            "RSI_PERIOD": 10, "ADX_PERIOD": 10, "ATR_PERIOD": 10,
            "CHANDELIER_MULT": 1.0, "PROFIT_LOCK_1": 0.2, "PROFIT_LOCK_2": 0.4,
        }
    },
    {
        "strategy_class": "GoldScalpPro",
        "label": "COMPOUND_BALANCED",
        "params": {
            "SL_ATR_MULT": 1.5, "TP_ATR_MULT": 2.0, "MIN_SL_DISTANCE": 0.10,
            "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0,
            "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
            "CHANDELIER_MULT": 1.5, "PROFIT_LOCK_1": 0.5, "PROFIT_LOCK_2": 1.0,
        }
    },
    {
        "strategy_class": "GoldScalpPro",
        "label": "COMPOUND_ULTRA_FAST",
        "params": {
            # Ultra fast: จับ micro-moves
            "SL_ATR_MULT": 1.0, "TP_ATR_MULT": 0.7, "MIN_SL_DISTANCE": 0.05,
            "SUPERTREND_LEN": 3, "SUPERTREND_MUL": 1.0,
            "RSI_PERIOD": 7, "ADX_PERIOD": 7, "ATR_PERIOD": 7,
            "CHANDELIER_MULT": 0.8, "PROFIT_LOCK_1": 0.15, "PROFIT_LOCK_2": 0.3,
        }
    },

    # ─────────────────────────────────────────────────────────────
    # GoldScalpWR60 — Compound WR Focus (TP แคบ = WR สูง)
    # ─────────────────────────────────────────────────────────────
    {
        "strategy_class": "GoldScalpWR60",
        "label": "WR60_COMPOUND_TIGHT",
        "params": {
            "SUPERTREND_LEN": 5, "SUPERTREND_MUL": 1.5,
            "RSI_PERIOD": 10, "ADX_PERIOD": 10, "ATR_PERIOD": 10,
            "SL_ATR_MULT": 1.2, "TP_ATR_MULT": 0.8,
            "MIN_SL_DISTANCE": 0.05,
        }
    },
    {
        "strategy_class": "GoldScalpWR60",
        "label": "WR60_COMPOUND_NORM",
        "params": {
            "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0,
            "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
            "SL_ATR_MULT": 1.5, "TP_ATR_MULT": 1.0,
            "MIN_SL_DISTANCE": 0.08,
        }
    },
    {
        "strategy_class": "GoldScalpWR60",
        "label": "WR60_COMPOUND_WIDE",
        "params": {
            "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5,
            "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
            "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 1.5,
            "MIN_SL_DISTANCE": 0.10,
        }
    },
    {
        "strategy_class": "GoldScalpWR60",
        "label": "WR60_QUICKWIN",
        "params": {
            # Quick Win: TP น้อยมาก ปิดเร็ว compound ได้บ่อย
            "SUPERTREND_LEN": 5, "SUPERTREND_MUL": 1.2,
            "RSI_PERIOD": 8, "ADX_PERIOD": 8, "ATR_PERIOD": 8,
            "SL_ATR_MULT": 1.0, "TP_ATR_MULT": 0.6,
            "MIN_SL_DISTANCE": 0.04,
        }
    },

    # ─────────────────────────────────────────────────────────────
    # SmartFusion — Active Compound (lower ADX = more signals)
    # ─────────────────────────────────────────────────────────────
    {
        "strategy_class": "SmartFusion",
        "label": "FUSION_COMPOUND_DEF",
        "params": {
            "adx_threshold": 15,
            "sl_atr_mult": 1.5,
            "tp_atr_mult": 2.0,
        }
    },
    {
        "strategy_class": "SmartFusion",
        "label": "FUSION_COMPOUND_AGG",
        "params": {
            "adx_threshold": 12,
            "sl_atr_mult": 1.2,
            "tp_atr_mult": 1.5,
        }
    },

    # ─────────────────────────────────────────────────────────────
    # GoldSniperMini — Active Silver
    # ─────────────────────────────────────────────────────────────
    {
        "strategy_class": "GoldSniperMini",
        "label": "MINI_COMPOUND_DEF",
        "params": {
            "_sl_mult": 1.5, "_tp_mult": 2.0, "_adx_min": 18,
        }
    },
    {
        "strategy_class": "GoldSniperMini",
        "label": "MINI_COMPOUND_FAST",
        "params": {
            "_sl_mult": 1.2, "_tp_mult": 1.5, "_adx_min": 15,
        }
    },

    # ─────────────────────────────────────────────────────────────
    # SniperPro — Active XAG
    # ─────────────────────────────────────────────────────────────
    {
        "strategy_class": "SniperPro",
        "label": "SNIPER_COMPOUND_DEF",
        "params": {
            "_override_config": {
                "rsi_buy": 38, "rsi_sell": 62,
                "stoch_k": 25, "bb_dev": 1.8,
                "adx": 18,
                "sl_mult": 1.5, "tp_mult": 2.5,
                "min_conf": 65,
            }
        }
    },
    {
        "strategy_class": "SniperPro",
        "label": "SNIPER_COMPOUND_AGG",
        "params": {
            "_override_config": {
                "rsi_buy": 40, "rsi_sell": 60,
                "stoch_k": 30, "bb_dev": 1.5,
                "adx": 15,
                "sl_mult": 1.2, "tp_mult": 2.0,
                "min_conf": 55,
            }
        }
    },

    # ─────────────────────────────────────────────────────────────
    # OmniscientOracle — Active Silver
    # ─────────────────────────────────────────────────────────────
    {
        "strategy_class": "OmniscientOracle",
        "label": "ORACLE_COMPOUND",
        "params": {}
    },

    # ─────────────────────────────────────────────────────────────
    # SilverMeanRev — BB + RSI Mean-Reversion (XAG-SPECIFIC)
    # ─────────────────────────────────────────────────────────────
    {
        "strategy_class": "SilverMeanRev",
        "label": "SILVER_MR_DEFAULT",
        "params": {
            "BB_LEN": 20, "BB_STD": 2.0, "RSI_PERIOD": 14,
            "RSI_BUY": 35, "RSI_SELL": 65,
            "EMA_PERIOD": 50, "ADX_MAX": 30, "ADX_PERIOD": 14,
            "SL_ATR_MULT": 1.2, "TP_USE_BB_MID": True,
            "ATR_PERIOD": 14, "MIN_SL_DISTANCE": 0.05,
            "MIN_CONFIDENCE": 55,
        }
    },
    {
        "strategy_class": "SilverMeanRev",
        "label": "SILVER_MR_AGGRESSIVE",
        "params": {
            # ลด threshold = เทรดบ่อยขึ้น
            "BB_LEN": 20, "BB_STD": 1.8, "RSI_PERIOD": 10,
            "RSI_BUY": 40, "RSI_SELL": 60,
            "EMA_PERIOD": 50, "ADX_MAX": 35, "ADX_PERIOD": 14,
            "SL_ATR_MULT": 1.0, "TP_USE_BB_MID": True,
            "ATR_PERIOD": 10, "MIN_SL_DISTANCE": 0.04,
            "MIN_CONFIDENCE": 45,
        }
    },
    {
        "strategy_class": "SilverMeanRev",
        "label": "SILVER_MR_TIGHT_BB",
        "params": {
            # BB std ต่ำ = bands แคบ = trigger ง่ายขึ้น
            "BB_LEN": 15, "BB_STD": 1.5, "RSI_PERIOD": 10,
            "RSI_BUY": 38, "RSI_SELL": 62,
            "EMA_PERIOD": 40, "ADX_MAX": 30, "ADX_PERIOD": 10,
            "SL_ATR_MULT": 1.0, "TP_USE_BB_MID": True,
            "ATR_PERIOD": 10, "MIN_SL_DISTANCE": 0.04,
            "MIN_CONFIDENCE": 50,
        }
    },
    {
        "strategy_class": "SilverMeanRev",
        "label": "SILVER_MR_CONSERVATIVE",
        "params": {
            # RSI extreme = เทรดน้อยแต่แม่นยำ
            "BB_LEN": 20, "BB_STD": 2.2, "RSI_PERIOD": 14,
            "RSI_BUY": 30, "RSI_SELL": 70,
            "EMA_PERIOD": 50, "ADX_MAX": 25, "ADX_PERIOD": 14,
            "SL_ATR_MULT": 1.5, "TP_USE_BB_MID": True,
            "ATR_PERIOD": 14, "MIN_SL_DISTANCE": 0.08,
            "MIN_CONFIDENCE": 60,
        }
    },
    {
        "strategy_class": "SilverMeanRev",
        "label": "SILVER_MR_ATR_TP",
        "params": {
            # TP ใช้ ATR แทน BB Mid (อาจได้กำไรมากกว่า)
            "BB_LEN": 20, "BB_STD": 2.0, "RSI_PERIOD": 14,
            "RSI_BUY": 35, "RSI_SELL": 65,
            "EMA_PERIOD": 50, "ADX_MAX": 30, "ADX_PERIOD": 14,
            "SL_ATR_MULT": 1.2, "TP_USE_BB_MID": False,
            "TP_ATR_MULT": 2.0,
            "ATR_PERIOD": 14, "MIN_SL_DISTANCE": 0.05,
            "MIN_CONFIDENCE": 55,
        }
    },
]


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def create_strategy(class_name: str):
    """Instantiate a strategy by class name."""
    if class_name == "GoldScalpPro":
        return GoldScalpProStrategy()
    elif class_name == "GoldScalpWR60":
        return GoldScalpWR60Strategy()
    elif class_name == "SniperPro":
        return SniperProStrategy()
    elif class_name == "SmartFusion":
        return SmartFusionStrategy()
    elif class_name == "GoldSniperMini":
        return GoldSniperMiniStrategy()
    elif class_name == "OmniscientOracle":
        return OmniscientOracleStrategy()
    elif class_name == "SilverMeanRev":
        return SilverMeanRevStrategy()
    else:
        raise ValueError(f"Unknown strategy class: {class_name}")


def apply_params(strategy, params: dict):
    """Apply parameter dict to strategy instance."""
    for key, value in params.items():
        if key.startswith("_"):
            continue  # Skip meta-params (handled per-strategy)
        if hasattr(strategy, key):
            setattr(strategy, key, value)


class UniversalAdapter:
    """
    Universal adapter to make ANY strategy compatible with FullFeatureBacktester.
    
    FullFeatureBacktester calls:
      1. strategy.analyze(history, symbol, regime_context=regime)  # Gold-style
      2. strategy.analyze(history, profile, regime.regime)          # Forex-style fallback
    
    This adapter normalizes those calls to the strategy's actual interface.
    """
    def __init__(self, inner_strategy, adapter_type: str, symbol: str = "XAGUSDc", 
                 config_override: Optional[dict] = None):
        self.inner = inner_strategy
        self.adapter_type = adapter_type
        self.symbol = symbol
        self.config_override = config_override
        self.name = getattr(inner_strategy, 'name', adapter_type)

    def get_status(self):
        if hasattr(self.inner, 'get_status'):
            return self.inner.get_status()
        return {"name": self.name}

    def analyze(self, df: pd.DataFrame, symbol_or_profile=None, **kwargs):
        """
        Normalize the backtester's call to the inner strategy's interface.
        Handles: regime_context kwarg, SymbolProfile positional args, etc.
        """
        from app.strategy.templates.base_strategy import StrategyDecision

        # Determine actual symbol string
        symbol = self.symbol
        if isinstance(symbol_or_profile, str):
            symbol = symbol_or_profile
        
        # Set symbol in df.attrs for strategies that use it
        df.attrs['symbol'] = symbol

        try:
            if self.adapter_type == "SniperPro":
                return self._call_sniper(df, symbol)
            elif self.adapter_type == "SmartFusion":
                return self.inner.analyze(df, direction="AUTO")
            elif self.adapter_type == "GoldSniperMini":
                return self.inner.analyze(df, direction="AUTO")
            elif self.adapter_type == "OmniscientOracle":
                return self.inner.analyze(df=df, symbol=symbol)
            else:
                # Generic: try Gold-style first, then simple
                try:
                    return self.inner.analyze(df, symbol, **kwargs)
                except TypeError:
                    return self.inner.analyze(df)
        except Exception as e:
            return StrategyDecision(signal="WAIT", reason=f"Adapter error: {e}")

    def _call_sniper(self, df: pd.DataFrame, symbol: str):
        """Handle SniperPro's unique SniperSignal return type."""
        from app.strategy.templates.base_strategy import StrategyDecision

        # Apply config override if provided
        original_config = None
        if self.config_override:
            original_config = self.inner.CONFIGS.get("XAG", {}).copy()
            self.inner.CONFIGS["XAG"] = self.config_override

        try:
            result = self.inner.analyze(df)
        finally:
            if original_config is not None:
                self.inner.CONFIGS["XAG"] = original_config

        # Convert SniperSignal → StrategyDecision
        signal_str = result.signal.value if hasattr(result.signal, 'value') else str(result.signal)

        if signal_str in ("BUY", "SELL"):
            return StrategyDecision(
                signal=signal_str,
                entry_price=result.entry_price,
                sl=result.stop_loss,
                tp=result.take_profit,
                reason=", ".join(result.reasons),
                confidence=result.confidence / 100.0,
                stop_loss=result.stop_loss,
                take_profit=result.take_profit,
            )
        else:
            return StrategyDecision(
                signal="WAIT",
                reason=", ".join(result.reasons) if result.reasons else "No setup",
            )


def fetch_data(symbol: str, days: int) -> pd.DataFrame:
    """Fetch M5 data from MT5."""
    if not mt5.initialize():
        logger.error("MT5 Initialization failed")
        return None

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=days)

    logger.info(f"Fetching {days} days of M5 data for {symbol}...")
    rates = mt5.copy_rates_range(symbol, TIMEFRAME, start_date, end_date)

    if rates is None or len(rates) == 0:
        logger.error(f"No data fetched for {symbol}")
        mt5.shutdown()
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    logger.info(f"Fetched {len(df):,} candles ({df['time'].min()} → {df['time'].max()})")

    mt5.shutdown()
    return df


def calculate_daily_stats(trades_df: pd.DataFrame) -> Dict[str, float]:
    """Calculate daily win rate and consistency score."""
    if trades_df.empty:
        return {"daily_wr": 0.0, "consistency": 0.0, "avg_daily_pnl": 0.0}

    if not pd.api.types.is_datetime64_any_dtype(trades_df.get('exit_time', pd.Series())):
        trades_df['exit_time'] = pd.to_datetime(trades_df['exit_time'], errors='coerce')

    pnl_col = 'pnl' if 'pnl' in trades_df.columns else 'profit_usd'
    if pnl_col not in trades_df.columns:
        return {"daily_wr": 0.0, "consistency": 0.0, "avg_daily_pnl": 0.0}

    daily_pnl = trades_df.groupby(trades_df['exit_time'].dt.date)[pnl_col].sum()

    total_days = len(daily_pnl)
    winning_days = (daily_pnl > 0).sum()
    daily_wr = (winning_days / total_days * 100) if total_days > 0 else 0.0

    avg_daily = daily_pnl.mean()
    std_daily = daily_pnl.std()
    consistency = (avg_daily / std_daily) if std_daily > 0 else 0.0

    return {"daily_wr": daily_wr, "consistency": consistency, "avg_daily_pnl": avg_daily}


def compute_score(result: BacktestResult, daily_stats: Dict[str, float]) -> float:
    """
    Compound Growth Score — ให้น้ำหนัก:
    1. จำนวนเทรด/วัน (compound ต้องเทรดบ่อย)
    2. Expectancy ต่อเทรด (กำไรเฉลี่ย)
    3. Win Rate สูง (ลด drawdown)
    4. Consistency (Daily PnL สม่ำเสมอ)
    5. Drawdown ต่ำ (รักษาทุน)
    """
    if result.total_profit_usd < 0:
        return -100.0  # ขาดทุน = ตกรอบ

    if result.total_trades < 30:
        return -50.0   # น้อยเกินไปสำหรับ compound

    score = 0.0
    
    # --- 1. เทรด/วัน (Compound ต้องมีจำนวนเทรดเพียงพอ) ---
    # เป้า: 1-5 เทรด/วัน (250 trading days/year = 250-1250 trades)
    trades_per_day = result.total_trades / 250.0
    if trades_per_day >= 1.0:
        score += min(trades_per_day, 5.0) * 15  # max 75 pts
    else:
        score += trades_per_day * 10  # penalty for too few
    
    # --- 2. Expectancy ต่อเทรด (USC) ---
    expectancy = result.total_profit_usd / max(result.total_trades, 1)
    if expectancy > 0:
        score += min(expectancy * 5, 40)  # max 40 pts
    else:
        score -= 20
    
    # --- 3. Win Rate (สำคัญมากสำหรับ compound) ---
    if result.win_rate >= 60:
        score += (result.win_rate - 50) * 2.0  # bonus per % above 50
    elif result.win_rate >= 50:
        score += (result.win_rate - 50) * 1.0
    else:
        score -= (50 - result.win_rate) * 1.5  # penalty below 50%
    
    # --- 4. Profit Factor ---
    score += min(result.profit_factor, 3.0) * 15  # max 45 pts
    
    # --- 5. Daily Consistency (Sharpe-like) ---
    consistency = daily_stats.get("consistency", 0.0)
    score += min(consistency * 20, 30)  # max 30 pts
    
    # --- 6. Daily Win Rate ---
    daily_wr = daily_stats.get("daily_wr", 0.0)
    score += daily_wr * 0.5  # max 50 pts
    
    # --- 7. Total Profit (compound growth) ---
    score += min(result.total_profit_usd / 50.0, 40)  # max 40 pts
    
    # --- 8. Drawdown Penalty ---
    score -= result.max_drawdown_pct * 3.0  # heavy penalty
    
    return round(score, 2)


def save_to_brain(label: str, strategy_class: str, params: dict, result: BacktestResult, daily_stats: dict, score: float):
    """Save the best configuration to brain.db."""
    try:
        store = MemoryStore()
        store.connect()

        # Map strategy class to brain key
        class_to_name = {
            "GoldScalpPro": "gold_scalp_pro",
            "GoldScalpWR60": "gold_scalp_wr60",
            "SniperPro": "sniper_pro",
            "SmartFusion": "smart_fusion",
            "GoldSniperMini": "gold_sniper_mini",
            "OmniscientOracle": "omniscient_oracle",
            "SilverMeanRev": "silver_mean_rev",
        }
        strategy_name = class_to_name.get(strategy_class, strategy_class.lower())

        save_params = {k: v for k, v in params.items() if not k.startswith("_")}
        save_params["_label"] = label
        save_params["_strategy_class"] = strategy_class
        save_params["_win_rate"] = result.win_rate
        save_params["_profit_factor"] = result.profit_factor
        save_params["_total_profit"] = result.total_profit_usd
        save_params["_max_dd"] = result.max_drawdown_pct
        save_params["_total_trades"] = result.total_trades
        save_params["_daily_wr"] = daily_stats.get("daily_wr", 0.0)
        save_params["_trained_at"] = datetime.now(timezone.utc).isoformat()

        logger.info(f"Saving best params to Brain (Strategy: {strategy_name}, Symbol: {SYMBOL}, Score: {score:.2f})...")
        store.save_evolved_params(
            strategy_name=strategy_name,
            symbol=SYMBOL,
            regime="ALL",
            params=save_params,
            score=score
        )
        logger.info("✅ Saved to brain.db successfully.")

    except Exception as e:
        logger.error(f"Failed to save to brain: {e}")
        import traceback
        traceback.print_exc()


def save_all_results(results: list, days: int, equity: float):
    """บันทึกผลทุก contestant ลง brain.db ตาราง xag_tournament_history."""
    import sqlite3
    try:
        db_path = Path(__file__).resolve().parent.parent / "data" / "sqlite" / "brain.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS xag_tournament_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL,
                strategy_class TEXT NOT NULL,
                params TEXT,
                win_rate REAL,
                profit_factor REAL,
                total_profit REAL,
                max_dd_pct REAL,
                total_trades INTEGER,
                daily_wr REAL,
                consistency REAL,
                score REAL,
                equity REAL,
                days INTEGER,
                trades_per_day REAL,
                expectancy REAL,
                trained_at TEXT
            )
        """)
        ts = datetime.now(timezone.utc).isoformat()
        for r in results:
            res = r["result"]
            ds = r["daily_stats"]
            trades_day = res.total_trades / max(days / 365 * 250, 1)
            expect = res.total_profit_usd / max(res.total_trades, 1)
            conn.execute("""
                INSERT INTO xag_tournament_history
                (label, strategy_class, params, win_rate, profit_factor,
                 total_profit, max_dd_pct, total_trades, daily_wr, consistency,
                 score, equity, days, trades_per_day, expectancy, trained_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r["label"], r["strategy_class"],
                json.dumps({k:v for k,v in r["params"].items() if not k.startswith("_")}),
                res.win_rate, res.profit_factor,
                res.total_profit_usd, res.max_drawdown_pct,
                res.total_trades, ds.get("daily_wr", 0),
                ds.get("consistency", 0), r["score"],
                equity, days, trades_day, expect, ts
            ))
        conn.commit()
        conn.close()
        logger.info(f"📊 Saved {len(results)} tournament results to xag_tournament_history")
    except Exception as e:
        logger.error(f"Failed to save tournament history: {e}")
        import traceback; traceback.print_exc()


# ═══════════════════════════════════════════════════════════════════
# MAIN TOURNAMENT
# ═══════════════════════════════════════════════════════════════════

def run_tournament(days: int = 300, initial_equity: float = 500.0):
    """Run the full tournament and find the best XAG (Silver) strategy."""

    df = fetch_data(SYMBOL, days)
    if df is None:
        print(f"❌ Failed to fetch data. Make sure MT5 is running and {SYMBOL} is in Market Watch.")
        return

    total = len(CONTESTANTS)
    results = []

    print(f"\n{'='*90}")
    print(f"🥈 SILVER STRATEGY TOURNAMENT — {SYMBOL} ({days} Days, Equity: ${initial_equity:.0f})")
    print(f"   Data: {len(df):,} M5 candles | Contestants: {total}")
    print(f"{'='*90}\n")

    header = f"{'#':>3} {'Label':<22} {'Class':<18} {'WR%':>7} {'PF':>7} {'PnL$':>10} {'DD%':>7} {'Trades':>7} {'DailyWR':>8} {'Score':>8}"
    print(header)
    print("-" * len(header))

    for i, contestant in enumerate(CONTESTANTS):
        label = contestant["label"]
        cls_name = contestant["strategy_class"]
        params = contestant["params"]

        try:
            # Create strategy — wrap non-GoldScalp strategies in UniversalAdapter
            needs_adapter = cls_name in ("SniperPro", "SmartFusion", "GoldSniperMini", "OmniscientOracle")
            
            if needs_adapter:
                base_strategy = create_strategy(cls_name)
                apply_params(base_strategy, params)
                config_override = params.get("_override_config", None)
                strategy = UniversalAdapter(base_strategy, cls_name, SYMBOL, config_override)
            else:
                strategy = create_strategy(cls_name)
                apply_params(strategy, params)

            # Reproducible
            random.seed(42)
            np.random.seed(42)

            # Set symbol in df.attrs for strategies that use it
            df.attrs['symbol'] = SYMBOL

            # Run Backtest
            bt = FullFeatureBacktester(strategy, initial_equity=initial_equity)
            result = bt.run(df, SYMBOL, contract_size=CONTRACT_SIZE, point=POINT_VALUE, digits=DIGITS)

            # Daily stats
            if result.trades:
                if isinstance(result.trades[0], dict):
                    trades_df = pd.DataFrame(result.trades)
                else:
                    trades_df = pd.DataFrame([vars(t) for t in result.trades])
                daily_stats = calculate_daily_stats(trades_df)
            else:
                daily_stats = {"daily_wr": 0.0, "consistency": 0.0, "avg_daily_pnl": 0.0}

            # Score
            score = compute_score(result, daily_stats)

            results.append({
                "label": label,
                "strategy_class": cls_name,
                "params": params,
                "result": result,
                "daily_stats": daily_stats,
                "score": score,
            })

            # Print row
            pnl_str = f"${result.total_profit_usd:,.2f}"
            print(f"{i+1:>3} {label:<22} {cls_name:<18} {result.win_rate:>6.1f}% {result.profit_factor:>6.2f} {pnl_str:>10} {result.max_drawdown_pct:>6.1f}% {result.total_trades:>7} {daily_stats['daily_wr']:>7.1f}% {score:>8.1f}")

        except Exception as e:
            print(f"{i+1:>3} {label:<22} {cls_name:<18} ❌ ERROR: {e}")
            import traceback
            traceback.print_exc()

    if not results:
        print("\n❌ No results. Check MT5 connection and data.")
        return

    # Sort by score
    results.sort(key=lambda x: x["score"], reverse=True)

    # ─── Results Summary ───
    best = results[0]
    best_res = best["result"]
    best_ds = best["daily_stats"]

    print(f"\n{'='*90}")
    print(f"🥈🏆 CHAMPION: {best['label']} ({best['strategy_class']})")
    print(f"{'='*90}")
    print(f"  Win Rate       : {best_res.win_rate:.2f}%")
    print(f"  Profit Factor  : {best_res.profit_factor:.2f}")
    print(f"  Total Profit   : ${best_res.total_profit_usd:.2f}")
    print(f"  Max Drawdown   : {best_res.max_drawdown_pct:.2f}%")
    print(f"  Total Trades   : {best_res.total_trades}")
    print(f"  Daily Win Rate : {best_ds['daily_wr']:.1f}%")
    print(f"  Consistency    : {best_ds['consistency']:.3f}")
    print(f"  Score          : {best['score']:.2f}")
    print(f"\n  Parameters:")
    for k, v in best["params"].items():
        if not k.startswith("_"):
            print(f"    {k:20s} = {v}")

    # Top 3
    print(f"\n{'─'*50}")
    print("📊 TOP 3:")
    for rank, r in enumerate(results[:3], 1):
        emoji = ["🥇", "🥈", "🥉"][rank-1]
        print(f"  {emoji} {r['label']:<22} WR={r['result'].win_rate:.1f}% PF={r['result'].profit_factor:.2f} PnL=${r['result'].total_profit_usd:.2f} Score={r['score']:.1f}")

    # Save best to brain
    save_to_brain(
        label=best["label"],
        strategy_class=best["strategy_class"],
        params=best["params"],
        result=best_res,
        daily_stats=best_ds,
        score=best["score"]
    )

    # Also save runner-up if significantly different strategy class
    if len(results) >= 2:
        runner = results[1]
        if runner["strategy_class"] != best["strategy_class"] and runner["score"] > 0:
            save_to_brain(
                label=runner["label"],
                strategy_class=runner["strategy_class"],
                params=runner["params"],
                result=runner["result"],
                daily_stats=runner["daily_stats"],
                score=runner["score"],
            )
            print(f"\n  ✅ Also saved runner-up ({runner['label']}) as alternative strategy")

    # ─── Save ALL results to brain.db for analysis ───
    save_all_results(results, days, initial_equity)

    print(f"\n{'='*90}")
    print("✅ XAG (Silver) Training Complete!")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="XAG (Silver) Strategy Tournament (USC)")
    parser.add_argument("--days", type=int, default=365, help="Days of historical data (default: 365 = 1 year)")
    parser.add_argument("--equity", type=float, default=500.0, help="Initial equity in USD (default: 500 for USC/Cent)")
    args = parser.parse_args()

    try:
        run_tournament(days=args.days, initial_equity=args.equity)
    except KeyboardInterrupt:
        print("\n⚠️ Tournament interrupted by user.")
    except Exception:
        import traceback
        traceback.print_exc()
