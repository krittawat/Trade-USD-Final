import os
import re

with open('app/brain/ml_pattern_learner.py', 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace('MODEL_FILE = "pattern_model.joblib"', 'MODEL_FILE_TEMPLATE = "pattern_{symbol}.joblib"')

init_old = '''        self._model = None
        self._feature_names: list[str] = []
        self._model_ready = False
        self._last_train_time: float = 0.0
        self._train_samples: int = 0
        self._accuracy: float = 0.0
        self._model_path = MODEL_DIR / MODEL_FILE

        # Config
        self._enabled = True
        self._min_samples = MIN_SAMPLES
        if settings:
            self._enabled = getattr(settings, 'ml_pattern_enabled', True)
            self._min_samples = getattr(settings, 'ml_min_samples', MIN_SAMPLES)

        # Try load existing model
        self._try_load_model()'''
init_new = '''        self._models = {}
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
        self._try_load_all_models()'''
text = text.replace(init_old, init_new)

predict_old = '''    def predict(
        self,
        candles: pd.DataFrame,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
        spread_ratio: float = 1.0,
    ) -> float:
        """
        Predict win probability สำหรับสภาวะปัจจุบัน.

        Args:
            candles: DataFrame ที่มี [open, high, low, close, volume]
            regime: สภาวะตลาด (TRENDING_UP, RANGING, ...)
            session: trading session (ASIA, LONDON, NY, ...)
            spread_ratio: spread / average spread

        Returns:
            float: 0.0-1.0 (probability of winning trade)
                   0.5 = neutral (model not ready or insufficient data)
        """
        if not self._enabled or not self._model_ready or self._model is None:
            return 0.5'''
predict_new = '''    def predict(
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
            return 0.5'''
text = text.replace(predict_old, predict_new)

text = text.replace('proba = self._model.predict_proba(X)', 'proba = model.predict_proba(X)')

train_old = '''    async def train(self, candles_by_symbol: dict[str, pd.DataFrame] | None = None) -> dict:'''
train_new = '''    async def train(self, candles_by_symbol: dict[str, pd.DataFrame] | None = None, symbol: str = "ALL") -> dict:'''
text = text.replace(train_old, train_new)

train_logic_old = '''            # ─── 1. Collect training data ───
            X, y = await self._collect_training_data(candles_by_symbol)'''
train_logic_new = '''            # ─── 1. Collect training data ───
            X, y = await self._collect_training_data(candles_by_symbol, symbol)'''
text = text.replace(train_logic_old, train_logic_new)

save_logic_old = '''                self._model = model
                self._model_ready = True
                self._accuracy = accuracy
                self._train_samples = len(X)
                self._last_train_time = time.monotonic()

                # Persist model
                self._save_model()

                logger.info("ml_model_trained", extra={
                    "samples": len(X),
                    "accuracy": round(accuracy, 4),
                    "features": len(self._feature_names),'''
save_logic_new = '''                self._models[symbol] = model
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
                    "features": len(self._feature_names.get(symbol, [])),'''
text = text.replace(save_logic_old, save_logic_new)

collect_old = '''    async def _collect_training_data(
        self,
        candles_by_symbol: dict[str, pd.DataFrame] | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:'''
collect_new = '''    async def _collect_training_data(
        self,
        candles_by_symbol: dict[str, pd.DataFrame] | None = None,
        target_symbol: str = "ALL",
    ) -> tuple[np.ndarray, np.ndarray]:'''
text = text.replace(collect_old, collect_new)

collect_sql_old = '''        try:
            rows = self.memory._conn.execute("""
                SELECT strategy_name, symbol, regime, session,
                       win_rate, total_trades, total_wins, total_losses
                FROM strategy_performance
                WHERE total_trades >= 3
                ORDER BY updated_at DESC
                LIMIT ?
            """, (MAX_TRAIN_SAMPLES,)).fetchall()'''
collect_sql_new = '''        try:
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
            
            rows = self.memory._conn.execute(query, params).fetchall()'''
text = text.replace(collect_sql_old, collect_sql_new)

feat_old = '''            self._feature_names = [
                "rsi", "rsi_slope", "atr_ratio", "adx",
                "plus_di", "minus_di", "ema_cross",
                "body_ratio", "wick_ratio",
                "avg_body_5", "avg_wick_5", "body_trend",
                "dir_consistency", "range_expansion", "close_pos",
                "vol_ratio",
                "hour", "day", "regime", "session", "spread_ratio",
            ]'''
feat_new = '''            feature_names = [
                "rsi", "rsi_slope", "atr_ratio", "adx",
                "plus_di", "minus_di", "ema_cross",
                "body_ratio", "wick_ratio",
                "avg_body_5", "avg_wick_5", "body_trend",
                "dir_consistency", "range_expansion", "close_pos",
                "vol_ratio",
                "hour", "day", "regime", "session", "spread_ratio",
            ]
            self._feature_names["ALL"] = feature_names'''
text = text.replace(feat_old, feat_new)

pers_old = '''    def _save_model(self) -> None:
        """Save model to disk via joblib."""
        try:
            import joblib
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            joblib.dump({
                "model": self._model,
                "feature_names": self._feature_names,
                "accuracy": self._accuracy,
                "train_samples": self._train_samples,
                "trained_at": datetime.now(timezone.utc).isoformat(),
            }, self._model_path)
            logger.info("ml_model_saved", extra={"path": str(self._model_path)})
        except Exception as e:
            logger.error("ml_model_save_error", extra={"error": str(e)})

    def _try_load_model(self) -> None:
        """Try loading existing model from disk."""
        if not self._model_path.exists():
            return

        try:
            import joblib
            data = joblib.load(self._model_path)
            self._model = data["model"]
            self._feature_names = data.get("feature_names", [])
            self._accuracy = data.get("accuracy", 0.0)
            self._train_samples = data.get("train_samples", 0)
            self._model_ready = True
            logger.info("ml_model_loaded", extra={
                "accuracy": self._accuracy,
                "samples": self._train_samples,
            })
        except Exception as e:
            logger.warning("ml_model_load_failed", extra={"error": str(e)})'''

pers_new = '''    def _save_model(self, symbol: str) -> None:
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
            pass'''
text = text.replace(pers_old, pers_new)

status_old = '''    def get_status(self) -> dict:
        """Model status สำหรับ dashboard."""
        return {
            "enabled": self._enabled,
            "model_ready": self._model_ready,
            "accuracy": round(self._accuracy, 4),
            "train_samples": self._train_samples,
            "feature_count": len(self._feature_names),
            "feature_names": self._feature_names,
        }'''
status_new = '''    def get_status(self, symbol: str = "ALL") -> dict:
        """Model status สำหรับ dashboard."""
        return {
            "enabled": self._enabled,
            "model_ready": self._model_ready.get(symbol, self._model_ready.get("ALL", False)),
            "accuracy": round(self._accuracy.get(symbol, 0.0), 4),
            "train_samples": self._train_samples.get(symbol, 0),
            "feature_count": len(self._feature_names.get(symbol, [])),
            "feature_names": self._feature_names.get(symbol, []),
            "loaded_models": list(self._models.keys()),
        }'''
text = text.replace(status_old, status_new)

with open('app/brain/ml_pattern_learner.py', 'w', encoding='utf-8') as f:
    f.write(text)

print('Update complete')
