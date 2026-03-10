"""
Deep Model V4 — Transformer-GRU Hybrid for Per-Symbol Trading Prediction.

Architecture:
    Input(60, 55) → Conv1D-Gate → GRU(128, 2L) → TransformerEncoder(2L, 4H)
    → Attention → FC(128→64→3) → Softmax

Per-Symbol Design:
    - Separate model per symbol (XAUUSDc, XAGUSDc, BTCUSDc)
    - Each learns symbol-specific patterns, microstructure, session behavior
    - 3-class output: BUY / HOLD / SELL (model learns when NOT to trade)

Improvements over V3 (GRU+Attention):
    - Conv1D gate: extracts local candle-cluster patterns
    - Transformer encoder: captures long-range session/trend dependencies
    - 3-class output: reduces false signals by explicitly predicting HOLD
    - 55 features: adds divergence, volume profile, session encoding
    - Per-symbol: no cross-symbol noise in training

RAM Safety (8GB target):
    - Model ~5MB per symbol × 3 = ~15MB total
    - Training peak: ~80MB
    - Inference: ~20MB
"""

import os
import json
import time
import numpy as np
from typing import Optional
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── Hyperparameters ──────────────────────────────────────────────
SEQUENCE_LENGTH = 60
INPUT_SIZE_V4 = 55     # V4: 43 MTF + 12 microstructure
HIDDEN_SIZE = 128
NUM_GRU_LAYERS = 2
NUM_TRANSFORMER_LAYERS = 2
NUM_HEADS = 4
DROPOUT = 0.3
BATCH_SIZE = 64
LEARNING_RATE = 0.0008
EPOCHS = 30
MIN_TRAIN_SAMPLES = 500
VALIDATION_SPLIT = 0.2
PATIENCE = 10          # Early stopping patience
NUM_CLASSES = 3        # BUY=0, HOLD=1, SELL=2

# Prediction thresholds
MIN_ACTION_PROB = 0.40       # BUY/SELL must exceed this
MAX_HOLD_PROB = 0.45         # HOLD must be below this
DEFAULT_CONFIDENCE_GAP = 0.05  # Default: winner must beat runner-up by this margin

# Per-symbol confidence gaps (tuned via backtest)
SYMBOL_CONFIDENCE_GAP = {
    "XAGUSDc": 0.10,  # XAG has tight confidence distribution, needs wider gap
}

MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "models")
MODEL_PREFIX = "deep_v4"

# ─── Labels ───────────────────────────────────────────────────────
LABEL_BUY = 0
LABEL_HOLD = 1
LABEL_SELL = 2
LABEL_NAMES = {0: "BUY", 1: "HOLD", 2: "SELL"}

# ─── PyTorch Import ───────────────────────────────────────────────
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torch.utils.data import TensorDataset, DataLoader
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
    logger.warning("torch_not_found", extra={
        "detail": "Deep Model V4 disabled. Run 'pip install torch' to enable."
    })

    class nn:
        Module = object


# ═══════════════════════════════════════════════════════════════════
# MODEL COMPONENTS
# ═══════════════════════════════════════════════════════════════════

class ConvGateBlock(nn.Module):
    """1D Convolution with GLU gating — extracts local candle-cluster patterns."""

    def __init__(self, input_size: int, conv_channels: int = 64, kernel_size: int = 3):
        super().__init__()
        padding = kernel_size // 2
        # GLU needs 2× channels (split into value and gate)
        self.conv = nn.Conv1d(
            in_channels=input_size,
            out_channels=conv_channels * 2,
            kernel_size=kernel_size,
            padding=padding,
        )
        self.norm = nn.LayerNorm(conv_channels)
        self.out_size = conv_channels

    def forward(self, x):
        # x: (batch, seq_len, input_size)
        x_t = x.transpose(1, 2)                    # (batch, input_size, seq_len)
        conv_out = self.conv(x_t)                   # (batch, conv_channels*2, seq_len)
        conv_out = conv_out.transpose(1, 2)         # (batch, seq_len, conv_channels*2)

        # GLU gating
        value, gate = conv_out.chunk(2, dim=-1)     # each (batch, seq_len, conv_channels)
        gated = value * torch.sigmoid(gate)         # (batch, seq_len, conv_channels)

        return self.norm(gated)                     # (batch, seq_len, conv_channels)


class Attention(nn.Module):
    """Learned attention over sequence — selects important time steps."""

    def __init__(self, hidden_size: int):
        super().__init__()
        self.attn = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.Tanh(),
            nn.Linear(hidden_size // 2, 1),
        )

    def forward(self, sequence_output):
        # sequence_output: (batch, seq_len, hidden_size)
        scores = self.attn(sequence_output)              # (batch, seq_len, 1)
        weights = torch.softmax(scores, dim=1)           # (batch, seq_len, 1)
        context = (sequence_output * weights).sum(dim=1)  # (batch, hidden_size)
        return context, weights


# ═══════════════════════════════════════════════════════════════════
# MAIN MODEL: Transformer-GRU Hybrid
# ═══════════════════════════════════════════════════════════════════

class TransformerGRU(nn.Module):
    """
    Transformer-GRU Hybrid for market prediction.

    Flow:
        Input → Conv1D-Gate → GRU → TransformerEncoder → Attention → Classifier

    Conv1D extracts local patterns (candle clusters, micro-trends).
    GRU captures sequential dependencies.
    Transformer captures long-range patterns (session cycles, macro trends).
    Attention focuses on the most important time steps.
    """

    def __init__(
        self,
        input_size: int = INPUT_SIZE_V4,
        hidden_size: int = HIDDEN_SIZE,
        num_gru_layers: int = NUM_GRU_LAYERS,
        num_transformer_layers: int = NUM_TRANSFORMER_LAYERS,
        num_heads: int = NUM_HEADS,
        num_classes: int = NUM_CLASSES,
        dropout: float = DROPOUT,
    ):
        super().__init__()

        # Stage 1: Conv1D Gate — extract local patterns
        self.conv_gate = ConvGateBlock(
            input_size=input_size,
            conv_channels=hidden_size,
            kernel_size=3,
        )

        # Stage 2: GRU — sequential processing
        self.gru = nn.GRU(
            input_size=self.conv_gate.out_size,
            hidden_size=hidden_size,
            num_layers=num_gru_layers,
            batch_first=True,
            dropout=dropout if num_gru_layers > 1 else 0,
        )

        # Stage 3: Transformer Encoder — long-range dependencies
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=hidden_size,
            nhead=num_heads,
            dim_feedforward=hidden_size * 2,
            dropout=dropout,
            batch_first=True,
            activation="gelu",
        )
        self.transformer = nn.TransformerEncoder(
            encoder_layer,
            num_layers=num_transformer_layers,
        )

        # Stage 4: Attention — focus on important time steps
        self.attention = Attention(hidden_size)

        # Stage 5: Classifier — 3-class output
        self.classifier = nn.Sequential(
            nn.LayerNorm(hidden_size),
            nn.Linear(hidden_size, 64),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(64, 32),
            nn.GELU(),
            nn.Dropout(dropout * 0.5),
            nn.Linear(32, num_classes),
        )

    def forward(self, x):
        # x: (batch, seq_len, input_size)
        gated = self.conv_gate(x)            # (batch, seq_len, hidden)
        gru_out, _ = self.gru(gated)         # (batch, seq_len, hidden)
        trans_out = self.transformer(gru_out)  # (batch, seq_len, hidden)
        context, attn_w = self.attention(trans_out)  # (batch, hidden)
        logits = self.classifier(context)    # (batch, num_classes)
        return logits                        # raw logits (use CrossEntropyLoss)

    def predict_proba(self, x):
        """Return softmax probabilities."""
        logits = self.forward(x)
        return torch.softmax(logits, dim=-1)


# ═══════════════════════════════════════════════════════════════════
# DEEP MODEL V4 MANAGER — Per-Symbol Training & Inference
# ═══════════════════════════════════════════════════════════════════

class DeepModelV4:
    """
    Manager for per-symbol Transformer-GRU models.

    Each symbol gets its own trained model:
        deep_v4_XAUUSDc.pth
        deep_v4_XAGUSDc.pth
        deep_v4_BTCUSDc.pth

    Usage:
        model = DeepModelV4("XAUUSDc")
        model.train_session(X_train, y_train)
        probs = model.predict(features_sequence)  # [p_buy, p_hold, p_sell]
    """

    def __init__(self, symbol: str, settings=None):
        self.symbol = symbol
        self.settings = settings
        self._enabled = TORCH_AVAILABLE
        self._model: Optional[TransformerGRU] = None
        self._device = "cpu"
        self._trained = False
        self._meta = {}

        if self._enabled:
            try:
                if torch.cuda.is_available():
                    self._device = "cuda"
                logger.debug("deep_v4_init", extra={
                    "symbol": symbol,
                    "device": self._device,
                })
            except Exception:
                pass

            self._init_model()
            self._load_model()

    def _init_model(self):
        """Initialize fresh model."""
        if not self._enabled:
            return
        self._model = TransformerGRU().to(self._device)
        param_count = sum(p.numel() for p in self._model.parameters())
        logger.debug("deep_v4_model_created", extra={
            "symbol": self.symbol,
            "params": param_count,
            "device": self._device,
        })

    # ─── Prediction ───────────────────────────────────────────────

    def predict(self, features_sequence: np.ndarray) -> dict:
        """
        Predict class probabilities from feature sequence.

        Args:
            features_sequence: shape (SEQUENCE_LENGTH, INPUT_SIZE_V4) or (1, SEQ, INP)

        Returns:
            dict: {"buy": float, "hold": float, "sell": float, "action": str, "confidence": float}
            Returns neutral if model not ready.
        """
        neutral = {
            "buy": 0.0, "hold": 1.0, "sell": 0.0,
            "action": "HOLD", "confidence": 0.0,
        }

        if not self._enabled or self._model is None or not self._trained:
            return neutral

        try:
            self._model.eval()
            with torch.no_grad():
                if features_sequence.ndim == 2:
                    features_sequence = features_sequence.reshape(
                        1, SEQUENCE_LENGTH, INPUT_SIZE_V4
                    )

                x = torch.FloatTensor(features_sequence).to(self._device)
                probs = self._model.predict_proba(x)[0].cpu().numpy()

            p_buy, p_hold, p_sell = float(probs[0]), float(probs[1]), float(probs[2])

            # Per-symbol confidence gap — look up or use default
            conf_gap = SYMBOL_CONFIDENCE_GAP.get(self.symbol, DEFAULT_CONFIDENCE_GAP)

            # Determine action with confidence margin filter
            # The winning class must:
            # 1. Exceed MIN_ACTION_PROB (0.40)
            # 2. P(HOLD) must be below MAX_HOLD_PROB (0.45)
            # 3. Winner must beat the runner-up by conf_gap
            # This filters out uncertain 50/50 predictions as HOLD
            
            if p_buy > p_sell and p_buy > MIN_ACTION_PROB and p_hold < MAX_HOLD_PROB:
                # Check confidence gap: BUY must beat SELL by margin
                if (p_buy - p_sell) >= conf_gap:
                    action = "BUY"
                    confidence = p_buy
                else:
                    action = "HOLD"
                    confidence = p_hold
            elif p_sell > p_buy and p_sell > MIN_ACTION_PROB and p_hold < MAX_HOLD_PROB:
                # Check confidence gap: SELL must beat BUY by margin
                if (p_sell - p_buy) >= conf_gap:
                    action = "SELL"
                    confidence = p_sell
                else:
                    action = "HOLD"
                    confidence = p_hold
            else:
                action = "HOLD"
                confidence = p_hold

            return {
                "buy": round(p_buy, 4),
                "hold": round(p_hold, 4),
                "sell": round(p_sell, 4),
                "action": action,
                "confidence": round(confidence, 4),
            }

        except Exception as e:
            logger.error("deep_v4_predict_error", extra={
                "symbol": self.symbol,
                "error": str(e),
            })
            return neutral

    # ─── Training ─────────────────────────────────────────────────

    def train_session(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        epochs: int = EPOCHS,
        verbose: bool = True,
    ) -> dict:
        """
        Train per-symbol model with walk-forward validation, early stopping,
        class weighting, and LR scheduling.

        Args:
            X_train: shape (N, SEQUENCE_LENGTH, INPUT_SIZE_V4)
            y_train: shape (N,) with values in {0=BUY, 1=HOLD, 2=SELL}
            epochs: training epochs
            verbose: print progress

        Returns:
            dict with training metrics
        """
        if not self._enabled:
            return {"status": "disabled", "reason": "torch not available"}

        if len(X_train) < MIN_TRAIN_SAMPLES:
            return {"status": "insufficient_data", "samples": len(X_train)}

        # ── Walk-forward split (time-ordered) ──
        split_idx = int(len(X_train) * (1 - VALIDATION_SPLIT))
        X_tr, X_val = X_train[:split_idx], X_train[split_idx:]
        y_tr, y_val = y_train[:split_idx], y_train[split_idx:]

        # ── Class weighting ──
        class_counts = np.bincount(y_tr.astype(int), minlength=NUM_CLASSES)
        total = len(y_tr)
        class_weights = []
        for c in range(NUM_CLASSES):
            if class_counts[c] > 0:
                w = total / (NUM_CLASSES * class_counts[c])
                # Cap HOLD weight at 2.0 to prevent HOLD over-prediction
                # (HOLD is only ~5-7% of data, raw weight would be 5-6x)
                if c == LABEL_HOLD:
                    w = min(w, 2.0)
                class_weights.append(w)
            else:
                class_weights.append(1.0)
        class_weights_tensor = torch.FloatTensor(class_weights).to(self._device)

        if verbose:
            print(f"\n{'='*60}")
            print(f"  Deep Model V4 Training — {self.symbol}")
            print(f"{'='*60}")
            print(f"  Train: {len(X_tr):,} | Val: {len(X_val):,}")
            print(f"  Class distribution (train):")
            for c in range(NUM_CLASSES):
                pct = class_counts[c] / total * 100 if total > 0 else 0
                print(f"    {LABEL_NAMES[c]}: {class_counts[c]:,} ({pct:.1f}%)")
            print(f"  Class weights: {[f'{w:.2f}' for w in class_weights]}")
            print(f"  Device: {self._device}")
            print(f"{'='*60}\n")

        # ── Reinitialize model ──
        self._init_model()

        # ── Create DataLoaders ──
        train_ds = TensorDataset(
            torch.FloatTensor(X_tr),
            torch.LongTensor(y_tr.astype(int)),
        )
        val_ds = TensorDataset(
            torch.FloatTensor(X_val),
            torch.LongTensor(y_val.astype(int)),
        )
        train_dl = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True, drop_last=True)
        val_dl = DataLoader(val_ds, batch_size=BATCH_SIZE, shuffle=False)

        if len(train_dl) == 0:
            return {"status": "insufficient_batches", "batch_size": BATCH_SIZE}

        # ── Optimizer + Scheduler ──
        optimizer = optim.AdamW(
            self._model.parameters(), lr=LEARNING_RATE, weight_decay=1e-4
        )
        scheduler = optim.lr_scheduler.OneCycleLR(
            optimizer,
            max_lr=LEARNING_RATE,
            epochs=epochs,
            steps_per_epoch=len(train_dl),
        )
        criterion = nn.CrossEntropyLoss(weight=class_weights_tensor)

        # ── Training Loop ──
        best_val_loss = float("inf")
        best_val_acc = 0.0
        patience_counter = 0
        best_state = None
        history = {"train_loss": [], "val_loss": [], "val_acc": []}

        t0 = time.time()

        for epoch in range(epochs):
            # ─ Train ─
            self._model.train()
            train_loss = 0.0
            train_correct = 0
            train_total = 0

            for batch_x, batch_y in train_dl:
                batch_x = batch_x.to(self._device)
                batch_y = batch_y.to(self._device)

                optimizer.zero_grad()
                logits = self._model(batch_x)
                loss = criterion(logits, batch_y)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self._model.parameters(), 1.0)
                optimizer.step()
                scheduler.step()

                train_loss += loss.item() * len(batch_y)
                preds = logits.argmax(dim=-1)
                train_correct += (preds == batch_y).sum().item()
                train_total += len(batch_y)

            avg_train_loss = train_loss / max(train_total, 1)
            train_acc = train_correct / max(train_total, 1)

            # ─ Validate ─
            self._model.eval()
            val_loss = 0.0
            val_correct = 0
            val_total = 0
            val_preds_all = []
            val_labels_all = []

            with torch.no_grad():
                for batch_x, batch_y in val_dl:
                    batch_x = batch_x.to(self._device)
                    batch_y = batch_y.to(self._device)

                    logits = self._model(batch_x)
                    loss = criterion(logits, batch_y)

                    val_loss += loss.item() * len(batch_y)
                    preds = logits.argmax(dim=-1)
                    val_correct += (preds == batch_y).sum().item()
                    val_total += len(batch_y)
                    val_preds_all.extend(preds.cpu().numpy())
                    val_labels_all.extend(batch_y.cpu().numpy())

            avg_val_loss = val_loss / max(val_total, 1)
            val_acc = val_correct / max(val_total, 1)

            history["train_loss"].append(avg_train_loss)
            history["val_loss"].append(avg_val_loss)
            history["val_acc"].append(val_acc)

            if verbose and (epoch % 3 == 0 or epoch == epochs - 1):
                lr = optimizer.param_groups[0]["lr"]
                print(
                    f"  Epoch {epoch+1:3d}/{epochs} | "
                    f"Train Loss: {avg_train_loss:.4f} Acc: {train_acc:.3f} | "
                    f"Val Loss: {avg_val_loss:.4f} Acc: {val_acc:.3f} | "
                    f"LR: {lr:.6f}"
                )

            # ─ Early Stopping ─
            if avg_val_loss < best_val_loss:
                best_val_loss = avg_val_loss
                best_val_acc = val_acc
                patience_counter = 0
                best_state = {
                    k: v.cpu().clone() for k, v in self._model.state_dict().items()
                }
            else:
                patience_counter += 1
                if patience_counter >= PATIENCE:
                    if verbose:
                        print(f"\n  Early stopping at epoch {epoch+1} (patience={PATIENCE})")
                    break

        elapsed = time.time() - t0

        # ── Restore best model ──
        if best_state is not None:
            self._model.load_state_dict(best_state)

        self._trained = True

        # ── Compute per-class metrics ──
        val_preds_arr = np.array(val_preds_all)
        val_labels_arr = np.array(val_labels_all)

        per_class = {}
        for c in range(NUM_CLASSES):
            tp = ((val_preds_arr == c) & (val_labels_arr == c)).sum()
            fp = ((val_preds_arr == c) & (val_labels_arr != c)).sum()
            fn = ((val_preds_arr != c) & (val_labels_arr == c)).sum()
            precision = tp / max(tp + fp, 1)
            recall = tp / max(tp + fn, 1)
            f1 = 2 * precision * recall / max(precision + recall, 1e-8)
            per_class[LABEL_NAMES[c]] = {
                "precision": round(float(precision), 4),
                "recall": round(float(recall), 4),
                "f1": round(float(f1), 4),
            }

        self._meta = {
            "version": "v4_transformer_gru",
            "symbol": self.symbol,
            "input_size": INPUT_SIZE_V4,
            "hidden_size": HIDDEN_SIZE,
            "num_classes": NUM_CLASSES,
            "best_val_loss": round(best_val_loss, 4),
            "best_val_acc": round(best_val_acc, 4),
            "per_class_metrics": per_class,
            "train_samples": len(X_tr),
            "val_samples": len(X_val),
            "class_distribution": {LABEL_NAMES[c]: int(class_counts[c]) for c in range(NUM_CLASSES)},
            "epochs_run": len(history["train_loss"]),
            "training_time_sec": round(elapsed, 1),
            "trained_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }

        # ── Save ──
        self._save_model()

        if verbose:
            print(f"\n{'='*60}")
            print(f"  Training Complete — {self.symbol}")
            print(f"{'='*60}")
            print(f"  Best Val Loss: {best_val_loss:.4f}")
            print(f"  Best Val Acc:  {best_val_acc:.3f}")
            for cls_name, metrics in per_class.items():
                print(f"  {cls_name:5s}: P={metrics['precision']:.3f}  R={metrics['recall']:.3f}  F1={metrics['f1']:.3f}")
            print(f"  Time:  {elapsed:.1f}s")
            print(f"{'='*60}\n")

        return {
            "status": "trained",
            **self._meta,
        }

    # ─── Save / Load ──────────────────────────────────────────────

    def _model_path(self) -> str:
        return os.path.join(MODEL_DIR, f"{MODEL_PREFIX}_{self.symbol}.pth")

    def _meta_path(self) -> str:
        return os.path.join(MODEL_DIR, f"{MODEL_PREFIX}_{self.symbol}_meta.json")

    def _save_model(self):
        """Save model weights and metadata."""
        if not self._enabled or self._model is None:
            return

        os.makedirs(MODEL_DIR, exist_ok=True)
        model_path = self._model_path()
        meta_path = self._meta_path()

        torch.save(self._model.state_dict(), model_path)
        with open(meta_path, "w") as f:
            json.dump(self._meta, f, indent=2)

        logger.info("deep_v4_model_saved", extra={
            "symbol": self.symbol,
            "path": model_path,
        })

    def _load_model(self):
        """Load saved model if exists."""
        if not self._enabled:
            return

        model_path = self._model_path()
        meta_path = self._meta_path()

        if not os.path.exists(model_path):
            logger.info("deep_v4_model_not_found", extra={
                "symbol": self.symbol,
                "path": model_path,
            })
            return

        try:
            state = torch.load(model_path, map_location=self._device, weights_only=True)
            self._model.load_state_dict(state)
            self._trained = True

            if os.path.exists(meta_path):
                with open(meta_path) as f:
                    self._meta = json.load(f)

            logger.debug("deep_v4_model_loaded", extra={
                "symbol": self.symbol,
                "meta": self._meta,
            })
        except Exception as e:
            logger.error("deep_v4_model_load_error", extra={
                "symbol": self.symbol,
                "error": str(e),
            })
            self._init_model()

    def get_status(self) -> dict:
        """Model status for dashboard."""
        return {
            "enabled": self._enabled,
            "trained": self._trained,
            "symbol": self.symbol,
            "device": self._device,
            "model": f"{MODEL_PREFIX}_{self.symbol}",
            "meta": self._meta,
        }
