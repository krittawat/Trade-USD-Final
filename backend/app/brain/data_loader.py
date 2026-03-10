"""
DataLoader V2 — Prepare data for Deep Learning (ATR-Based Target + 22 Features).

V2 Changes:
    1. ATR-based target: Win if price reaches +1ATR before -1ATR within 20 bars
    2. 22 real features (no placeholders): RSI, MACD, BBands, Stochastic, etc.
    3. Better normalization (all features scaled to roughly -1 to 1)

Memory Safety:
    - MT5 fetch limited to ~10000 bars max.
    - Return numpy arrays (efficient).
"""

import pandas as pd
import app.analysis.indicators as ind
import numpy as np
from typing import Tuple
from datetime import datetime, timezone

import MetaTrader5 as mt5

from app.core.logging import get_logger

logger = get_logger(__name__)

SEQUENCE_LENGTH = 60  # Must match deep_learner.py
INPUT_SIZE = 22       # V2: 22 real features

# MT5 timeframe mapping
TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

# V2 Feature columns (22 features — no placeholders)
FEATURE_COLS = [
    "rsi", "rsi_slope", "atr_ratio", "atr_pct",
    "adx", "plus_di", "minus_di",
    "ema_cross", "close_vs_ema50",
    "macd_hist", "bb_percent", "stoch_k",
    "body_ratio", "wick_ratio", "candle_direction",
    "vol_ratio", "momentum_3",
    "hour_sin", "hour_cos", "day_sin", "day_cos",
    "spread_norm",
]

class DLDataLoader:
    def __init__(self, db_path=None):
        pass

    def load_training_data(self, symbol: str, timeframe: str = "M5", limit: int = 5000) -> Tuple[np.ndarray, np.ndarray]:
        """
        V2: Load candles, compute 22 features, ATR-based target.
        
        Target: 1 if price reaches +1 ATR before -1 ATR within 20 bars (Win)
                0 otherwise (Loss/Flat)
        """
        logger.info("dl_loading_data_v2", extra={"symbol": symbol, "limit": limit})
        
        df = self._fetch_candles_mt5(symbol, timeframe, limit)
        if df is None or df.empty or len(df) < SEQUENCE_LENGTH + 50:
            logger.warning("dl_no_data", extra={"symbol": symbol})
            return np.array([]), np.array([])

        # 1. Compute Indicators
        df = self._add_indicators_v2(df)
        df.dropna(inplace=True)

        # 2. ATR-Based Target
        df = self._compute_atr_target(df, horizon=20, atr_mult=1.0)
        df.dropna(inplace=True)

        if len(df) < SEQUENCE_LENGTH + 10:
            logger.warning("dl_insufficient_after_target", extra={"symbol": symbol, "rows": len(df)})
            return np.array([]), np.array([])

        # 3. Build feature matrix
        data_matrix = df[FEATURE_COLS].values.astype(np.float32)
        
        # Replace any inf/nan
        data_matrix = np.nan_to_num(data_matrix, nan=0.0, posinf=1.0, neginf=-1.0)

        # 4. Sliding Windows
        X, y = [], []
        targets = df["target"].values
        for i in range(len(data_matrix) - SEQUENCE_LENGTH):
            window = data_matrix[i : i + SEQUENCE_LENGTH]
            target = targets[i + SEQUENCE_LENGTH - 1]
            X.append(window)
            y.append(target)

        X_arr = np.array(X, dtype=np.float32)
        y_arr = np.array(y, dtype=np.float32)
        
        pos_ratio = y_arr.mean() if len(y_arr) > 0 else 0
        logger.info("dl_data_ready_v2", extra={
            "symbol": symbol, "sequences": len(X), 
            "features": INPUT_SIZE, "pos_ratio": round(pos_ratio, 3)
        })
        return X_arr, y_arr

    def _compute_atr_target(self, df: pd.DataFrame, horizon: int = 20, atr_mult: float = 1.0) -> pd.DataFrame:
        """
        ATR-Based Target: Does price reach +1 ATR (TP) before -1 ATR (SL) within horizon bars?
        
        This simulates what a real trade would look like:
        - Win = price reaches Take Profit first
        - Loss = price reaches Stop Loss first OR stays flat
        """
        closes = df["close"].values
        atrs = df["atr_raw"].values  # Raw ATR (not normalized)
        targets = np.zeros(len(closes), dtype=np.float32)
        
        for i in range(len(closes) - horizon):
            entry = closes[i]
            atr = atrs[i]
            if atr <= 0 or np.isnan(atr):
                targets[i] = 0
                continue
                
            tp_level = entry + atr * atr_mult
            sl_level = entry - atr * atr_mult
            
            # Check future bars
            hit_tp = False
            hit_sl = False
            for j in range(1, horizon + 1):
                if i + j >= len(closes):
                    break
                future_high = df["high"].iloc[i + j]
                future_low = df["low"].iloc[i + j]
                
                if future_high >= tp_level:
                    hit_tp = True
                    break
                if future_low <= sl_level:
                    hit_sl = True
                    break
            
            targets[i] = 1.0 if hit_tp and not hit_sl else 0.0
        
        # Remove last `horizon` rows (no future data)
        df["target"] = targets
        df = df.iloc[:-horizon].copy()
        return df

    def _fetch_candles_mt5(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        """Fetch OHLCV directly from MT5."""
        try:
            tf = TIMEFRAME_MAP.get(timeframe)
            if tf is None:
                logger.error("dl_invalid_timeframe", extra={"timeframe": timeframe})
                return pd.DataFrame()

            if not mt5.terminal_info():
                mt5.initialize()

            rates = mt5.copy_rates_from_pos(symbol, tf, 0, min(limit, 10000))
            
            if rates is None or len(rates) == 0:
                logger.error("dl_mt5_no_data", extra={
                    "symbol": symbol, "error": str(mt5.last_error())
                })
                return pd.DataFrame()

            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
            
            if "tick_volume" in df.columns:
                df["volume"] = df["tick_volume"]
            
            df.sort_values("time", inplace=True)
            df.reset_index(drop=True, inplace=True)
            
            logger.info("dl_candles_fetched", extra={
                "symbol": symbol, "bars": len(df)
            })
            return df

        except Exception as e:
            logger.error("dl_fetch_error", extra={"error": str(e)})
            return pd.DataFrame()

    def _add_indicators_v2(self, df: pd.DataFrame) -> pd.DataFrame:
        """V2: Compute 22 real features — no placeholders."""
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"] if "volume" in df.columns else pd.Series(0, index=df.index)
        
        # ──── Trend ────
        # RSI
        df["rsi"] = ta.rsi(close, length=14) / 100.0
        df["rsi_slope"] = df["rsi"].diff(5)
        
        # ATR (raw for target, ratio for feature)
        df["atr_raw"] = ta.atr(high, low, close, length=14)
        atr_avg = df["atr_raw"].rolling(42).mean()
        df["atr_ratio"] = (df["atr_raw"] / atr_avg).clip(0, 3) / 3.0
        df["atr_pct"] = (df["atr_raw"] / close * 100).clip(0, 5) / 5.0
        
        # ADX
        adx = ta.adx(high, low, close, length=14)
        if adx is not None:
            df["adx"] = adx.get("ADX_14", 0) / 100.0
            df["plus_di"] = adx.get("DMP_14", 0) / 100.0
            df["minus_di"] = adx.get("DMN_14", 0) / 100.0
        else:
            df["adx"] = 0.0
            df["plus_di"] = 0.0
            df["minus_di"] = 0.0
            
        # EMA Cross
        ema9 = ta.ema(close, length=9)
        ema21 = ta.ema(close, length=21)
        ema50 = ta.ema(close, length=50)
        df["ema_cross"] = ((ema9 - ema21) / ema21 * 100).clip(-5, 5) / 5.0
        df["close_vs_ema50"] = ((close - ema50) / ema50 * 100).clip(-5, 5) / 5.0

        # ──── Momentum ────
        # MACD
        macd = ta.macd(close, fast=12, slow=26, signal=9)
        if macd is not None and "MACDh_12_26_9" in macd.columns:
            macd_h = macd["MACDh_12_26_9"]
            # Normalize by ATR for cross-symbol compatibility
            df["macd_hist"] = (macd_h / df["atr_raw"]).clip(-3, 3) / 3.0
        else:
            df["macd_hist"] = 0.0
        
        # Bollinger Band %B
        bb = ta.bbands(close, length=20, std=2)
        if bb is not None:
            bbl = bb.get("BBL_20_2.0")
            bbu = bb.get("BBU_20_2.0")
            if bbl is not None and bbu is not None:
                bb_range = bbu - bbl
                df["bb_percent"] = ((close - bbl) / bb_range).clip(0, 1)
            else:
                df["bb_percent"] = 0.5
        else:
            df["bb_percent"] = 0.5
        
        # Stochastic K
        stoch = ta.stoch(high, low, close, k=14, d=3)
        if stoch is not None and "STOCHk_14_3_3" in stoch.columns:
            df["stoch_k"] = stoch["STOCHk_14_3_3"] / 100.0
        else:
            df["stoch_k"] = 0.5

        # ──── Candle Structure ────
        rng = high - low
        body = (close - df["open"]).abs()
        df["body_ratio"] = (body / rng).clip(0, 1)
        
        upper_wick = high - df[["open", "close"]].max(axis=1)
        lower_wick = df[["open", "close"]].min(axis=1) - low
        df["wick_ratio"] = ((upper_wick + lower_wick) / rng).clip(0, 1)
        
        # Candle direction: +1 bullish, -1 bearish, normalized to -1..1
        df["candle_direction"] = np.sign(close - df["open"]).astype(float)
        
        # ──── Volume ────
        vol_avg = volume.rolling(20).mean()
        df["vol_ratio"] = (volume / vol_avg).clip(0, 5) / 5.0
        
        # ──── Momentum ────
        df["momentum_3"] = (close.pct_change(3) * 100).clip(-5, 5) / 5.0

        # ──── Time (Cyclical Encoding) ────
        hour = df["time"].dt.hour
        day = df["time"].dt.dayofweek
        df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
        df["day_sin"] = np.sin(2 * np.pi * day / 5)
        df["day_cos"] = np.cos(2 * np.pi * day / 5)
        
        # ──── Spread ────
        if "spread" in df.columns:
            spread_avg = df["spread"].rolling(50).mean()
            df["spread_norm"] = (df["spread"] / spread_avg).clip(0, 3) / 3.0
        else:
            df["spread_norm"] = 0.5
        
        return df
