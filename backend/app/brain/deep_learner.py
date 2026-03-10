"""
DeepLearner V2 — Deep Learning module for profitable market prediction (LSTM).

V2 Changes:
    - INPUT_SIZE: 15 → 22 (22 real features, no placeholders)
    - HIDDEN_SIZE: 64 → 96 (more capacity)
    - DROPOUT: 0.2 → 0.3 (better regularization)
    - LR: 0.001 → 0.0005 (smoother convergence)
    - EPOCHS: 10 → 20 (more training per session)
    - Added: LR Scheduler (ReduceOnPlateau)
    - Added: Validation split (20%)
    - Added: Early stopping (patience=5)
    - Added: Gradient clipping (max_norm=1.0)
    - Added: Class-weighted BCE loss
    - Added: Validation accuracy logging

Architecture:
    - Input: Sequence of 22 technical features (last 60 bars)
    - Model: 2-Layer LSTM (96 hidden) + FC Head with Dropout
    - Output: Probability of ATR-based Win (0.0 - 1.0)
"""

import math
import time
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any

import numpy as np
import pandas as pd
import app.analysis.indicators as ind

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── V2 Config ───
MODEL_DIR = Path("backend/data/models")
MODEL_FILE = "deep_brain_v2.pth"
META_FILE = "deep_brain_v2_meta.json"
SEQUENCE_LENGTH = 60       # Lookback 60 bars
INPUT_SIZE = 22            # V2: 22 real features
HIDDEN_SIZE = 96           # V2: Bigger model
NUM_LAYERS = 2             # LSTM Layers
DROPOUT = 0.3              # V2: Stronger regularization
BATCH_SIZE = 64            # V2: Bigger batch for stability
LEARNING_RATE = 0.0005     # V2: Smoother convergence
EPOCHS = 20                # V2: More training per session
MIN_TRAIN_SAMPLES = 500
VALIDATION_SPLIT = 0.2     # V2: 20% validation
EARLY_STOP_PATIENCE = 5    # V2: Stop if no improvement for 5 epochs
GRAD_CLIP_NORM = 1.0       # V2: Gradient clipping

# check torch availability
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logger.warning("torch_not_found", extra={"detail": "DeepLearner disabled. Run 'pip install torch' to enable."})
    # Mock nn for class definition
    class nn:
        Module = object


class TradePredictorLSTM(nn.Module):
    """V2 LSTM Model for ATR-based market prediction."""
    
    def __init__(self, input_size=INPUT_SIZE, hidden_size=HIDDEN_SIZE, num_layers=NUM_LAYERS, dropout=DROPOUT):
        super(TradePredictorLSTM, self).__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            num_layers=num_layers,
            batch_first=True,
            dropout=0  # No inplace dropout in LSTM (causes gradient issues in threaded training)
        )
        self.post_lstm_dropout = nn.Dropout(dropout)
        self.fc_head = nn.Sequential(
            nn.Linear(hidden_size, 48),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(48, 16),
            nn.ReLU(),
            nn.Dropout(dropout * 0.5),
            nn.Linear(16, 1),
            nn.Sigmoid()
        )

    def forward(self, x):
        lstm_out, _ = self.lstm(x)
        last_step = lstm_out[:, -1, :]
        last_step = self.post_lstm_dropout(last_step)
        out = self.fc_head(last_step)
        return out


class DeepLearner:
    """
    V2 Manager for the Deep Learning Model.
    Handles training with validation, early stopping, LR scheduling, and inference.
    """

    def __init__(self, settings=None):
        self.settings = settings
        self._enabled = TORCH_AVAILABLE
        self._model = None
        self._optimizer = None
        self._scheduler = None
        self._criterion = None
        self._model_ready = False
        self._meta = {
            "version": "v2",
            "accuracy": 0.0,
            "val_accuracy": 0.0,
            "loss": 0.0,
            "val_loss": 0.0,
            "trained_at": None,
            "samples": 0,
            "epochs": 0,
            "best_val_loss": 999.0,
        }
        self.model_path = MODEL_DIR / MODEL_FILE
        self.meta_path = MODEL_DIR / META_FILE
        
        if self._enabled:
            self._init_model()
            self._load_model()

    def _init_model(self):
        """Initialize fresh V2 model architecture."""
        try:
            self._model = TradePredictorLSTM()
            self._criterion = nn.BCELoss()  # Will be replaced with weighted version during training
            self._optimizer = optim.Adam(self._model.parameters(), lr=LEARNING_RATE, weight_decay=1e-5)
            self._scheduler = optim.lr_scheduler.ReduceLROnPlateau(
                self._optimizer, mode='min', factor=0.5, patience=3
            )
        except Exception as e:
            logger.error("dl_init_error", extra={"error": str(e)})
            self._enabled = False

    # ────────────────────────────────────────────────────────────────
    # Inference
    # ────────────────────────────────────────────────────────────────

    def predict_pivotal(self, features_sequence: np.ndarray) -> float:
        """
        Predict probability of ATR-based Win.
        
        Args:
            features_sequence: numpy array of shape (SEQUENCE_LENGTH, INPUT_SIZE)
            
        Returns:
            float: 0.0 - 1.0 confidence. 0.5 if neutral/not ready.
        """
        if not self._enabled or not self._model_ready or self._model is None:
            return 0.5

        # Handle shape mismatches gracefully
        if len(features_sequence.shape) != 2:
            return 0.5
            
        seq_len, feat_dim = features_sequence.shape
        
        if feat_dim != INPUT_SIZE:
            # Feature dimension mismatch — pad or truncate
            if feat_dim < INPUT_SIZE:
                padding = np.zeros((seq_len, INPUT_SIZE - feat_dim))
                features_sequence = np.hstack([features_sequence, padding])
            else:
                features_sequence = features_sequence[:, :INPUT_SIZE]
        
        if seq_len < SEQUENCE_LENGTH:
            pad_len = SEQUENCE_LENGTH - seq_len
            padding = np.zeros((pad_len, INPUT_SIZE))
            features_sequence = np.vstack([padding, features_sequence])
        elif seq_len > SEQUENCE_LENGTH:
            features_sequence = features_sequence[-SEQUENCE_LENGTH:]

        try:
            self._model.eval()
            with torch.no_grad():
                x_tensor = torch.FloatTensor(features_sequence).unsqueeze(0)
                prediction = self._model(x_tensor)
                return float(prediction.item())
        except Exception as e:
            logger.debug("dl_predict_error", extra={"error": str(e)})
            return 0.5

    def predict_from_candles(self, candidates_df: pd.DataFrame) -> float:
        """
        V2: End-to-end prediction from candles DataFrame.
        Computes 22 features on the fly (matching data_loader V2).
        """
        if candidates_df is None or len(candidates_df) < SEQUENCE_LENGTH + 20:
            return 0.5
            
        try:
            df = candidates_df.copy()
            
            # Optimize: use last 200 bars max
            if len(df) > 200:
                df = df.iloc[-200:].copy()
            
            close = df["close"]
            high = df["high"]
            low = df["low"]
            volume = df["volume"] if "volume" in df else (df["tick_volume"] if "tick_volume" in df else pd.Series(0, index=df.index))

            # ──── Trend ────
            df["rsi"] = ta.rsi(close, length=14) / 100.0
            df["rsi_slope"] = df["rsi"].diff(5)
            
            atr = ta.atr(high, low, close, length=14)
            atr_avg = atr.rolling(42).mean()
            df["atr_ratio"] = (atr / atr_avg).clip(0, 3) / 3.0
            df["atr_pct"] = (atr / close * 100).clip(0, 5) / 5.0
            
            adx = ta.adx(high, low, close, length=14)
            if adx is not None:
                df["adx"] = adx.get("ADX_14", 0) / 100.0
                df["plus_di"] = adx.get("DMP_14", 0) / 100.0
                df["minus_di"] = adx.get("DMN_14", 0) / 100.0
            else:
                df["adx"] = 0.0; df["plus_di"] = 0.0; df["minus_di"] = 0.0
                
            ema9 = ta.ema(close, length=9)
            ema21 = ta.ema(close, length=21)
            ema50 = ta.ema(close, length=50)
            df["ema_cross"] = ((ema9 - ema21) / ema21 * 100).clip(-5, 5) / 5.0
            df["close_vs_ema50"] = ((close - ema50) / ema50 * 100).clip(-5, 5) / 5.0

            # ──── Momentum ────
            macd = ta.macd(close, fast=12, slow=26, signal=9)
            if macd is not None and "MACDh_12_26_9" in macd.columns:
                df["macd_hist"] = (macd["MACDh_12_26_9"] / atr).clip(-3, 3) / 3.0
            else:
                df["macd_hist"] = 0.0
            
            bb = ta.bbands(close, length=20, std=2)
            if bb is not None:
                bbl = bb.get("BBL_20_2.0")
                bbu = bb.get("BBU_20_2.0")
                if bbl is not None and bbu is not None:
                    df["bb_percent"] = ((close - bbl) / (bbu - bbl)).clip(0, 1)
                else:
                    df["bb_percent"] = 0.5
            else:
                df["bb_percent"] = 0.5
            
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
            df["candle_direction"] = np.sign(close - df["open"]).astype(float)
            
            # ──── Volume & Momentum ────
            vol_avg = volume.rolling(20).mean()
            df["vol_ratio"] = (volume / vol_avg).clip(0, 5) / 5.0
            df["momentum_3"] = (close.pct_change(3) * 100).clip(-5, 5) / 5.0

            # ──── Time (Cyclical) ────
            if hasattr(df.index, 'hour'):
                hour = df.index.hour
                day = df.index.dayofweek
            elif "time" in df.columns:
                if pd.api.types.is_numeric_dtype(df["time"]):
                    dt = pd.to_datetime(df["time"], unit='s')
                else:
                    dt = df["time"]
                hour = dt.dt.hour
                day = dt.dt.dayofweek
            else:
                hour = pd.Series(12, index=df.index)
                day = pd.Series(2, index=df.index)
                
            df["hour_sin"] = np.sin(2 * np.pi * hour / 24)
            df["hour_cos"] = np.cos(2 * np.pi * hour / 24)
            df["day_sin"] = np.sin(2 * np.pi * day / 5)
            df["day_cos"] = np.cos(2 * np.pi * day / 5)
            
            df["spread_norm"] = 0.5  # Live spread not available in candle data
            
            # ──── Select & Predict ────
            feature_cols = [
                "rsi", "rsi_slope", "atr_ratio", "atr_pct",
                "adx", "plus_di", "minus_di",
                "ema_cross", "close_vs_ema50",
                "macd_hist", "bb_percent", "stoch_k",
                "body_ratio", "wick_ratio", "candle_direction",
                "vol_ratio", "momentum_3",
                "hour_sin", "hour_cos", "day_sin", "day_cos",
                "spread_norm",
            ]
            
            df_feats = df[feature_cols].fillna(0.0)
            
            if len(df_feats) < SEQUENCE_LENGTH:
                return 0.5
                
            seq_data = df_feats.values[-SEQUENCE_LENGTH:].astype(np.float32)
            seq_data = np.nan_to_num(seq_data, nan=0.0, posinf=1.0, neginf=-1.0)
            
            return self.predict_pivotal(seq_data)
            
        except Exception as e:
            logger.debug("dl_prep_error", extra={"error": str(e)})
            return 0.5

    # ────────────────────────────────────────────────────────────────
    # V2 Training (with validation, early stopping, class weighting)
    # ────────────────────────────────────────────────────────────────

    def train_session(self, X_train: np.ndarray, y_train: np.ndarray) -> Dict[str, Any]:
        """
        V2 Training session with validation split, early stopping, LR scheduling.
        """
        if not self._enabled:
            return {"status": "disabled_or_missing_torch"}
            
        if len(X_train) < MIN_TRAIN_SAMPLES:
            return {"status": "insufficient_data", "samples": len(X_train)}

        # Reset optimizer & scheduler fresh to avoid stale gradient state
        self._optimizer = optim.Adam(self._model.parameters(), lr=LEARNING_RATE, weight_decay=1e-5)
        self._scheduler = optim.lr_scheduler.ReduceLROnPlateau(
            self._optimizer, mode='min', factor=0.5, patience=3
        )

        # ──── Validation Split ────
        n = len(X_train)
        val_size = int(n * VALIDATION_SPLIT)
        indices = np.random.permutation(n)
        train_idx = indices[val_size:]
        val_idx = indices[:val_size]
        
        X_tr, y_tr = X_train[train_idx], y_train[train_idx]
        X_val, y_val = X_train[val_idx], y_train[val_idx]
        
        # ──── Class Weighting ────
        pos_count = y_tr.sum()
        neg_count = len(y_tr) - pos_count
        if pos_count > 0 and neg_count > 0:
            pos_weight = neg_count / pos_count
        else:
            pos_weight = 1.0
        
        logger.info("dl_training_start_v2", extra={
            "train_samples": len(X_tr), "val_samples": len(X_val),
            "pos_ratio": round(float(pos_count / len(y_tr)), 3),
            "pos_weight": round(float(pos_weight), 2),
            "device": "cpu"
        })
        
        # Weighted BCE Loss
        criterion = nn.BCELoss(reduction='none')
        
        # DataLoaders
        train_dataset = torch.utils.data.TensorDataset(
            torch.FloatTensor(X_tr),
            torch.FloatTensor(y_tr).unsqueeze(1)
        )
        train_loader = torch.utils.data.DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True)
        
        X_val_tensor = torch.FloatTensor(X_val)
        y_val_tensor = torch.FloatTensor(y_val).unsqueeze(1)
        
        # ──── Training Loop ────
        best_val_loss = float('inf')
        patience_counter = 0
        epoch_stats = []
        start_time = time.monotonic()
        
        for epoch in range(EPOCHS):
            # ---- Train ----
            self._model.train()
            running_loss = 0.0
            correct = 0
            total = 0
            
            for X_batch, y_batch in train_loader:
                self._optimizer.zero_grad()
                outputs = self._model(X_batch)
                
                # Weighted loss
                raw_loss = criterion(outputs, y_batch)
                weights = torch.where(y_batch == 1, pos_weight, 1.0)
                loss = (raw_loss * weights).mean()
                
                loss.backward()
                # Gradient clipping
                torch.nn.utils.clip_grad_norm_(self._model.parameters(), GRAD_CLIP_NORM)
                self._optimizer.step()
                
                running_loss += loss.item()
                predicted = (outputs > 0.5).float()
                correct += (predicted == y_batch).sum().item()
                total += y_batch.size(0)
            
            train_loss = running_loss / len(train_loader)
            train_acc = correct / total if total > 0 else 0
            
            # ---- Validate ----
            self._model.eval()
            with torch.no_grad():
                val_outputs = self._model(X_val_tensor)
                val_loss_raw = criterion(val_outputs, y_val_tensor)
                val_weights = torch.where(y_val_tensor == 1, pos_weight, 1.0)
                val_loss = (val_loss_raw * val_weights).mean().item()
                val_predicted = (val_outputs > 0.5).float()
                val_acc = (val_predicted == y_val_tensor).float().mean().item()
            
            # LR Scheduler step
            self._scheduler.step(val_loss)
            
            epoch_stats.append({
                "epoch": epoch + 1, "train_loss": round(train_loss, 4),
                "val_loss": round(val_loss, 4), "train_acc": round(train_acc, 3),
                "val_acc": round(val_acc, 3)
            })
            
            # Log every 5 epochs
            if (epoch + 1) % 5 == 0:
                current_lr = self._optimizer.param_groups[0]['lr']
                logger.info("dl_epoch_v2", extra={
                    "epoch": epoch + 1, "train_loss": round(train_loss, 4),
                    "val_loss": round(val_loss, 4), "val_acc": round(val_acc, 3),
                    "lr": current_lr
                })
            
            # ---- Early Stopping ----
            if val_loss < best_val_loss:
                best_val_loss = val_loss
                patience_counter = 0
                # Save best model state
                best_state = {k: v.clone() for k, v in self._model.state_dict().items()}
            else:
                patience_counter += 1
                if patience_counter >= EARLY_STOP_PATIENCE:
                    logger.info("dl_early_stop", extra={"epoch": epoch + 1, "best_val_loss": round(best_val_loss, 4)})
                    break
        
        duration = time.monotonic() - start_time
        
        # Restore best model
        if best_state:
            self._model.load_state_dict(best_state)
        
        final_stats = epoch_stats[-1]
        
        # Update meta
        self._meta["loss"] = final_stats["train_loss"]
        self._meta["val_loss"] = final_stats["val_loss"]
        self._meta["accuracy"] = final_stats["train_acc"]
        self._meta["val_accuracy"] = final_stats["val_acc"]
        self._meta["trained_at"] = datetime.now(timezone.utc).isoformat()
        self._meta["samples"] = len(X_train)
        self._meta["epochs"] += len(epoch_stats)
        self._meta["best_val_loss"] = round(best_val_loss, 4)
        self._model_ready = True
        
        # Save
        self._save_model()
        
        logger.info("dl_training_complete_v2", extra={
            "final_train_loss": final_stats["train_loss"],
            "final_val_loss": final_stats["val_loss"],
            "final_val_acc": final_stats["val_acc"],
            "best_val_loss": round(best_val_loss, 4),
            "epochs_run": len(epoch_stats),
            "duration": round(duration, 1)
        })
        
        return {
            "status": "success",
            "train_loss": final_stats["train_loss"],
            "val_loss": final_stats["val_loss"],
            "val_accuracy": final_stats["val_acc"],
            "best_val_loss": round(best_val_loss, 4),
            "duration_seconds": round(duration, 2),
            "epochs_run": len(epoch_stats),
        }

    # ────────────────────────────────────────────────────────────────
    # Persistence
    # ────────────────────────────────────────────────────────────────

    def _save_model(self):
        try:
            MODEL_DIR.mkdir(parents=True, exist_ok=True)
            torch.save(self._model.state_dict(), self.model_path)
            with open(self.meta_path, 'w') as f:
                json.dump(self._meta, f)
            logger.info("dl_model_saved_v2")
        except Exception as e:
            logger.error("dl_save_error", extra={"error": str(e)})

    def _load_model(self):
        if not self.model_path.exists():
            return

        try:
            self._model.load_state_dict(torch.load(self.model_path, weights_only=True))
            if self.meta_path.exists():
                with open(self.meta_path, 'r') as f:
                    self._meta = json.load(f)
            
            self._model_ready = True
            logger.info("dl_model_loaded_v2", extra=self._meta)
        except Exception as e:
            logger.warning("dl_load_error_reinit", extra={"error": str(e)})
            # Re-init fresh model if load fails (e.g. architecture change)
            self._init_model()

    def get_status(self):
        return {
            "enabled": self._enabled,
            "ready": self._model_ready,
            "meta": self._meta,
            "backend": "pytorch" if TORCH_AVAILABLE else "none"
        }
