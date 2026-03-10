import pandas as pd
import numpy as np
import logging
import joblib
from pathlib import Path
import pandas_ta as ta

logger = logging.getLogger("opus_logger")

class DeepBrainPredictor:
    """
    Antigravity Deep Brain
    Institutional-grade Neural Network for price action prediction.
    Enhanced with Local-Path Portability & Offline Training.
    """
    def __init__(self, symbol: str):
        self.symbol = symbol
        # New portable path: project-relative
        self.model_dir = Path(__file__).parent / "models"
        self.model_path = self.model_dir / f"{symbol}_deep_brain.joblib"
        self.scaler_path = self.model_dir / f"{symbol}_scaler.joblib"
        
        # Fallback to old path if not found in new path (for migration)
        self.legacy_dir = Path("D:/Trade/gold-risk-engine/backend/models")
        
        self.model = None
        self.scaler = None
        
        self._load_model()

    def _load_model(self):
        # 1. Generate variations to check
        # Strips common suffixes to try base symbol if needed
        base_symbol = self.symbol
        for suffix in ['m', 'c']:
            if self.symbol.endswith(suffix):
                base_symbol = self.symbol[:-len(suffix)]
                break
                
        variations = list(set([self.symbol, f"{self.symbol}m", f"{self.symbol}c", base_symbol, f"{base_symbol}m", f"{base_symbol}c"]))
        dirs_to_check = [self.model_dir, self.legacy_dir]
        
        for d in dirs_to_check:
            if not d.exists(): continue
            for var in variations:
                m_path = d / f"{var}_deep_brain.joblib"
                s_path = d / f"{var}_scaler.joblib"
                
                if m_path.exists() and s_path.exists():
                    try:
                        self.model = joblib.load(m_path)
                        self.scaler = joblib.load(s_path)
                        logger.info(f"BRAIN [{self.symbol}] Deep Brain loaded from {d.name} using model: {var}")
                        return # SUCCESS
                    except Exception as e:
                        logger.error(f"Failed to load Deep Brain for {var}: {e}")
        
        logger.warning(f"BRAIN [{self.symbol}] Deep Brain model files not found in local OR legacy paths (checked variations: {variations}).")

    def engineer_deep_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """Alpha-Capture Feature Engineering for Deep Learning (10 Core Features)"""
        try:
            df = df.copy()
            # Ensure price columns are numeric
            for col in ['open', 'high', 'low', 'close']:
                df[col] = pd.to_numeric(df[col], errors='coerce')

            # 1. Price Momentum Velocity
            df['roc_5'] = df.ta.roc(length=5)
            df['roc_13'] = df.ta.roc(length=13)
            
            # 2. Volatility Clusters
            atr = df.ta.atr(length=14)
            df['natr'] = (atr / df['close']) * 100
            
            # 3. Trend Alignment
            ema21 = df.ta.ema(length=21)
            ema50 = df.ta.ema(length=50)
            df['ema_gap'] = (ema21 - ema50) / df['close']
            
            # 4. Relative Strength
            df['rsi'] = df.ta.rsi(length=14)
            
            # 5. Price Action Tensors
            df['range'] = (df['high'] - df['low']) / df['close']
            df['upper_wick'] = (df['high'] - np.maximum(df['close'], df['open'])) / df['close']
            df['lower_wick'] = (np.minimum(df['close'], df['open']) - df['low']) / df['close']
            
            # 6. Cycle Features
            if 'time' in df.columns:
                dt_series = pd.to_datetime(df['time'], unit='s' if pd.api.types.is_numeric_dtype(df['time']) else None)
                df['hour_sin'] = np.sin(2 * np.pi * dt_series.dt.hour / 24)
                df['hour_cos'] = np.cos(2 * np.pi * dt_series.dt.hour / 24)
            else:
                df['hour_sin'] = 0.0
                df['hour_cos'] = 0.0

            df.replace([np.inf, -np.inf], np.nan, inplace=True)
            df = df.ffill()
            df = df.fillna(0)
            
            return df
        except Exception as e:
            logger.error(f"Feature Engineering Failed: {e}")
            return None

    def train_offline(self, df: pd.DataFrame, epochs: int = 100):
        """
        Train a new Deep Brain model on the fly using the 10 core features.
        Uses RandomForest as a robust, high-confidence classifier for $100-tier accounts.
        """
        try:
            from sklearn.ensemble import RandomForestClassifier
            from sklearn.preprocessing import StandardScaler
            
            logger.info(f"BRAIN [{self.symbol}] Starting Offline Brain Training...")
            
            # 1. Prepare Features
            df_feat = self.engineer_deep_features(df)
            if df_feat is None:
                logger.error(f"ERR [{self.symbol}] Feature engineering returned None")
                return False
                
            features = ['roc_5', 'roc_13', 'natr', 'ema_gap', 'rsi', 'range', 'upper_wick', 'lower_wick', 'hour_sin', 'hour_cos']
            
            # 2. Prepare Targets (Binary: Price direction in 12 bars vs 0.5 ATR)
            horizon = 12
            atr = df_feat.ta.atr(length=14).fillna(df_feat['close'] * 0.001)
            
            # Target: 1 if close in 12 bars > current close + 0.5*ATR, else 0
            # (Simplified for robust binary classification)
            future_close = df_feat['close'].shift(-horizon)
            target_move = 0.5 * atr
            
            # BUY if up, SELL if down
            y = np.where(future_close > (df_feat['close'] + target_move), 1, 
                         np.where(future_close < (df_feat['close'] - target_move), 0, -1))
            
            # Filter labels (remove -1/Neutral for binary training)
            # Filter labels (remove -1/Neutral for binary training)
            mask = (y != -1) & (~np.isnan(y))
            X = df_feat[features].iloc[mask] # Use DataFrame to store feature names
            y = y[mask]
            
            if len(X) < 500:
                logger.error(f"ERR [{self.symbol}] Insufficient data for training ({len(X)} samples)")
                return False

            # 3. Train Model
            self.scaler = StandardScaler()
            X_scaled = self.scaler.fit_transform(X) # Stores feature_names_in_
            
            self.model = RandomForestClassifier(n_estimators=epochs, max_depth=12, random_state=42)
            self.model.fit(X_scaled, y)
            
            # 4. Save to New Project Path
            self.model_dir.mkdir(parents=True, exist_ok=True)
            joblib.dump(self.model, self.model_path)
            joblib.dump(self.scaler, self.scaler_path)
            
            logger.info(f"OK [{self.symbol}] Brain Training Complete! Accuracy: {self.model.score(X_scaled, y):.2%}")
            return True
            
        except Exception as e:
            logger.error(f"ERR [{self.symbol}] Training Failed: {e}", exc_info=True)
            return False

    def evolve_from_experience(self):
        """
        Incremental learning/fine-tuning from shadow experience.
        Loads data from shadow_experience table and updates the model.
        """
        try:
            from backend.trader.storage.sqlite_db import db
            import json
            
            logger.info(f"🎓 BRAIN [{self.symbol}] Evolving from experience...")
            
            # 1. Get closed shadow trades with outcomes
            cursor = db.conn.cursor()
            cursor.execute("""
                SELECT features, outcome, side FROM shadow_experience 
                WHERE symbol = ? AND outcome != 0
                ORDER BY timestamp DESC LIMIT 2000
            """, (self.symbol,))
            
            rows = cursor.fetchall()
            if len(rows) < 50:
                logger.info(f"  ⏭️ Not enough new experience samples ({len(rows)}/50). Skipping evolution.")
                return False
                
            # 2. Prepare X and y
            X_list = []
            y_list = []
            
            features_keys = ['roc_5', 'roc_13', 'natr', 'ema_gap', 'rsi', 'range', 'upper_wick', 'lower_wick', 'hour_sin', 'hour_cos']
            
            for row in rows:
                feat_dict = json.loads(row[0])
                outcome = row[1]
                side = row[2].upper()
                
                X_row = [feat_dict.get(k, 0) for k in features_keys]
                
                # Determine intended direction label (1 or 0)
                # If BUY + Win -> Direction was Up (1)
                # If BUY + Loss -> Direction was Down (0)
                # If SELL + Win -> Direction was Down (0)
                # If SELL + Loss -> Direction was Up (1)
                if side == "BUY":
                    label = 1 if outcome == 1 else 0
                else: # SELL
                    label = 0 if outcome == 1 else 1
                
                X_list.append(X_row)
                y_list.append(label)

            X = pd.DataFrame(X_list, columns=features_keys)
            y = np.array(y_list)

            # 3. Fine-tune or retrain
            if self.scaler:
                # Robust handle for models fitted WITH and WITHOUT feature names
                if hasattr(self.scaler, "feature_names_in_"):
                    X_scaled = self.scaler.transform(X)
                else:
                    X_scaled = self.scaler.transform(X.values)
                    
                # RandomForest needs full fit on the new "Experience Replay" buffer
                self.model.fit(X_scaled, y)
                
                # Save
                joblib.dump(self.model, self.model_path)
                logger.info(f"✅ BRAIN [{self.symbol}] Evolution complete. Accuracy on exp: {self.model.score(X_scaled, y):.2%}")
                return True
                
            return False

        except Exception as e:
            logger.error(f"ERR [{self.symbol}] Evolution Failed: {e}")
            return False

    def predict_next_move(self, df_input: pd.DataFrame) -> tuple[str, float]:
        """Fast prediction using existing DataFrame."""
        if self.model is None or self.scaler is None:
            return "NEUTRAL", 0.0
            
        try:
            features = ['roc_5', 'roc_13', 'natr', 'ema_gap', 'rsi', 'range', 'upper_wick', 'lower_wick', 'hour_sin', 'hour_cos']
            
            missing = [f for f in features if f not in df_input.columns]
            if missing:
                df_calc = self.engineer_deep_features(df_input)
                if df_calc is None or df_calc.empty: return "NEUTRAL", 0.0
                latest_row = df_calc.iloc[[-1]][features]
            else:
                latest_row = df_input.iloc[[-1]][features]
            
            # Robust handle for models fitted WITH and WITHOUT feature names
            if hasattr(self.scaler, "feature_names_in_"):
                latest_scaled = self.scaler.transform(latest_row)
            else:
                latest_scaled = self.scaler.transform(latest_row.values)
            
            prediction = self.model.predict(latest_scaled)[0]
            probability = self.model.predict_proba(latest_scaled)[0]
            
            confidence = float(probability[prediction])
            result = "BUY" if prediction == 1 else "SELL"
            
            return result, confidence
            
        except Exception as e:
            logger.debug(f"DeepBrain fast predict error: {e}")
            return "NEUTRAL", 0.0

# Singleton Cache
_brains = {}

def get_brain(symbol: str) -> DeepBrainPredictor:
    if symbol not in _brains:
        _brains[symbol] = DeepBrainPredictor(symbol)
    return _brains[symbol]
