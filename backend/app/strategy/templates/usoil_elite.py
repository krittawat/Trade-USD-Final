"""
USOIL Elite Strategy V2 — Ported to Factory Pattern.
Designed for the USOILm (Standard) symbol with ~18-30 pts spread.

PHILOSOPHY:
    Crude Oil is a high-momentum asset but highly sensitive to US Session hours.
    This strategy combines:
    1. Trend Filter (EMA 200)
    2. Momentum Alignment (EMA 9/21)
    3. Mean Reversion Safety (RSI extremes)
    4. Market Structure & Force
"""

import numpy as np
import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)

class USOilEliteStrategy(BaseStrategy):
    """
    USOIL Elite Strategy — Factory Compatible.
    High momentum scalping/day-trading specifically tuned for Crude Oil.
    """

    name = "usoil_elite"
    timeframe = "M5"
    asset_class = "*"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.STRONG_TREND,
        RegimeType.WEAK_TREND,
        RegimeType.HIGH_VOLATILITY,
    ]

    p = {
        "ema_fast": 9,
        "ema_mid": 21,
        "ema_slow": 50,
        "ema_trend": 200,

        "rsi_period": 14,
        "rsi_buy_min": 42,  # Slightly tighter for smarter filter
        "rsi_buy_max": 75,
        "rsi_sell_min": 25,
        "rsi_sell_max": 58,

        "min_rr": 1.7,      # Increased from 1.5 for better expectancy
        "max_sl_pts": 450,
        "sl_atr_mult": 1.6, # Tighter SL for Oil momentum
        
        # Session Specific Risk Multipliers
        "session_risk": {
            "LONDON": 1.0,
            "NY": 1.2,      # Higher volatility in NY, allow more room
            "OVERLAP": 1.3,
        }
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
        if len(candles) < 210:
            return self.create_hold(symbol, "Insufficient data")

        # ── Compute Indicators ──
        close = candles["close"]
        high = candles["high"]
        low = candles["low"]
        volume = candles["tick_volume"]

        ema_9 = close.ewm(span=self.p["ema_fast"], adjust=False).mean()
        ema_21 = close.ewm(span=self.p["ema_mid"], adjust=False).mean()
        ema_200 = close.ewm(span=self.p["ema_trend"], adjust=False).mean()
        
        rsi = self._calc_rsi(close, self.p["rsi_period"])
        atr = self._calc_atr(candles, 14)
        vol_ma = volume.rolling(window=20).mean()

        i = len(candles) - 1
        c = close.iloc[i]
        e9 = ema_9.iloc[i]
        e21 = ema_21.iloc[i]
        e200 = ema_200.iloc[i]
        r = rsi.iloc[i]
        a = atr.iloc[i]
        v = volume.iloc[i]
        vma = vol_ma.iloc[i]
        
        # Get Session (assuming passed in kwargs or detected)
        session = kwargs.get('session', 'NY') # Default to NY for Oil if unknown

        if pd.isna(a) or a <= 0:
            return self.create_hold(symbol, "ATR not ready")

        b_score, s_score = 0, 0
        b_reasons, s_reasons = [], []

        # ── Layer 1: Enhanced Trend ──
        # Bullish Stack
        if c > e200 and e21 > e200 and e9 > e21:
            b_score += 40
            b_reasons.append("Trend: Perfect Bullish Stack")
        elif c > e200:
            b_score += 20
            b_reasons.append("Trend: Bearish but above EMA-200")

        # Bearish Stack
        if c < e200 and e21 < e200 and e9 < e21:
            s_score += 40
            s_reasons.append("Trend: Perfect Bearish Stack")
        elif c < e200:
            s_score += 20
            s_reasons.append("Trend: Bullish but below EMA-200")

        # ── Layer 2: Momentum & Volume Convergence ──
        vol_spike = v > vma * 1.2
        
        if self.p["rsi_buy_min"] < r < self.p["rsi_buy_max"]:
            if r > 50: 
                b_score += 15
                if vol_spike: 
                    b_score += 15
                    b_reasons.append("Mom: RSI-Volume Convergence (Bullish)")
                else:
                    b_reasons.append(f"Mom: RSI {r:.1f} Bullish")
            
        if self.p["rsi_sell_min"] < r < self.p["rsi_sell_max"]:
            if r < 50: 
                s_score += 15
                if vol_spike: 
                    s_score += 15
                    s_reasons.append("Mom: RSI-Volume Convergence (Bearish)")
                else:
                    s_reasons.append(f"Mom: RSI {r:.1f} Bearish")
            
        # ── Layer 3: Price Action Pressure ──
        if pressure:
            score = pressure.get("score", 0)
            if score > 60:
                b_score += 30
                b_reasons.append("Force: Strong Buying Pressure")
            elif score < -60:
                s_score += 30
                s_reasons.append("Force: Strong Selling Pressure")
        else:
            # Fallback: Candlestick Body Logic
            body_size = abs(c - candles["open"].iloc[i])
            if body_size > a * 0.5:
                if c > candles["open"].iloc[i]:
                    b_score += 20
                    b_reasons.append("PA: Large Bullish Body")
                else:
                    s_score += 20
                    s_reasons.append("PA: Large Bearish Body")

        side = Action.HOLD
        final_score = 0
        reasons = []

        # Required minimum score increased to 75 for "Smarter" filter
        threshold = 75
        if b_score >= threshold and b_score > s_score:
            side = Action.BUY
            final_score = b_score
            reasons = b_reasons
        elif s_score >= threshold and s_score > b_score:
            side = Action.SELL
            final_score = s_score
            reasons = s_reasons

        if side == Action.HOLD:
            return self.create_hold(symbol, f"No setup (B:{b_score} S:{s_score})")

        # ── Smart Risk Management ──
        risk_mult = self.p["session_risk"].get(session, 1.0)
        sl_dist = a * self.p["sl_atr_mult"] * risk_mult
        
        # SL Point Cap Check
        point_size = float(profile.point)
        sl_points = sl_dist / point_size if point_size else sl_dist * 1000
        if sl_points > self.p["max_sl_pts"]:
            sl_dist = self.p["max_sl_pts"] * point_size

        if side == Action.BUY:
            sl = c - sl_dist
            tp = c + (sl_dist * self.p["min_rr"])
        else:
            sl = c + sl_dist
            tp = c - (sl_dist * self.p["min_rr"])

        confidence = min(0.98, final_score / 100.0)

        return Decision(
            symbol=symbol,
            action=side,
            confidence=confidence,
            reason="; ".join(reasons),
            stop_loss=round(sl, profile.digits),
            take_profit=round(tp, profile.digits),
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["usoil_elite", "smart_v2", session]
        )

    # ── Indicator Helpers ──
    @staticmethod
    def _calc_rsi(s: pd.Series, period: int = 14) -> pd.Series:
        d = s.diff()
        g = d.clip(lower=0).ewm(span=period, adjust=False).mean()
        l = (-d).clip(lower=0).ewm(span=period, adjust=False).mean()
        return 100 - (100 / (1 + g / l.replace(0, np.nan)))

    @staticmethod
    def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()
