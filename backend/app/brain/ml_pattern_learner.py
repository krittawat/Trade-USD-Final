"""
MLPatternLearner — ML model ที่เรียนรู้ pattern จากผลเทรดจริง.

สถาปัตยกรรม:
    - scikit-learn RandomForestClassifier (lightweight, 8GB RAM safe)
    - Feature extraction จาก OHLCV windows (20 bars)
    - Train จากผลเทรดจริงใน brain.db
    - Predict probability of WIN สำหรับสภาวะตลาดปัจจุบัน

Features (per 20-bar window):
    - RSI, RSI slope
    - ATR, ATR ratio (current vs average)
    - ADX, +DI, -DI
    - EMA cross signal
    - Body/wick ratios
    - Volume ratio
    - Hour of day, day of week
    - Regime encoded
    - Spread ratio

Lifecycle:
    1. Extract features จาก historical OHLCV ที่จุดเข้าเทรด
    2. Label ด้วยผลเทรดจริง (WIN/LOSS) จาก brain.db
    3. Train RandomForest ทุก training cycle (~6 ชม.)
    4. Predict win probability สำหรับ current market state
    5. ใช้ confidence boost/dampen สำหรับ strategy selection

RAM Safety:
    - Model ~2MB, inference ~10MB peak
    - Training: batch processing, max 5000 samples
    - Model cached in memory; persisted to disk via joblib

กฎ:
    - ผลลัพธ์เป็น advisor เท่านั้น — ไม่ override Risk Engine
    - min 50 samples ก่อนเริ่ม predict (ป้องกัน overfitting)
    - ถ้า model ไม่พร้อม → return 0.5 (neutral)
"""

import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── Config ───
MODEL_DIR = Path("backend/data/models")
MODEL_FILE_TEMPLATE = "pattern_{symbol}.joblib"
MIN_SAMPLES = 50           # ขั้นต่ำก่อนเริ่ม predict
MAX_TRAIN_SAMPLES = 5000   # จำกัด training samples (RAM safety)
WINDOW_SIZE = 20           # จำนวนแท่งเทียนที่ใช้ extract features
N_ESTIMATORS = 50          # จำนวน trees (lightweight)
MAX_DEPTH = 10             # จำกัดความลึก (ป้องกัน overfit + RAM)


class MLPatternLearner:
    """
    Lightweight ML model สำหรับ predict trade outcomes.

    ใช้ RandomForestClassifier จาก scikit-learn.
    RAM footprint: ~15MB peak (train), ~5MB steady (inference).
    """

    def __init__(self, memory_store=None, settings=None) -> None:
        """
        Args:
            memory_store: MemoryStore สำหรับดึง training data
            settings: Settings สำหรับ config
        """
        self.memory = memory_store
        self.settings = settings
        self._models = {}
        self._feature_names = {}
        self._model_ready = {}
        self._last_train_time = {}
        self._train_samples = {}
        self._accuracy = {}

        # Config
        self._enabled = True
        self._min_samples = MIN_SAMPLES
        if settings:
            self._enabled = getattr(settings, 'ml_pattern_enabled', True)
            self._min_samples = getattr(settings, 'ml_min_samples', MIN_SAMPLES)

        # Try load existing models
        self._try_load_all_models()

    # ────────────────────────────────────────────────────────────────
    # predict() — Predict win probability
    # ────────────────────────────────────────────────────────────────

    def predict(
        self,
        candles: pd.DataFrame,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
        spread_ratio: float = 1.0,
        symbol: str = "ALL",
    ) -> float:
        """
        Predict win probability สำหรับสภาวะปัจจุบัน.

        Args:
            candles: DataFrame ที่มี [open, high, low, close, volume]
            regime: สภาวะตลาด (TRENDING_UP, RANGING, ...)
            session: trading session (ASIA, LONDON, NY, ...)
            spread_ratio: spread / average spread
            symbol: สัญลักษณ์ที่ต้องการ predict (ใช้โมเดลเฉพาะถ้ามี)

        Returns:
            float: 0.0-1.0 (probability of winning trade)
                   0.5 = neutral (model not ready or insufficient data)
        """
        model = self._models.get(symbol) or self._models.get("ALL")
        ready = self._model_ready.get(symbol) or self._model_ready.get("ALL", False)

        if not self._enabled or not ready or model is None:
            return 0.5

        try:
            features = self._extract_features(candles, regime, session, spread_ratio)
            if features is None:
                return 0.5

            # Predict probability of WIN class
            X = np.array([features])
            proba = model.predict_proba(X)

            # proba shape: (1, 2) → [P(LOSS), P(WIN)]
            win_prob = float(proba[0][1]) if proba.shape[1] == 2 else 0.5

            logger.debug("ml_prediction", extra={
                "win_prob": round(win_prob, 3),
                "regime": regime,
                "session": session,
            })

            return win_prob

        except Exception as e:
            logger.debug("ml_predict_error", extra={"error": str(e)})
            return 0.5

    # ────────────────────────────────────────────────────────────────
    # train() — Train model จาก historical data
    # ────────────────────────────────────────────────────────────────

    async def train(self, candles_by_symbol: dict[str, pd.DataFrame] | None = None, symbol: str = "ALL") -> dict:
        """
        Train ML model จาก historical trade outcomes.

        Args:
            candles_by_symbol: {symbol: candles_df} สำหรับ feature extraction
                              ถ้า None → ใช้เฉพาะ features จาก brain.db

        Returns:
            dict: {"samples": N, "accuracy": float, "status": str}
        """
        if not self._enabled:
            return {"samples": 0, "accuracy": 0.0, "status": "disabled"}

        try:
            # ─── 1. Collect training data ───
            X, y = await self._collect_training_data(candles_by_symbol, symbol)

            if len(X) < self._min_samples:
                return {
                    "samples": len(X),
                    "accuracy": 0.0,
                    "status": f"insufficient_data (need {self._min_samples}, have {len(X)})",
                }

            # ─── 2. Train/test split ───
            from sklearn.model_selection import train_test_split
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42, stratify=y,
            )

            # ─── 3. Train RandomForest ───
            from sklearn.ensemble import RandomForestClassifier
            model = RandomForestClassifier(
                n_estimators=N_ESTIMATORS,
                max_depth=MAX_DEPTH,
                min_samples_split=5,
                min_samples_leaf=3,
                class_weight="balanced",  # handle imbalanced win/loss
                random_state=42,
                n_jobs=1,  # single thread (8GB RAM safety)
            )
            model.fit(X_train, y_train)

            # ─── 4. Evaluate ───
            accuracy = model.score(X_test, y_test)

            # ─── 5. Only use if accuracy > 52% (better than random) ───
            if accuracy > 0.52:
                self._models[symbol] = model
                self._model_ready[symbol] = True
                self._accuracy[symbol] = accuracy
                self._train_samples[symbol] = len(X)
                self._last_train_time[symbol] = time.monotonic()

                # Persist model
                self._save_model(symbol)

                logger.info("ml_model_trained", extra={
                    "symbol": symbol,
                    "samples": len(X),
                    "accuracy": round(accuracy, 4),
                    "features": len(self._feature_names.get(symbol, [])),
                    "train_size": len(X_train),
                    "test_size": len(X_test),
                })

                return {
                    "samples": len(X),
                    "accuracy": round(accuracy, 4),
                    "status": "trained_ok",
                }
            else:
                logger.warning("ml_model_too_weak", extra={
                    "accuracy": round(accuracy, 4),
                    "threshold": 0.52,
                })
                return {
                    "samples": len(X),
                    "accuracy": round(accuracy, 4),
                    "status": "accuracy_too_low",
                }

        except Exception as e:
            logger.error("ml_train_error", extra={"error": str(e)}, exc_info=True)
            return {"samples": 0, "accuracy": 0.0, "status": f"error: {e}"}

    # ────────────────────────────────────────────────────────────────
    # Feature extraction
    # ────────────────────────────────────────────────────────────────

    def _extract_features(
        self,
        candles: pd.DataFrame,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
        spread_ratio: float = 1.0,
    ) -> list[float] | None:
        """Extract feature vector จาก OHLCV candles (last WINDOW_SIZE bars)."""
        if candles is None or len(candles) < WINDOW_SIZE + 10:
            return None

        try:
            import app.analysis.indicators as ind

            c = candles.tail(WINDOW_SIZE + 10).copy()
            close = c["close"]
            high = c["high"]
            low = c["low"]
            volume = c.get("volume", pd.Series([0] * len(c)))

            # ─── Momentum ───
            rsi = ta.rsi(close, length=14)
            rsi_val = float(rsi.iloc[-1]) if rsi is not None and not pd.isna(rsi.iloc[-1]) else 50.0
            rsi_slope = float(rsi.iloc[-1] - rsi.iloc[-5]) if rsi is not None and len(rsi) >= 5 and not pd.isna(rsi.iloc[-5]) else 0.0

            # ─── Volatility ───
            atr = ta.atr(high, low, close, length=14)
            atr_val = float(atr.iloc[-1]) if atr is not None and not pd.isna(atr.iloc[-1]) else 0.0
            atr_avg = float(atr.iloc[-42:].mean()) if atr is not None and len(atr) >= 14 else atr_val
            atr_ratio = atr_val / atr_avg if atr_avg > 0 else 1.0

            # ─── Trend ───
            adx_df = ta.adx(high, low, close, length=14)
            adx_val = 25.0
            plus_di = 25.0
            minus_di = 25.0
            if adx_df is not None:
                adx_col = f"ADX_14"
                if adx_col in adx_df.columns and not pd.isna(adx_df[adx_col].iloc[-1]):
                    adx_val = float(adx_df[adx_col].iloc[-1])
                if "DMP_14" in adx_df.columns and not pd.isna(adx_df["DMP_14"].iloc[-1]):
                    plus_di = float(adx_df["DMP_14"].iloc[-1])
                if "DMN_14" in adx_df.columns and not pd.isna(adx_df["DMN_14"].iloc[-1]):
                    minus_di = float(adx_df["DMN_14"].iloc[-1])

            # ─── EMA Cross ───
            ema9 = ta.ema(close, length=9)
            ema21 = ta.ema(close, length=21)
            ema_cross = 0.0
            if ema9 is not None and ema21 is not None:
                e9 = float(ema9.iloc[-1]) if not pd.isna(ema9.iloc[-1]) else 0
                e21 = float(ema21.iloc[-1]) if not pd.isna(ema21.iloc[-1]) else 0
                if e21 > 0:
                    ema_cross = (e9 - e21) / e21 * 100  # percentage diff

            # ─── Candle Structure (last bar) ───
            last = c.iloc[-1]
            body = abs(last["close"] - last["open"])
            candle_range = last["high"] - last["low"]
            body_ratio = body / candle_range if candle_range > 0 else 0.5
            upper_wick = last["high"] - max(last["open"], last["close"])
            lower_wick = min(last["open"], last["close"]) - last["low"]
            wick_ratio = (upper_wick + lower_wick) / candle_range if candle_range > 0 else 0.5

            # ─── Multi-bar Shape Features (5-bar lookback) ───
            n_bars = min(5, len(c) - 1)
            tail_bars = c.iloc[-(n_bars + 1):]
            tb_body = (tail_bars["close"] - tail_bars["open"]).abs()
            tb_range = (tail_bars["high"] - tail_bars["low"]).replace(0, 1e-10)
            tb_body_ratio = tb_body / tb_range
            tb_wick = tb_range - tb_body
            tb_wick_ratio = tb_wick / tb_range

            avg_body_ratio_5 = float(tb_body_ratio.iloc[-n_bars:].mean())
            avg_wick_ratio_5 = float(tb_wick_ratio.iloc[-n_bars:].mean())

            # Body trend: positive = bodies getting bigger (momentum building)
            if n_bars >= 3:
                br_vals = tb_body_ratio.iloc[-n_bars:].values
                x = np.arange(len(br_vals))
                if np.std(br_vals) > 0:
                    body_trend = float(np.polyfit(x, br_vals, 1)[0])
                else:
                    body_trend = 0.0
            else:
                body_trend = 0.0

            # Direction consistency: ratio of bullish bars in last N bars
            is_bull = (tail_bars["close"] > tail_bars["open"]).iloc[-n_bars:]
            direction_consistency = float(is_bull.sum()) / n_bars if n_bars > 0 else 0.5

            # Range expansion: recent range vs 20-bar range
            range_20 = float(tb_range.iloc[-min(20, len(tb_range)):].mean())
            range_5 = float(tb_range.iloc[-n_bars:].mean())
            range_expansion = range_5 / range_20 if range_20 > 0 else 1.0

            # Close position: where is close within 20-bar range?
            hi_20 = float(c["high"].iloc[-min(20, len(c)):].max())
            lo_20 = float(c["low"].iloc[-min(20, len(c)):].min())
            close_position = (float(close.iloc[-1]) - lo_20) / (hi_20 - lo_20) if (hi_20 - lo_20) > 0 else 0.5

            # ─── Volume ───
            vol_current = float(volume.iloc[-1]) if not pd.isna(volume.iloc[-1]) else 0
            vol_avg = float(volume.iloc[-20:].mean()) if len(volume) >= 20 else vol_current
            vol_ratio = vol_current / vol_avg if vol_avg > 0 else 1.0

            # ─── Temporal ───
            last_time = c.index[-1] if hasattr(c.index[-1], 'hour') else None
            hour = float(last_time.hour) / 24.0 if last_time and hasattr(last_time, 'hour') else 0.5
            day = float(last_time.weekday()) / 6.0 if last_time and hasattr(last_time, 'weekday') else 0.5

            # ─── Regime encoding ───
            regime_map = {
                "TRENDING_UP": 1.0,
                "TRENDING_DOWN": -1.0,
                "RANGING": 0.0,
                "HIGH_VOLATILITY": 0.5,
                "LOW_VOLATILITY": -0.5,
                "UNKNOWN": 0.0,
            }
            regime_val = regime_map.get(regime, 0.0)

            # ─── Session encoding ───
            session_map = {
                "ASIA": 0.0,
                "LONDON": 0.33,
                "NY": 0.67,
                "OVERLAP": 1.0,
                "CLOSED": -1.0,
            }
            session_val = session_map.get(session, 0.0)

            features = [
                rsi_val / 100.0,        # normalized 0-1
                rsi_slope / 100.0,      # normalized
                atr_ratio,              # already ratio
                adx_val / 100.0,        # normalized
                plus_di / 100.0,
                minus_di / 100.0,
                ema_cross / 10.0,       # scaled
                body_ratio,             # 0-1
                wick_ratio,             # 0-1
                avg_body_ratio_5,       # 0-1 (multi-bar)
                avg_wick_ratio_5,       # 0-1 (multi-bar)
                min(max(body_trend, -1), 1),  # clamped
                direction_consistency,  # 0-1
                min(range_expansion, 3.0) / 3.0,  # capped
                min(max(close_position, 0), 1),   # 0-1
                min(vol_ratio, 5.0) / 5.0,  # capped and normalized
                hour,                   # 0-1
                day,                    # 0-1
                regime_val,             # -1 to 1
                session_val,            # -1 to 1
                min(spread_ratio, 5.0) / 5.0,  # capped and normalized
            ]

            feature_names = [
                "rsi", "rsi_slope", "atr_ratio", "adx",
                "plus_di", "minus_di", "ema_cross",
                "body_ratio", "wick_ratio",
                "avg_body_5", "avg_wick_5", "body_trend",
                "dir_consistency", "range_expansion", "close_pos",
                "vol_ratio",
                "hour", "day", "regime", "session", "spread_ratio",
            ]
            self._feature_names["ALL"] = feature_names

            # Sanitize NaN/Inf
            features = [0.0 if (math.isnan(f) or math.isinf(f)) else f for f in features]

            return features

        except Exception as e:
            logger.debug("feature_extraction_error", extra={"error": str(e)})
            return None

    # ────────────────────────────────────────────────────────────────
    # Training data collection
    # ────────────────────────────────────────────────────────────────

    async def _collect_training_data(
        self,
        candles_by_symbol: dict[str, pd.DataFrame] | None = None,
        target_symbol: str = "ALL",
    ) -> tuple[np.ndarray, np.ndarray]:
        """Collect training samples from brain.db + candle features."""
        X_list = []
        y_list = []

        if not self.memory or not self.memory._conn:
            return np.array(X_list), np.array(y_list)

        # Get performance records
        try:
            query = """
                SELECT strategy_name, symbol, regime, session,
                       win_rate, total_trades, total_wins, total_losses
                FROM strategy_performance
                WHERE total_trades >= 3
            """
            params = []
            if target_symbol != "ALL":
                query += " AND symbol = ?"
                params.append(target_symbol)
            query += " ORDER BY updated_at DESC LIMIT ?"
            params.append(MAX_TRAIN_SAMPLES)
            
            rows = self.memory._conn.execute(query, params).fetchall()
        except Exception:
            rows = []

        if not rows:
            return np.array(X_list), np.array(y_list)

        # For each record, generate synthetic features from stored regime/session
        for row in rows:
            regime = row["regime"] or "UNKNOWN"
            session = row["session"] or "CLOSED"
            win_rate = row["win_rate"] or 0.5
            total_trades = row["total_trades"] or 0

            # Generate features from candles if available
            symbol = row["symbol"]
            if candles_by_symbol and symbol in candles_by_symbol:
                candles = candles_by_symbol[symbol]
                if candles is not None and len(candles) >= WINDOW_SIZE + 10:
                    features = self._extract_features(candles, regime, session)
                    if features:
                        # Create multiple samples based on win/loss ratio
                        wins = row["total_wins"] or 0
                        losses = row["total_losses"] or 0

                        # Add win samples with slight variation
                        for i in range(min(wins, 50)):
                            varied = self._vary_features(features, i)
                            X_list.append(varied)
                            y_list.append(1)

                        # Add loss samples with slight variation
                        for i in range(min(losses, 50)):
                            varied = self._vary_features(features, i + 100)
                            X_list.append(varied)
                            y_list.append(0)
            else:
                # Synthetic features from aggregated data
                regime_map = {
                    "TRENDING_UP": 1.0, "TRENDING_DOWN": -1.0,
                    "RANGING": 0.0, "HIGH_VOLATILITY": 0.5,
                    "LOW_VOLATILITY": -0.5, "UNKNOWN": 0.0,
                }
                session_map = {
                    "ASIA": 0.0, "LONDON": 0.33, "NY": 0.67,
                    "OVERLAP": 1.0, "CLOSED": -1.0,
                }

                base_features = [
                    0.5,  # rsi (neutral)
                    0.0,  # rsi_slope
                    1.0,  # atr_ratio
                    0.25, # adx (normalized)
                    0.25, # plus_di
                    0.25, # minus_di
                    0.0,  # ema_cross
                    0.5,  # body_ratio
                    0.5,  # wick_ratio
                    0.2,  # vol_ratio
                    0.5,  # hour
                    0.5,  # day
                    regime_map.get(regime, 0.0),
                    session_map.get(session, 0.0),
                    0.2,  # spread_ratio
                ]

                wins = row["total_wins"] or 0
                losses = row["total_losses"] or 0

                for i in range(min(wins, 20)):
                    varied = self._vary_features(base_features, i)
                    X_list.append(varied)
                    y_list.append(1)

                for i in range(min(losses, 20)):
                    varied = self._vary_features(base_features, i + 100)
                    X_list.append(varied)
                    y_list.append(0)

        X = np.array(X_list) if X_list else np.array([]).reshape(0, 15)
        y = np.array(y_list) if y_list else np.array([])

        return X, y

    def _vary_features(self, features: list[float], seed: int) -> list[float]:
        """Add small random variation to features (data augmentation)."""
        rng = np.random.RandomState(seed)
        noise = rng.normal(0, 0.05, len(features))
        return [max(-1.0, min(2.0, f + n)) for f, n in zip(features, noise)]

    # ────────────────────────────────────────────────────────────────
    # Model persistence
    # ────────────────────────────────────────────────────────────────

    def _save_model(self, symbol: str) -> None:
        """Save model to disk via joblib."""
        try:
            import joblib
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            model_path = MODEL_DIR / MODEL_FILE_TEMPLATE.format(symbol=symbol)
            joblib.dump({
                "model": self._models[symbol],
                "feature_names": self._feature_names.get(symbol, self._feature_names.get("ALL", [])),
                "accuracy": self._accuracy[symbol],
                "train_samples": self._train_samples[symbol],
                "symbol": symbol,
                "trained_at": datetime.now(timezone.utc).isoformat(),
            }, model_path)
            logger.info("ml_model_saved", extra={"path": str(model_path), "symbol": symbol})
        except Exception as e:
            logger.error("ml_model_save_error", extra={"error": str(e), "symbol": symbol})

    def _try_load_all_models(self) -> None:
        """Try loading all existing models from disk."""
        if not MODEL_DIR.exists():
            return

        try:
            import joblib
            for model_path in MODEL_DIR.glob("pattern_*.joblib"):
                try:
                    filename = model_path.name
                    symbol = filename.replace("pattern_", "").replace(".joblib", "")
                    if not symbol:
                        symbol = "ALL"
                        
                    data = joblib.load(model_path)
                    self._models[symbol] = data["model"]
                    self._feature_names[symbol] = data.get("feature_names", [])
                    self._accuracy[symbol] = data.get("accuracy", 0.0)
                    self._train_samples[symbol] = data.get("train_samples", 0)
                    self._model_ready[symbol] = True
                    logger.info("ml_model_loaded", extra={
                        "symbol": symbol,
                        "accuracy": self._accuracy[symbol],
                        "samples": self._train_samples[symbol],
                    })
                except Exception as ex:
                    logger.warning("ml_model_load_failed", extra={"file": str(model_path), "error": str(ex)})
        except ImportError:
            pass

    # ────────────────────────────────────────────────────────────────
    # Status (for API/dashboard)
    # ────────────────────────────────────────────────────────────────

    def get_status(self, symbol: str = "ALL") -> dict:
        """Model status สำหรับ dashboard."""
        return {
            "enabled": self._enabled,
            "model_ready": self._model_ready.get(symbol, self._model_ready.get("ALL", False)),
            "accuracy": round(self._accuracy.get(symbol, 0.0), 4),
            "train_samples": self._train_samples.get(symbol, 0),
            "feature_count": len(self._feature_names.get(symbol, [])),
            "feature_names": self._feature_names.get(symbol, []),
            "loaded_models": list(self._models.keys()),
        }
