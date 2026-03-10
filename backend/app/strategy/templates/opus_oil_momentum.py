"""
OPUS Oil Momentum — Aggressive Crude Hunter (Institutional Grade).
==============================================================
Designed for USOILm (Standard) | Target: 2,000 THB/day potential.
Combines legacy Momentum logic with the OPUS Ghost Protocol Regime Engine.

PHILOSOPHY:
    Crude Oil moves in sharp, sustained impulses. OPUS detects the 'Expansion' 
    phase and entries are synchronized with Smart Money Pullbacks (BOS/FVG).
"""

import numpy as np
import pandas as pd
from typing import Optional

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy
from app.brain.opus_regime import classify_opus_regime

logger = get_logger(__name__)

class OpusOilMomentumStrategy(BaseStrategy):
    """
    OPUS Oil Momentum — The ultimate USOIL weaponry.
    """

    name = "opus_oil_momen"
    timeframe = "M5"
    asset_class = "oil"  # Specifically for Oil
    suitable_regimes = [
        RegimeType.STRONG_TREND,
        RegimeType.VOL_EXPANSION,
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.BREAKOUT,
    ]

    p = {
        "ema_fast": 9,
        "ema_mid": 21,
        "ema_trend": 200,
        "min_roc": 0.15,      # High threshold for true momentum
        "adx_gate": 25,
        "min_opus_conf": 0.68,
        "sl_atr_mult": 1.5,
        "tp_rr": 1.9,         # Institutional Risk/Reward
        "max_sl_pts": 450,    # 45 pips cap for Oil
    }

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        pressure: dict | None = None,
        **kwargs,
    ) -> Decision:
        symbol = profile.symbol
        if len(candles) < 200:
            return self.create_hold(symbol, "Insufficient history for OPUS")

        # ─── Step 1: OPUS Regime Classification ───
        session = kwargs.get('session', 'NY')
        opus = classify_opus_regime(candles, session=session)
        
        # Check OPUS Gate
        if opus.confidence < self.p["min_opus_conf"]:
            return self.create_hold(symbol, f"OPUS Gate: low confidence ({opus.confidence:.2f})")

        # ─── Step 2: Momentum Indicators ───
        close = candles["close"]
        high = candles["high"]
        low = candles["low"]
        
        ema_9 = close.ewm(span=self.p["ema_fast"], adjust=False).mean()
        ema_21 = close.ewm(span=self.p["ema_mid"], adjust=False).mean()
        ema_200 = close.ewm(span=self.p["ema_trend"], adjust=False).mean()
        
        roc_5 = ((close.iloc[-1] / close.iloc[-5]) - 1) * 100
        atr = self._calc_atr(candles, 14)
        
        c = close.iloc[-1]
        e9, e21, e200 = ema_9.iloc[-1], ema_21.iloc[-1], ema_200.iloc[-1]
        a = atr.iloc[-1]
        
        # ─── Step 3: Confluence Scoring ───
        score = 0
        reasons = []
        
        # Base Trend Alignment
        if c > e200 and e9 > e21:
            score += 3
            reasons.append("Trend: Bulls dominant")
        elif c < e200 and e9 < e21:
            score += 3
            reasons.append("Trend: Bears dominant")
            
        # Opus Structure Synergy
        if opus.structure == "BULLISH" and c > e200:
            score += 4; reasons.append("OPUS: Bullish Structure")
        elif opus.structure == "BEARISH" and c < e200:
            score += 4; reasons.append("OPUS: Bearish Structure")
            
        # Momentum Velocity
        if abs(roc_5) > self.p["min_roc"]:
            score += 3; reasons.append(f"Velocity: {roc_5:.2f}% (High)")
            
        # Volume / Pressure Synergy
        if pressure and abs(pressure.get("score", 0)) > 50:
            # Match pressure sign with trend
            p_score = pressure.get("score", 0)
            if (p_score > 0 and c > e200) or (p_score < 0 and c < e200):
                score += 3; reasons.append("Force: Pressure Alignment")

        # ─── Step 4: Entry Logic ───
        side = Action.HOLD
        if score >= 10:
            if c > e200 and roc_5 > 0: side = Action.BUY
            if c < e200 and roc_5 < 0: side = Action.SELL
            
        if side == Action.HOLD:
            return self.create_hold(symbol, f"Scoring low ({score}/10)")

        # Institutional Execution logic (Limit at EMA9 or Market if ROC extreme)
        is_aggressive = abs(roc_5) > 0.25 or opus.aggressive_mode
        entry_price = e9 if not is_aggressive else c
        
        sl_dist = a * self.p["sl_atr_mult"]
        point_size = float(profile.point)
        if (sl_dist / point_size) > self.p["max_sl_pts"]:
            sl_dist = self.p["max_sl_pts"] * point_size
            
        if side == Action.BUY:
            sl = entry_price - sl_dist
            tp = entry_price + (sl_dist * self.p["tp_rr"])
        else:
            sl = entry_price + sl_dist
            tp = entry_price - (sl_dist * self.p["tp_rr"])

        return Decision(
            symbol=symbol,
            action=side,
            confidence=min(0.98, 0.7 + (score/40.0)),
            reason="; ".join(reasons),
            stop_loss=round(sl, profile.digits),
            take_profit=round(tp, profile.digits),
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["OPUS", "OIL", "MOMENTUM", "SMC"]
        )

    @staticmethod
    def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()
