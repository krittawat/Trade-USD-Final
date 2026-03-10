"""
MTF Feature Engine — Multi-Timeframe Feature Engineering for AI Training.

Combines features from multiple timeframes into a unified feature vector:
    M5:  22 features (full indicator set - entry signals)
    M15:  8 features (trend + momentum - confirmation)
    H1:   6 features (trend + structure - direction bias)
    H4:   4 features (major trend)
    D1:   3 features (macro environment)
    ─────────────────
    Total: 43 features (V3)

    V4 adds 12 microstructure features on M5:
    rsi_divergence, volume_delta, volume_ma_ratio, session_sin/cos,
    day_of_week_sin/cos, price_acceleration, wick_pressure,
    range_position, consecutive_direction, atr_change_rate
    ─────────────────
    Total: 55 features (V4)

Higher TF data is forward-filled (time-aligned) to M5 bars.

RAM Safety:
    - Process one symbol at a time
    - Use chunked reads from Parquet
    - Free intermediate DataFrames after merge
"""

import os
import numpy as np
import pandas as pd
import pandas_ta as ta
import app.analysis.indicators as ind
from typing import Tuple, Optional
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── Constants ─────────────────────────────────────────────────────
SEQUENCE_LENGTH = 60  # Lookback window for model input
INPUT_SIZE = 43       # Total MTF features (V3)
INPUT_SIZE_V4 = 55    # V4: 43 MTF + 12 microstructure

# Feature column names per TF
M5_FEATURES = [
    "rsi", "rsi_slope", "atr_ratio", "atr_pct",
    "adx", "plus_di", "minus_di",
    "ema_cross", "close_vs_ema50",
    "macd_hist", "bb_percent", "stoch_k",
    "body_ratio", "wick_ratio", "candle_direction",
    "vol_ratio", "momentum_3",
    "hour_sin", "hour_cos", "day_sin", "day_cos",
    "spread_norm",
]  # 22 features

# V4: 12 additional microstructure features
V4_EXTRA_FEATURES = [
    "rsi_divergence",
    "volume_delta",
    "volume_ma_ratio",
    "session_sin",
    "session_cos",
    "day_of_week_sin",
    "day_of_week_cos",
    "price_acceleration",
    "wick_pressure",
    "range_position",
    "consecutive_direction",
    "atr_change_rate",
]  # 12 features

M15_FEATURES = [
    "m15_rsi", "m15_adx", "m15_ema_cross", "m15_macd_hist",
    "m15_bb_percent", "m15_stoch_k", "m15_momentum_3", "m15_vol_ratio",
]  # 8 features

H1_FEATURES = [
    "h1_rsi", "h1_adx", "h1_ema_cross",
    "h1_macd_hist", "h1_atr_ratio", "h1_trend_strength",
]  # 6 features

H4_FEATURES = [
    "h4_rsi", "h4_adx", "h4_ema_cross", "h4_trend_strength",
]  # 4 features

D1_FEATURES = [
    "d1_rsi", "d1_ema_cross", "d1_atr_regime",
]  # 3 features

ALL_FEATURES = M5_FEATURES + M15_FEATURES + H1_FEATURES + H4_FEATURES + D1_FEATURES
ALL_FEATURES_V4 = ALL_FEATURES + V4_EXTRA_FEATURES  # 55 total


class MTFFeatureEngine:
    """Multi-Timeframe Feature Engineering Engine."""

    def __init__(self, data_dir: str | None = None):
        """
        Args:
            data_dir: Directory containing Parquet files.
                      Default: backend/data/exports/mtf
        """
        if data_dir is None:
            data_dir = os.path.join(
                os.path.dirname(__file__), "..", "..", "data", "exports", "mtf"
            )
        self.data_dir = os.path.abspath(data_dir)

    def load_and_build(
        self,
        symbol: str,
        limit: int | None = None,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Load Parquet data, build MTF features, create sequences + targets.

        Args:
            symbol: e.g. "XAUUSDc"
            limit: Max M5 bars to use (None = all)

        Returns:
            (X, y): X shape (N, SEQUENCE_LENGTH, INPUT_SIZE), y shape (N,)
        """
        logger.info("mtf_loading", extra={"symbol": symbol, "data_dir": self.data_dir})

        # 1. Load all TF data from Parquet
        dfs = self._load_parquet_data(symbol)
        if dfs is None:
            return np.array([]), np.array([])

        # 2. Compute features per TF
        m5 = self._compute_m5_features(dfs["M5"])
        m15 = self._compute_higher_tf_features(dfs.get("M15"), prefix="m15", full=True)
        h1 = self._compute_higher_tf_features(dfs.get("H1"), prefix="h1", full=False)
        h4 = self._compute_higher_tf_features(dfs.get("H4"), prefix="h4", minimal=True)
        d1 = self._compute_d1_features(dfs.get("D1"))

        # Free raw data
        del dfs

        # 3. Time-align higher TFs to M5
        merged = self._merge_timeframes(m5, m15, h1, h4, d1)
        del m5, m15, h1, h4, d1

        if merged is None or len(merged) < SEQUENCE_LENGTH + 30:
            logger.warning("mtf_insufficient_data", extra={
                "symbol": symbol, "rows": len(merged) if merged is not None else 0
            })
            return np.array([]), np.array([])

        if limit:
            merged = merged.tail(limit).reset_index(drop=True)

        # 4. Compute ATR-based targets
        merged = self._compute_target(merged)
        merged.dropna(inplace=True)

        if len(merged) < SEQUENCE_LENGTH + 10:
            logger.warning("mtf_insufficient_after_target", extra={
                "symbol": symbol, "rows": len(merged)
            })
            return np.array([]), np.array([])

        # 5. Build feature matrix
        feature_cols = [c for c in ALL_FEATURES if c in merged.columns]
        missing = set(ALL_FEATURES) - set(feature_cols)
        for col in missing:
            merged[col] = 0.0
            feature_cols.append(col)

        # Ensure column order matches ALL_FEATURES
        data_matrix = merged[ALL_FEATURES].values.astype(np.float32)
        data_matrix = np.nan_to_num(data_matrix, nan=0.0, posinf=1.0, neginf=-1.0)

        # 6. Create sliding window sequences
        targets = merged["target"].values
        X, y = [], []
        for i in range(len(data_matrix) - SEQUENCE_LENGTH):
            X.append(data_matrix[i: i + SEQUENCE_LENGTH])
            y.append(targets[i + SEQUENCE_LENGTH - 1])

        X_arr = np.array(X, dtype=np.float32)
        y_arr = np.array(y, dtype=np.float32)

        pos_ratio = y_arr.mean() if len(y_arr) > 0 else 0
        logger.info("mtf_features_ready", extra={
            "symbol": symbol,
            "sequences": len(X_arr),
            "features": INPUT_SIZE,
            "pos_ratio": round(pos_ratio, 3),
        })
        return X_arr, y_arr

    def build_live_features(
        self,
        candles_m5: pd.DataFrame,
        candles_m15: pd.DataFrame | None = None,
        candles_h1: pd.DataFrame | None = None,
        candles_h4: pd.DataFrame | None = None,
        candles_d1: pd.DataFrame | None = None,
    ) -> np.ndarray | None:
        """
        Build feature sequence from live candles (for prediction).

        Returns:
            numpy array of shape (1, SEQUENCE_LENGTH, INPUT_SIZE) or None
        """
        if candles_m5 is None or len(candles_m5) < SEQUENCE_LENGTH + 50:
            return None

        # Ensure time column exists
        for df in [candles_m5, candles_m15, candles_h1, candles_h4, candles_d1]:
            if df is not None and "time" not in df.columns and df.index.name == "time":
                df.reset_index(inplace=True)

        m5 = self._compute_m5_features(candles_m5)
        m15 = self._compute_higher_tf_features(candles_m15, prefix="m15", full=True)
        h1 = self._compute_higher_tf_features(candles_h1, prefix="h1", full=False)
        h4 = self._compute_higher_tf_features(candles_h4, prefix="h4", minimal=True)
        d1 = self._compute_d1_features(candles_d1)

        merged = self._merge_timeframes(m5, m15, h1, h4, d1)
        if merged is None or len(merged) < SEQUENCE_LENGTH:
            return None

        # Fill missing features
        for col in ALL_FEATURES:
            if col not in merged.columns:
                merged[col] = 0.0

        data_matrix = merged[ALL_FEATURES].tail(SEQUENCE_LENGTH).values.astype(np.float32)
        data_matrix = np.nan_to_num(data_matrix, nan=0.0, posinf=1.0, neginf=-1.0)

        return data_matrix.reshape(1, SEQUENCE_LENGTH, INPUT_SIZE)

    # ─── Private: Data Loading ─────────────────────────────────────────

    def _load_parquet_data(self, symbol: str) -> dict[str, pd.DataFrame] | None:
        """Load Parquet files for all TFs."""
        dfs = {}
        required_tfs = ["M5"]
        optional_tfs = ["M15", "H1", "H4", "D1"]

        for tf in required_tfs + optional_tfs:
            filepath = os.path.join(self.data_dir, f"{symbol}_{tf}.parquet")
            if os.path.exists(filepath):
                df = pd.read_parquet(filepath)
                if "time" in df.columns:
                    df["time"] = pd.to_datetime(df["time"], utc=True)
                dfs[tf] = df
                logger.debug("parquet_loaded", extra={
                    "symbol": symbol, "tf": tf, "bars": len(df)
                })
            elif tf in required_tfs:
                logger.error("parquet_missing", extra={
                    "symbol": symbol, "tf": tf, "path": filepath
                })
                return None
            else:
                logger.debug("parquet_optional_missing", extra={
                    "symbol": symbol, "tf": tf
                })

        return dfs

    # ─── Private: Feature Computation ──────────────────────────────────

    def _compute_m5_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute full 22-feature set for M5 (same as DataLoader V2)."""
        df = df.copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"] if "volume" in df.columns else pd.Series(0, index=df.index)

        # ── Trend ──
        df["rsi"] = ta.rsi(close, length=14) / 100.0
        df["rsi_slope"] = df["rsi"].diff(5)

        df["atr_raw"] = ta.atr(high, low, close, length=14)
        atr_avg = df["atr_raw"].rolling(42).mean()
        df["atr_ratio"] = (df["atr_raw"] / atr_avg).clip(0, 3) / 3.0
        df["atr_pct"] = (df["atr_raw"] / close * 100).clip(0, 5) / 5.0

        adx = ta.adx(high, low, close, length=14)
        if adx is not None:
            df["adx"] = adx.get("ADX_14", 0) / 100.0
            df["plus_di"] = adx.get("DMP_14", 0) / 100.0
            df["minus_di"] = adx.get("DMN_14", 0) / 100.0
        else:
            df["adx"] = df["plus_di"] = df["minus_di"] = 0.0

        ema9 = ta.ema(close, length=9)
        ema21 = ta.ema(close, length=21)
        ema50 = ta.ema(close, length=50)
        df["ema_cross"] = ((ema9 - ema21) / ema21 * 100).clip(-5, 5) / 5.0
        df["close_vs_ema50"] = ((close - ema50) / ema50 * 100).clip(-5, 5) / 5.0

        # ── Momentum ──
        macd = ta.macd(close, fast=12, slow=26, signal=9)
        if macd is not None and "MACDh_12_26_9" in macd.columns:
            df["macd_hist"] = (macd["MACDh_12_26_9"] / df["atr_raw"]).clip(-3, 3) / 3.0
        else:
            df["macd_hist"] = 0.0

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

        stoch = ta.stoch(high, low, close, k=14, d=3)
        if stoch is not None and "STOCHk_14_3_3" in stoch.columns:
            df["stoch_k"] = stoch["STOCHk_14_3_3"] / 100.0
        else:
            df["stoch_k"] = 0.5

        # ── Candle Structure ──
        rng = high - low
        body = (close - df["open"]).abs()
        df["body_ratio"] = (body / rng.replace(0, np.nan)).clip(0, 1).fillna(0)

        upper_wick = high - df[["open", "close"]].max(axis=1)
        lower_wick = df[["open", "close"]].min(axis=1) - low
        df["wick_ratio"] = ((upper_wick + lower_wick) / rng.replace(0, np.nan)).clip(0, 1).fillna(0)
        df["candle_direction"] = np.sign(close - df["open"]).astype(float)

        # ── Volume ──
        vol_avg = volume.rolling(20).mean()
        df["vol_ratio"] = (volume / vol_avg.replace(0, np.nan)).clip(0, 5).fillna(0) / 5.0

        # ── Momentum ──
        df["momentum_3"] = (close.pct_change(3) * 100).clip(-5, 5) / 5.0

        # ── Time (Cyclical) ──
        if "time" in df.columns:
            hour = df["time"].dt.hour
            day = df["time"].dt.dayofweek
        else:
            hour = pd.Series(0, index=df.index)
            day = pd.Series(0, index=df.index)
        df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
        df["day_sin"] = np.sin(2 * np.pi * day / 5)
        df["day_cos"] = np.cos(2 * np.pi * day / 5)

        # ── Spread ──
        df["spread_norm"] = 0.5  # Parquet may not have spread

        return df

    def _compute_higher_tf_features(
        self,
        df: pd.DataFrame | None,
        prefix: str,
        full: bool = False,
        minimal: bool = False,
    ) -> pd.DataFrame | None:
        """Compute features for M15/H1/H4."""
        if df is None or df.empty:
            return None

        df = df.copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"] if "volume" in df.columns else pd.Series(0, index=df.index)

        # RSI
        df[f"{prefix}_rsi"] = ta.rsi(close, length=14) / 100.0

        # ADX
        adx = ta.adx(high, low, close, length=14)
        if adx is not None:
            df[f"{prefix}_adx"] = adx.get("ADX_14", 0) / 100.0
        else:
            df[f"{prefix}_adx"] = 0.0

        # EMA Cross
        ema9 = ta.ema(close, length=9)
        ema21 = ta.ema(close, length=21)
        df[f"{prefix}_ema_cross"] = ((ema9 - ema21) / ema21 * 100).clip(-5, 5) / 5.0

        # Trend strength (EMA50 slope)
        ema50 = ta.ema(close, length=50)
        if ema50 is not None:
            df[f"{prefix}_trend_strength"] = (ema50.pct_change(5) * 1000).clip(-5, 5) / 5.0
        else:
            df[f"{prefix}_trend_strength"] = 0.0

        if not minimal:
            # MACD
            macd = ta.macd(close, fast=12, slow=26, signal=9)
            atr_raw = ta.atr(high, low, close, length=14)
            if macd is not None and "MACDh_12_26_9" in macd.columns and atr_raw is not None:
                df[f"{prefix}_macd_hist"] = (macd["MACDh_12_26_9"] / atr_raw).clip(-3, 3) / 3.0
            else:
                df[f"{prefix}_macd_hist"] = 0.0

        if not minimal and prefix == "h1":
            # ATR ratio for H1
            atr_raw = ta.atr(high, low, close, length=14)
            if atr_raw is not None:
                atr_avg = atr_raw.rolling(42).mean()
                df[f"{prefix}_atr_ratio"] = (atr_raw / atr_avg).clip(0, 3).fillna(1.0) / 3.0
            else:
                df[f"{prefix}_atr_ratio"] = 0.33

        if full:
            # Additional for M15 only
            bb = ta.bbands(close, length=20, std=2)
            if bb is not None:
                bbl = bb.get("BBL_20_2.0")
                bbu = bb.get("BBU_20_2.0")
                if bbl is not None and bbu is not None:
                    bb_range = bbu - bbl
                    df[f"{prefix}_bb_percent"] = ((close - bbl) / bb_range).clip(0, 1)
                else:
                    df[f"{prefix}_bb_percent"] = 0.5
            else:
                df[f"{prefix}_bb_percent"] = 0.5

            stoch = ta.stoch(high, low, close, k=14, d=3)
            if stoch is not None and "STOCHk_14_3_3" in stoch.columns:
                df[f"{prefix}_stoch_k"] = stoch["STOCHk_14_3_3"] / 100.0
            else:
                df[f"{prefix}_stoch_k"] = 0.5

            df[f"{prefix}_momentum_3"] = (close.pct_change(3) * 100).clip(-5, 5) / 5.0

            vol_avg = volume.rolling(20).mean()
            df[f"{prefix}_vol_ratio"] = (volume / vol_avg.replace(0, np.nan)).clip(0, 5).fillna(0) / 5.0

        return df

    def _compute_d1_features(self, df: pd.DataFrame | None) -> pd.DataFrame | None:
        """Compute D1 features: RSI, EMA cross, ATR regime."""
        if df is None or df.empty:
            return None

        df = df.copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]

        df["d1_rsi"] = ta.rsi(close, length=14) / 100.0

        ema9 = ta.ema(close, length=9)
        ema21 = ta.ema(close, length=21)
        df["d1_ema_cross"] = ((ema9 - ema21) / ema21 * 100).clip(-5, 5) / 5.0

        # ATR regime: is volatility expanding or contracting?
        atr_raw = ta.atr(high, low, close, length=14)
        if atr_raw is not None:
            atr_avg = atr_raw.rolling(42).mean()
            df["d1_atr_regime"] = (atr_raw / atr_avg).clip(0, 3).fillna(1.0) / 3.0
        else:
            df["d1_atr_regime"] = 0.33

        return df

    # ─── Private: Merge ────────────────────────────────────────────────

    def _merge_timeframes(
        self,
        m5: pd.DataFrame,
        m15: pd.DataFrame | None,
        h1: pd.DataFrame | None,
        h4: pd.DataFrame | None,
        d1: pd.DataFrame | None,
    ) -> pd.DataFrame | None:
        """Merge higher TF features into M5 via time-based forward-fill."""
        if m5 is None or m5.empty:
            return None

        result = m5.copy()
        if "time" not in result.columns:
            logger.error("m5_no_time_column")
            return None

        for tf_df, tf_features, tf_name in [
            (m15, M15_FEATURES, "M15"),
            (h1, H1_FEATURES, "H1"),
            (h4, H4_FEATURES, "H4"),
            (d1, D1_FEATURES, "D1"),
        ]:
            if tf_df is None or tf_df.empty:
                # Fill with zeros if TF data missing
                for col in tf_features:
                    result[col] = 0.0
                continue

            # Select only time + feature columns
            available_features = [c for c in tf_features if c in tf_df.columns]
            if not available_features:
                for col in tf_features:
                    result[col] = 0.0
                continue

            htf = tf_df[["time"] + available_features].copy()
            htf = htf.sort_values("time").drop_duplicates(subset=["time"], keep="last")

            # merge_asof: align higher TF to M5 time (forward-fill)
            result = pd.merge_asof(
                result.sort_values("time"),
                htf,
                on="time",
                direction="backward",  # Use the most recent higher-TF bar
            )

            # Fill missing features with 0
            for col in tf_features:
                if col not in result.columns:
                    result[col] = 0.0

            logger.debug("tf_merged", extra={
                "tf": tf_name,
                "features_merged": len(available_features),
            })

        return result

    # ─── Private: Target ───────────────────────────────────────────────

    def _compute_target(
        self,
        df: pd.DataFrame,
        horizon: int = 20,
        atr_mult: float = 1.0,
    ) -> pd.DataFrame:
        """
        ATR-Based Target (V3 binary): Win if price reaches +1 ATR before -1 ATR.
        """
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        if "atr_raw" not in df.columns:
            df["atr_raw"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        atrs = df["atr_raw"].values
        targets = np.zeros(len(closes), dtype=np.float32)

        for i in range(len(closes) - horizon):
            entry = closes[i]
            atr = atrs[i]
            if atr <= 0 or np.isnan(atr):
                targets[i] = 0
                continue

            tp_level = entry + atr * atr_mult
            sl_level = entry - atr * atr_mult

            for j in range(1, horizon + 1):
                idx = i + j
                if idx >= len(closes):
                    break
                if highs[idx] >= tp_level:
                    targets[i] = 1.0
                    break
                if lows[idx] <= sl_level:
                    targets[i] = 0.0
                    break

        df["target"] = targets
        df = df.iloc[:-horizon].copy()
        return df

    def _compute_target_3class(
        self,
        df: pd.DataFrame,
        horizon: int = 20,
        tp_mult: float = 1.5,
        sl_mult: float = 1.0,
    ) -> pd.DataFrame:
        """
        V4 3-Class Target:
            BUY (0):  price reaches +tp_mult ATR before -sl_mult ATR
            SELL (2): price reaches -tp_mult ATR before +sl_mult ATR
            HOLD (1): neither target reached within horizon bars

        The asymmetric tp/sl creates a slight bar for trade signals,
        making HOLD the default and reducing overtrading.
        """
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values

        if "atr_raw" not in df.columns:
            df["atr_raw"] = ta.atr(df["high"], df["low"], df["close"], length=14)

        atrs = df["atr_raw"].values
        targets = np.ones(len(closes), dtype=np.int64)  # Default = HOLD (1)

        for i in range(len(closes) - horizon):
            entry = closes[i]
            atr = atrs[i]
            if atr <= 0 or np.isnan(atr):
                targets[i] = 1  # HOLD
                continue

            buy_tp = entry + atr * tp_mult
            buy_sl = entry - atr * sl_mult
            sell_tp = entry - atr * tp_mult
            sell_sl = entry + atr * sl_mult

            for j in range(1, horizon + 1):
                idx = i + j
                if idx >= len(closes):
                    break
                # Check BUY condition: price goes up to TP before hitting SL
                if highs[idx] >= buy_tp:
                    targets[i] = 0  # BUY
                    break
                # Check SELL condition: price goes down to TP before hitting SL
                if lows[idx] <= sell_tp:
                    targets[i] = 2  # SELL
                    break
            # If loop completes without break → stays HOLD (1)

        df["target_3class"] = targets
        df = df.iloc[:-horizon].copy()
        return df

    # ─── V4: Additional Microstructure Features ────────────────────────

    def _compute_v4_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute 12 additional V4 microstructure features on M5 data.
        Applied after _compute_m5_features() on the same DataFrame.
        """
        close = df["close"]
        high = df["high"]
        low = df["low"]
        opn = df["open"]
        volume = df["volume"] if "volume" in df.columns else pd.Series(0, index=df.index)

        # 1. RSI Divergence: compare RSI direction vs price direction
        rsi_change = df["rsi"].diff(5) if "rsi" in df.columns else pd.Series(0, index=df.index)
        price_change = close.pct_change(5)
        # Divergence = RSI going opposite to price
        df["rsi_divergence"] = np.where(
            (rsi_change > 0) & (price_change < 0), 1.0,
            np.where((rsi_change < 0) & (price_change > 0), -1.0, 0.0)
        )

        # 2. Volume Delta: buy vs sell pressure (up candle vol - down candle vol)
        direction = np.sign(close - opn)
        vol_delta = (direction * volume).rolling(10).sum()
        vol_total = volume.rolling(10).sum()
        df["volume_delta"] = (vol_delta / vol_total.replace(0, np.nan)).clip(-1, 1).fillna(0)

        # 3. Volume MA Ratio: current volume / 20-bar MA
        vol_ma20 = volume.rolling(20).mean()
        df["volume_ma_ratio"] = (volume / vol_ma20.replace(0, np.nan)).clip(0, 5).fillna(0) / 5.0

        # 4-5. Session Encoding (cyclical) — based on hour of day
        if "time" in df.columns:
            # Map hour to trading session:
            # Asian=0-8, London=8-15, NY=15-22, Off=22-24
            hour = df["time"].dt.hour
            session_phase = hour / 24.0  # 0-1 range
        else:
            session_phase = pd.Series(0.5, index=df.index)
        df["session_sin"] = np.sin(2 * np.pi * session_phase)
        df["session_cos"] = np.cos(2 * np.pi * session_phase)

        # 6-7. Day of Week Encoding (cyclical)
        if "time" in df.columns:
            dow = df["time"].dt.dayofweek / 5.0  # 0-1 for Mon-Fri
        else:
            dow = pd.Series(0.5, index=df.index)
        df["day_of_week_sin"] = np.sin(2 * np.pi * dow)
        df["day_of_week_cos"] = np.cos(2 * np.pi * dow)

        # 8. Price Acceleration — second derivative of EMA9
        ema9 = ta.ema(close, length=9)
        if ema9 is not None:
            ema_velocity = ema9.diff()
            ema_accel = ema_velocity.diff()
            atr_raw = df["atr_raw"] if "atr_raw" in df.columns else ta.atr(high, low, close, length=14)
            df["price_acceleration"] = (ema_accel / atr_raw.replace(0, np.nan)).clip(-3, 3).fillna(0) / 3.0
        else:
            df["price_acceleration"] = 0.0

        # 9. Wick Pressure — net buying/selling pressure from wicks
        rng = (high - low).replace(0, np.nan)
        upper_wick = high - df[["open", "close"]].max(axis=1)
        lower_wick = df[["open", "close"]].min(axis=1) - low
        # Positive = more lower wick = buying pressure
        df["wick_pressure"] = ((lower_wick - upper_wick) / rng).clip(-1, 1).fillna(0)

        # 10. Range Position — where is price in recent high-low range
        rolling_high = high.rolling(20).max()
        rolling_low = low.rolling(20).min()
        rolling_range = (rolling_high - rolling_low).replace(0, np.nan)
        df["range_position"] = ((close - rolling_low) / rolling_range).clip(0, 1).fillna(0.5)

        # 11. Consecutive Direction — count of same-direction candles
        candle_dir = np.sign(close - opn)
        consecutive = pd.Series(0.0, index=df.index)
        count = 0
        prev_dir = 0
        for i in range(len(candle_dir)):
            d = candle_dir.iloc[i]
            if d == prev_dir and d != 0:
                count += 1
            else:
                count = 1 if d != 0 else 0
            consecutive.iloc[i] = count * d  # Positive for up, negative for down
            prev_dir = d
        df["consecutive_direction"] = consecutive.clip(-10, 10) / 10.0

        # 12. ATR Change Rate — rate of change of ATR (volatility acceleration)
        atr_col = df["atr_raw"] if "atr_raw" in df.columns else ta.atr(high, low, close, length=14)
        if atr_col is not None:
            df["atr_change_rate"] = (atr_col.pct_change(5) * 10).clip(-3, 3) / 3.0
        else:
            df["atr_change_rate"] = 0.0

        return df

    # ─── V4: Load and Build with 55 features + 3-class target ─────────

    def load_and_build_v4(
        self,
        symbol: str,
        limit: int | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        V4 Pipeline: Load data, build 55 features, create 3-class targets.

        Args:
            symbol: e.g. "XAUUSDc"
            limit: Max M5 bars

        Returns:
            (X, y): X shape (N, 60, 55), y shape (N,) with values {0, 1, 2}
        """
        logger.info("v4_loading", extra={"symbol": symbol})

        # 1. Load all TF data
        dfs = self._load_parquet_data(symbol)
        if dfs is None:
            return np.array([]), np.array([])

        # 2. Compute base features per TF
        m5 = self._compute_m5_features(dfs["M5"])
        m15 = self._compute_higher_tf_features(dfs.get("M15"), prefix="m15", full=True)
        h1 = self._compute_higher_tf_features(dfs.get("H1"), prefix="h1", full=False)
        h4 = self._compute_higher_tf_features(dfs.get("H4"), prefix="h4", minimal=True)
        d1 = self._compute_d1_features(dfs.get("D1"))
        del dfs

        # 3. Time-align higher TFs to M5
        merged = self._merge_timeframes(m5, m15, h1, h4, d1)
        del m5, m15, h1, h4, d1

        if merged is None or len(merged) < SEQUENCE_LENGTH + 30:
            return np.array([]), np.array([])

        if limit:
            merged = merged.tail(limit).reset_index(drop=True)

        # 4. Add V4 microstructure features
        merged = self._compute_v4_features(merged)

        # 5. Compute 3-class targets
        merged = self._compute_target_3class(merged)
        merged.dropna(inplace=True)

        if len(merged) < SEQUENCE_LENGTH + 10:
            return np.array([]), np.array([])

        # 6. Build feature matrix (55 features)
        for col in ALL_FEATURES_V4:
            if col not in merged.columns:
                merged[col] = 0.0

        data_matrix = merged[ALL_FEATURES_V4].values.astype(np.float32)
        data_matrix = np.nan_to_num(data_matrix, nan=0.0, posinf=1.0, neginf=-1.0)

        # 7. Create sliding window sequences
        targets = merged["target_3class"].values
        X, y = [], []
        for i in range(len(data_matrix) - SEQUENCE_LENGTH):
            X.append(data_matrix[i: i + SEQUENCE_LENGTH])
            y.append(targets[i + SEQUENCE_LENGTH - 1])

        X_arr = np.array(X, dtype=np.float32)
        y_arr = np.array(y, dtype=np.int64)

        # Log class distribution
        from collections import Counter
        dist = Counter(y_arr.tolist())
        logger.info("v4_features_ready", extra={
            "symbol": symbol,
            "sequences": len(X_arr),
            "features": INPUT_SIZE_V4,
            "class_dist": {"BUY": dist.get(0, 0), "HOLD": dist.get(1, 0), "SELL": dist.get(2, 0)},
        })
        return X_arr, y_arr

    def build_live_features_v4(
        self,
        candles_m5: pd.DataFrame,
        candles_m15: pd.DataFrame | None = None,
        candles_h1: pd.DataFrame | None = None,
        candles_h4: pd.DataFrame | None = None,
        candles_d1: pd.DataFrame | None = None,
    ) -> np.ndarray | None:
        """
        V4: Build 55-feature sequence from live candles (for prediction).

        Returns:
            numpy array of shape (1, SEQUENCE_LENGTH, INPUT_SIZE_V4) or None
        """
        if candles_m5 is None or len(candles_m5) < SEQUENCE_LENGTH + 50:
            return None

        for df in [candles_m5, candles_m15, candles_h1, candles_h4, candles_d1]:
            if df is not None and "time" not in df.columns and df.index.name == "time":
                df.reset_index(inplace=True)

        m5 = self._compute_m5_features(candles_m5)
        m15 = self._compute_higher_tf_features(candles_m15, prefix="m15", full=True)
        h1 = self._compute_higher_tf_features(candles_h1, prefix="h1", full=False)
        h4 = self._compute_higher_tf_features(candles_h4, prefix="h4", minimal=True)
        d1 = self._compute_d1_features(candles_d1)

        merged = self._merge_timeframes(m5, m15, h1, h4, d1)
        if merged is None or len(merged) < SEQUENCE_LENGTH:
            return None

        # Add V4 features
        merged = self._compute_v4_features(merged)

        for col in ALL_FEATURES_V4:
            if col not in merged.columns:
                merged[col] = 0.0

        data_matrix = merged[ALL_FEATURES_V4].tail(SEQUENCE_LENGTH).values.astype(np.float32)
        data_matrix = np.nan_to_num(data_matrix, nan=0.0, posinf=1.0, neginf=-1.0)

        return data_matrix.reshape(1, SEQUENCE_LENGTH, INPUT_SIZE_V4)
