"""
Omniscient Oracle Strategy — Institutional SMC (Trinity Edition).

Uses ChartIntelligence for shared SMC analysis.
Supports BTCUSD, XAUUSD, XAGUSD, EURUSD, GBPUSD using Smart Money Concepts.

Tunable Parameters (for AutoTune):
    - divine_score_threshold: Minimum divine score to trigger entry (default: 45)
    - atr_sl_mult: ATR multiplier for SL buffer (default: 0.3)
    - rr_target: Risk:Reward ratio for TP (default: 5.0)
    - MIN_ADX: Minimum ADX for trending classification (default: 25)
    - absorption_vol_mult: Multiplier for adaptive absorption volume (default: 3.0)
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Optional
from app.analysis.chart_intelligence import ChartIntelligence, SMCAnalysis, _resolve_tuning
from .base_strategy import BaseStrategy, StrategyDecision
from .ghost_oracle_tuning import get_tuning, is_session_allowed, get_risk_pct
from app.core.logging import get_logger

try:
    import app.analysis.indicators as ind
except ImportError:
    ta = None

logger = get_logger(__name__)


class OmniscientOracleStrategy(BaseStrategy):
    """
    Omniscient Oracle Strategy — Institutional Grade (Trinity Edition).

    "THE DIVINE TRINITY" — Mastery over BTC, Gold, Silver, and FX flow.
    Now powered by ChartIntelligence for consistent, shared SMC analysis.
    """

    def __init__(self, symbol: str = "XAUUSD", **kwargs):
        self.name = "OMNISCIENT ORACLE (DIVINE)"
        self.symbol = symbol
        # Load per-symbol tuning, merge with kwargs overrides
        tuning = get_tuning(symbol, overrides=kwargs)
        self.params = {
            "divine_score_threshold": kwargs.get("divine_score_threshold", 40),  # ↓ was 60 (allow more signals)
            "atr_sl_mult": kwargs.get("atr_sl_mult", tuning.get("atr_sl_mult", 2.0)),  # ↑ was 0.3 (IMMEDIATE_SL fix)
            "rr_target": kwargs.get("rr_target", 1.5),  # ↓ was 2.0 (wider SL needs lower RR for WR)
            "MIN_ADX": kwargs.get("MIN_ADX", 20),  # ↓ was 25 (allow less strong trends)
            "absorption_vol_mult": kwargs.get("absorption_vol_mult", 3.0),
        }
        self.min_confidence = kwargs.get("min_confidence", 0.65)  # ↓ was 0.85 (allow more signals)
        self._tuning = tuning  # Keep for session checks
        self._ci = ChartIntelligence(params=self.params)

    def _auto_tune_absorption(self, df: pd.DataFrame, symbol: str) -> None:
        """
        Adaptive absorption volume tuning based on recent market activity.
        """
        if len(df) < 50:
            return

        vol_col = (
            "tick_volume" if "tick_volume" in df.columns
            else ("volume" if "volume" in df.columns else None)
        )
        if not vol_col:
            return

        avg_vol = df[vol_col].rolling(20).mean().iloc[-1]
        if pd.isna(avg_vol) or avg_vol <= 0:
            return

        mult = self.params.get("absorption_vol_mult", 3.0)
        adaptive = avg_vol * mult

        # Clamp to safe range per asset
        sym = symbol.upper()
        if "XAU" in sym:
            adaptive = max(300, min(adaptive, 5000))
        elif "BTC" in sym:
            adaptive = max(100, min(adaptive, 3000))
        elif "XAG" in sym:
            adaptive = max(400, min(adaptive, 6000))
        else:
            adaptive = max(100, min(adaptive, 2000))

        # Update ChartIntelligence params
        self._ci.params["adaptive_absorption"] = adaptive

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs
    ) -> Decision:
        symbol = profile.symbol
        current_session = kwargs.get("current_session", "")

        if candles is None or len(candles) < 60:
            return Decision(
                symbol=symbol,
                action=Action.HOLD,
                reason="Insufficient data for Oracle analysis",
            )

        # ── Session filter (ASIA_LOSS fix: block losing sessions) ──
        if current_session and not is_session_allowed(symbol, current_session):
            return StrategyDecision(
                signal="NO_TRADE",
                reason=f"Session {current_session} not allowed for {symbol}",
            )

        # Auto-tune absorption thresholds
        self._auto_tune_absorption(candles, symbol)

        # ── ChartIntelligence Analysis ──
        smc: SMCAnalysis = self._ci.full_analysis(candles, symbol=symbol)

        idx = len(candles) - 1
        current_price = float(candles.iloc[idx]["close"])

        # ── HTF Trend Detection ──
        d1_trend = kwargs.get("d1_trend", "UNKNOWN")
        if d1_trend == "UNKNOWN":
            ema200 = candles["close"].ewm(span=200).mean()
            d1_trend = "UP" if current_price > ema200.iloc[idx] else "DOWN"

        # ── Divine Score Calculation ──
        divine_threshold = self.params.get("divine_score_threshold", 45)

        # Check trend alignment before scoring
        flow_ok = (
            (d1_trend == "UP" and smc.delta_flow > -100) or
            (d1_trend == "DOWN" and smc.delta_flow < 100)
        )

        if not flow_ok:
            return Decision(
                symbol=symbol,
                action=Action.HOLD,
                reason=f"Flow misaligned with HTF trend ({d1_trend})",
                extra={
                    "regime": smc.regime,
                    "bias": d1_trend,
                    "flow": round(smc.delta_flow, 1),
                },
            )

        bias = d1_trend
        divine_score, reasons = smc.divine_score(bias)

        # Check sweep confirmation (stricter: close must break prev candle)
        prev = candles.iloc[idx - 1]
        if bias == "UP" and smc.bull_sweep:
            if candles.iloc[idx]["close"] <= prev["high"]:
                divine_score -= 20  # Penalize weak sweep
        elif bias == "DOWN" and smc.bear_sweep:
            if candles.iloc[idx]["close"] >= prev["low"]:
                divine_score -= 20

        # ── Signal Decision ──
        signal = Action.HOLD
        confidence = 0.0

        if divine_score >= divine_threshold:
            signal = Action.BUY if bias == "UP" else Action.SELL
            confidence = min(divine_score / 100.0, 1.0)

        if signal != Action.HOLD:
            # SL/TP Calculation
            sl_mult = self.params.get("atr_sl_mult", 0.3)
            rr_target = self.params.get("rr_target", 5.0)
            atr = smc.atr_value if smc.atr_value > 0 else current_price * 0.001

            if signal == Action.BUY:
                sl = min(smc.internal_low, candles.iloc[-5:]["low"].min()) - (atr * sl_mult)
                tp = current_price + (abs(current_price - sl) * rr_target)
            else:
                sl = max(smc.internal_high, candles.iloc[-5:]["high"].max()) + (atr * sl_mult)
                tp = current_price - (abs(current_price - sl) * rr_target)

            return Decision(
                symbol=symbol,
                action=signal,
                confidence=confidence,
                reason=" | ".join(reasons),
                stop_loss=sl,
                take_profit=tp,
                risk_pct=get_risk_pct(symbol),
                extra={
                    "regime": smc.regime,
                    "divine_score": divine_score,
                    "liquidity": "Swept" if (smc.bull_sweep or smc.bear_sweep) else "Building",
                    "order_flow": "Imbalanced" if abs(smc.delta_flow) > 500 else "Neutral",
                    "absorption": "High" if (smc.bull_absorption or smc.bear_absorption) else "Low",
                },
            )

        # Action.HOLD — waiting for setup
        return Decision(
            symbol=symbol,
            action=Action.HOLD,
            reason="Waiting for Institutional Inefficiency",
            extra={
                "regime": smc.regime,
                "divine_score": divine_score,
                "liquidity": "Swept" if (smc.bull_sweep or smc.bear_sweep) else "Building",
                "bias": d1_trend,
                "suggested_pending": {
                    "int_buy": smc.internal_low,
                    "int_sell": smc.internal_high,
                    "ext_buy": smc.external_low,
                    "ext_sell": smc.external_high,
                    "sl_dist_atr": sl_mult if 'sl_mult' in locals() else 0.3, # changed dir() to locals() or just 0.3
                    "tp_rr": rr_target if 'rr_target' in locals() else 5.0,
                }
            },
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "accuracy_tier": "Divine",
            "params": self.params,
            "assets": ["BTCUSD", "XAUUSD", "XAGUSD", "EURUSD", "GBPUSD"],
            "features": ["SMC", "Order Flow", "Absorption", "ChartIntelligence"],
        }

    def update_parameters(self, params: Dict[str, Any]):
        """Update tunable parameters (used by AutoTune)."""
        self.params.update(params)
        self._ci = ChartIntelligence(params=self.params)


# Shared instances
btc_ultimate_strategy = OmniscientOracleStrategy()
oracle_strategy = btc_ultimate_strategy
