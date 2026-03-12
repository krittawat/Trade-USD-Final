"""
Regime Adaptive Strategy — All-Weather Dispatcher (Production Grade)
====================================================================
กลยุทธ์ "ฉลาด" ที่เทรดได้กำไรทุกสภาวะตลาด:

Architecture:
    1. รับ regime จาก classify_regime() (TRENDING/RANGING/HIGH_VOL/FAKEOUT/...)
    2. Dispatch ไปยัง sub-module ที่เหมาะสม:
       - _trend_follower()    → TRENDING_UP / TRENDING_DOWN
       - _range_trader()      → RANGING / LOW_VOLATILITY  
       - _volatility_surfer() → HIGH_VOLATILITY
       - _fakeout_hunter()    → FAKEOUT / LIQUIDITY_SWEEP
    3. คืน Decision object เดียว (BUY/SELL/HOLD) พร้อม SL/TP

Key Design:
    - ทุก sub-module ใช้ ATR-based SL/TP → ปรับตัวตาม volatility
    - Confidence scoring 0-100 → เข้าเทรดเมื่อ ≥ 65 เท่านั้น
    - Internal regime re-check → ป้องกัน stale regime data
    - ห้ามเทรดใน NEWS_SPIKE / UNKNOWN
"""

import numpy as np
import pandas as pd
import pandas_ta as ta
import app.analysis.indicators as ind
from typing import Optional, Dict, Tuple

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy
from app.risk.anti_hunt_sl import apply_anti_hunt_sl
from datetime import datetime, timezone

logger = get_logger(__name__)


# ====================================================================
# Configuration — ปรับแต่งค่าตรงนี้ (หรือ override จาก DB)
# ====================================================================

CONFIG = {
    # --- Trend Follower ---
    "trend_ema_fast": 9,
    "trend_ema_slow": 21,
    "trend_ema_filter": 50,       # higher TF filter
    "trend_adx_min": 28,          # raised for high-WR (was 25)
    "trend_rsi_buy_max": 65,      # tighter: avoid overbought (was 68)
    "trend_rsi_sell_min": 35,     # tighter: avoid oversold (was 32)
    "trend_sl_atr_mult": 1.8,     # tighter SL for better RR (was 2.0)
    "trend_tp_atr_mult": 2.5,     # closer TP for higher hit rate (was 3.0)
    "trend_min_rr": 1.2,          # slightly lower RR ok with high WR (was 1.3)
    "trend_pullback_tolerance": 0.8,  # tighter pullback zone (was 1.0)
    "trend_max_chase": 1.5,       # tighter chase limit (was 1.8)
    "trend_min_confidence": 75,   # HIGH threshold for 60%+ WR (was 60)
    
    # --- Range Trader ---
    "range_bb_length": 20,
    "range_bb_std": 2.0,
    "range_rsi_os": 35,           # stricter oversold for higher WR (was 38)
    "range_rsi_ob": 65,           # stricter overbought (was 62)
    "range_adx_max": 22,          # tighter: must be clearly ranging (was 25)
    "range_sl_atr_mult": 1.0,     # tighter SL (was 1.2)
    "range_min_rr": 0.8,          # higher min RR (was 0.6)
    "range_keltner_length": 20,
    "range_keltner_mult": 1.5,
    "range_bb_buy_zone": 0.08,    # NEW: must be very close to lower band (was 0.15)
    "range_bb_sell_zone": 0.92,   # NEW: must be very close to upper band (was 0.85)
    "range_min_confidence": 70,   # NEW: range-specific confidence gate
    
    # --- Volatility Surfer ---
    "vol_breakout_lookback": 20,
    "vol_volume_spike": 1.8,      # higher volume req (was 1.5)
    "vol_sl_atr_mult": 2.0,       # tighter SL (was 2.5)
    "vol_tp_atr_mult": 3.0,       # closer TP (was 4.0)
    "vol_min_rr": 1.2,            # lower RR ok with high WR (was 1.5)
    "vol_rsi_min_buy": 55,        # must be clearly bullish (was 50)
    "vol_rsi_max_sell": 45,       # must be clearly bearish (was 50)
    "vol_min_confidence": 70,     # NEW: vol-specific gate
    
    # --- Fakeout Hunter ---
    "fake_wick_lookback": 20,     # candles to check high/low
    "fake_return_bars": 3,        # price must return within N bars
    "fake_sl_atr_mult": 0.8,      # tighter SL (was 1.0)
    "fake_tp_atr_mult": 1.5,      # closer TP (was 2.0)
    "fake_min_rr": 0.5,           # slightly higher (was 0.4)
    "fake_wick_ratio_min": 0.55,  # stricter wick ratio (was 0.50)
    "fake_min_confidence": 65,    # NEW: fakeout-specific gate
    
    # --- Global ---
    "min_confidence": 70,         # RAISED from 55 for 60%+ WR target
    "atr_period": 14,
    "rsi_period": 14,
    "min_candles": 60,            # minimum data requirement
    "sma200_filter_enabled": True,  # NEW: SMA200 long-term trend gate
    
    # --- Session Filter ---
    "session_filter_enabled": True,  # enable session filter for Forex
    "session_london_start": 7,       # UTC hour
    "session_ny_end": 21,            # UTC hour
}


# ====================================================================
# Per-Symbol Overrides — สำหรับ asset ที่ต้องการค่าเฉพาะ
# ====================================================================

SYMBOL_OVERRIDES = {
    # --- XAGUSDc: Silver (extreme volatility) ---
    # Silver has 2-3x the ATR/price ratio of gold
    # Needs much wider SL/TP to avoid stopped out by noise
    "XAGUSDc": {
        "trend_sl_atr_mult": 2.5,       # wider for silver but tighter than V3 (was 3.0)
        "trend_tp_atr_mult": 3.5,       # closer TP for hit rate (was 5.0)
        "trend_min_rr": 1.0,
        "vol_sl_atr_mult": 3.0,         # wider for volatility surfer
        "vol_tp_atr_mult": 4.0,         # closer TP (was 5.5)
        "range_sl_atr_mult": 1.5,       # wider for range trader (was 1.8)
        "fake_sl_atr_mult": 1.2,        # wider for fakeout
        "fake_tp_atr_mult": 2.0,        # closer TP (was 3.0)
        # SMA200 stays ON (default True) — 57.1% WR with it
    },
    
    # --- XAUUSDc: Gold (strong trend follower) ---
    "XAUUSDc": {
        "trend_adx_min": 25,            # slightly relaxed for gold (was 28 global)
        "trend_min_confidence": 70,     # still high but relaxed vs 75
        "trend_pullback_tolerance": 1.0, # gold pullback zone
        "trend_max_chase": 2.0,         # gold trends extend further
        "trend_rsi_buy_max": 68,
        "trend_rsi_sell_min": 32,
        # SMA200 stays ON (default True) — 53.3% WR with it
    },
    
    # --- USDJPYc: SMA200 too restrictive (5 trades only) ---
    "USDJPYc": {
        "sma200_filter_enabled": False,  # V4+ showed only 5 trades with SMA200
    },
    
    # --- EURUSDc: SMA200 too restrictive (3 trades only) ---
    "EURUSDc": {
        "sma200_filter_enabled": False,  # V4+ showed only 3 trades with SMA200
    },
    
    # --- GBPUSDc: SMA200 too restrictive (2 trades only) ---
    "GBPUSDc": {
        "sma200_filter_enabled": False,  # V4+ showed only 2 trades with SMA200
    },
    
    # --- BTCUSDc: Crypto (high volatility, 24/7) ---
    # BTC session filter disabled (trades 24/7)
    "BTCUSDc": {
        "session_filter_enabled": False,  # BTC trades all sessions
        "sma200_filter_enabled": False,   # SMA200 too strict for crypto
        "vol_sl_atr_mult": 3.0,          # wider for crypto
        "vol_tp_atr_mult": 5.0,
    },
}


class RegimeAdaptiveStrategy(BaseStrategy):
    """
    All-Weather Strategy — เทรดได้ทุกสภาวะตลาด.
    
    Dispatch Logic:
        TRENDING_UP/DOWN  → _trend_follower()   (EMA cross + pullback)
        RANGING           → _range_trader()      (BB mean reversion)  
        HIGH_VOLATILITY   → _volatility_surfer() (breakout confirmation)
        FAKEOUT/SWEEP     → _fakeout_hunter()    (trap reversal)
        Others            → HOLD (patience mode)
    """
    
    name = "regime_adaptive"
    timeframe = "M5"
    suitable_regimes = [
        RegimeType.TRENDING_UP,
        RegimeType.TRENDING_DOWN,
        RegimeType.RANGING,
        RegimeType.HIGH_VOLATILITY,
        RegimeType.LOW_VOLATILITY,
        RegimeType.FAKEOUT,
        RegimeType.LIQUIDITY_SWEEP,
    ]
    
    def __init__(self, **kwargs):
        self.params = {**CONFIG}
        # Override with any kwargs
        for k, v in kwargs.items():
            if k in self.params:
                self.params[k] = v
        # Per-symbol overrides are applied dynamically in analyze()

    def _get_params_for_symbol(self, symbol: str) -> dict:
        """Get config with per-symbol overrides applied."""
        params = {**self.params}
        # Find matching override
        for sym_key, overrides in SYMBOL_OVERRIDES.items():
            if sym_key in symbol:  # Match XAGUSDc, XAUUSDc, BTCUSDc etc.
                params.update(overrides)
                break
        return params

    # ================================================================
    # Main Analysis — Dispatcher
    # ================================================================
    
    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        **kwargs,
    ) -> Decision:
        """Dispatch to the appropriate sub-module based on regime."""
        
        symbol = profile.symbol
        
        # --- Apply per-symbol config overrides ---
        p = self._get_params_for_symbol(symbol)
        
        # --- Session Filter (Forex only: London + NY) ---
        if p.get("session_filter_enabled", False):
            is_forex = any(fx in symbol.upper() for fx in ["EUR", "GBP", "USD", "JPY", "CHF", "AUD", "NZD", "CAD"])
            is_commodity = any(m in symbol.upper() for m in ["XAU", "XAG", "BTC", "ETH"])
            
            if is_forex and not is_commodity:
                now_utc = datetime.now(timezone.utc)
                hour_utc = now_utc.hour
                
                # For backtesting: use candle timestamp if available
                if hasattr(candles, 'index') and len(candles) > 0:
                    last_ts = candles.index[-1] if hasattr(candles.index, 'hour') else None
                    if last_ts is None and 'time' in candles.columns:
                        last_ts = candles['time'].iloc[-1]
                    if last_ts is not None and hasattr(last_ts, 'hour'):
                        hour_utc = last_ts.hour
                
                # Only trade London (07:00) through NY close (21:00) UTC
                if hour_utc < p.get("session_london_start", 7) or hour_utc >= p.get("session_ny_end", 21):
                    return self.create_hold(
                        symbol,
                        f"Session Filter: {hour_utc}:00 UTC outside London/NY window (07:00-21:00 UTC)"
                    )
        
        # --- Data Validation ---
        if candles is None or len(candles) < p["min_candles"]:
            return self.create_hold(symbol, "Insufficient data for analysis")
        
        # --- Pre-calculate shared indicators ---
        indicators = self._compute_indicators(candles, profile, p)
        if indicators is None:
            return self.create_hold(symbol, "Indicator calculation failed")
        
        # --- Internal regime verification ---
        # Use our own ADX/ATR to double-check the passed regime
        effective_regime = self._verify_regime(regime, indicators)
        
        # --- Patience Mode (No-Trade Regimes) ---
        if effective_regime in (RegimeType.NEWS_SPIKE, RegimeType.UNKNOWN):
            regime_val = getattr(effective_regime, "value", str(effective_regime))
            return self.create_hold(
                symbol, 
                f"Patience Mode: {regime_val} — waiting for clear setup"
            )
        
        # --- Dispatch to sub-module ---
        decision = None
        
        if effective_regime in (RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN):
            decision = self._trend_follower(candles, profile, indicators, effective_regime, p)
            
        elif effective_regime in (RegimeType.RANGING, RegimeType.LOW_VOLATILITY):
            decision = self._range_trader(candles, profile, indicators, p)
            
        elif effective_regime == RegimeType.HIGH_VOLATILITY:
            decision = self._volatility_surfer(candles, profile, indicators, p)
            
        elif effective_regime in (RegimeType.FAKEOUT, RegimeType.LIQUIDITY_SWEEP):
            decision = self._fakeout_hunter(candles, profile, indicators, p)
        
        if decision is None:
            regime_val = getattr(effective_regime, "value", str(effective_regime))
            return self.create_hold(symbol, f"No valid setup in {regime_val}")
        
        # --- Tag the decision ---
        if decision.action != Action.HOLD:
            decision.tags.append("regime_adaptive")
            regime_val = getattr(effective_regime, "value", str(effective_regime))
            decision.tags.append(f"regime:{regime_val}")
            decision.debug["regime"] = effective_regime.value
            decision.debug["adx"] = indicators["adx"]
            decision.debug["atr_ratio"] = indicators["atr_ratio"]
        
        return decision

    # ================================================================
    # Shared Indicator Computation
    # ================================================================

    def _compute_indicators(self, candles: pd.DataFrame, profile: SymbolProfile, p: dict = None) -> Optional[Dict]:
        """Pre-calculate all indicators used across sub-modules."""
        try:
            high = candles["high"].astype(float)
            low = candles["low"].astype(float)
            close = candles["close"].astype(float)
            open_p = candles["open"].astype(float)
            
            if p is None:
                p = self.params
            
            # --- ADX ---
            adx_df = ta.adx(high, low, close, length=14)
            adx = adx_df["ADX_14"].iloc[-1] if adx_df is not None else 0.0
            di_plus = adx_df["DMP_14"].iloc[-1] if adx_df is not None else 0.0
            di_minus = adx_df["DMN_14"].iloc[-1] if adx_df is not None else 0.0
            
            # --- ATR ---
            atr_series = ta.atr(high, low, close, length=p["atr_period"])
            atr = atr_series.iloc[-1] if atr_series is not None else 0.0
            atr_avg = atr_series.iloc[-50:].mean() if atr_series is not None and len(atr_series) >= 50 else atr
            atr_ratio = atr / atr_avg if atr_avg > 0 else 1.0
            
            # --- RSI ---
            rsi_series = ta.rsi(close, length=p["rsi_period"])
            rsi = rsi_series.iloc[-1] if rsi_series is not None else 50.0
            
            # --- EMAs ---
            ema_fast = ta.ema(close, length=p["trend_ema_fast"])
            ema_slow = ta.ema(close, length=p["trend_ema_slow"])
            ema_filter = ta.ema(close, length=p["trend_ema_filter"])
            
            ema_fast_val = ema_fast.iloc[-1] if ema_fast is not None else close.iloc[-1]
            ema_slow_val = ema_slow.iloc[-1] if ema_slow is not None else close.iloc[-1]
            ema_filter_val = ema_filter.iloc[-1] if ema_filter is not None else close.iloc[-1]
            
            ema_fast_prev = ema_fast.iloc[-2] if ema_fast is not None and len(ema_fast) >= 2 else ema_fast_val
            ema_slow_prev = ema_slow.iloc[-2] if ema_slow is not None and len(ema_slow) >= 2 else ema_slow_val
            
            # --- Bollinger Bands ---
            bb = ta.bbands(close, length=p["range_bb_length"], std=p["range_bb_std"])
            bb_lower = bb.iloc[-1, 0] if bb is not None else close.iloc[-1] - atr
            bb_mid = bb.iloc[-1, 1] if bb is not None else close.iloc[-1]
            bb_upper = bb.iloc[-1, 2] if bb is not None else close.iloc[-1] + atr
            
            # --- Volume (tick_volume) ---
            volume = candles.get("tick_volume", candles.get("volume", pd.Series([0] * len(candles))))
            vol_avg = volume.iloc[-20:].mean() if len(volume) >= 20 else volume.mean()
            vol_current = volume.iloc[-1]
            vol_ratio = vol_current / vol_avg if vol_avg > 0 else 1.0
            
            # --- Current Price ---
            price = close.iloc[-1]
            
            # --- Wick Analysis (last 5 candles) ---
            range_len = (high - low).values[-5:]
            body_len = np.abs((close - open_p).values[-5:])
            range_safe = np.where(range_len == 0, 1e-9, range_len)
            wick_ratio = np.mean((range_safe - body_len) / range_safe)
            
            # --- Highest/Lowest lookback ---
            lb = p["fake_wick_lookback"]
            highest = high.iloc[-lb:].max()
            lowest = low.iloc[-lb:].min()
            
            # --- SMA 200 (Long-term trend filter) ---
            sma200 = close.rolling(window=200).mean()
            sma200_val = float(sma200.iloc[-1]) if len(close) >= 200 and pd.notna(sma200.iloc[-1]) else None
            
            return {
                "adx": round(float(adx), 2),
                "di_plus": round(float(di_plus), 2),
                "di_minus": round(float(di_minus), 2),
                "atr": float(atr),
                "atr_avg": float(atr_avg),
                "atr_ratio": round(float(atr_ratio), 2),
                "rsi": round(float(rsi), 1),
                "rsi_series": rsi_series,
                "ema_fast": float(ema_fast_val),
                "ema_slow": float(ema_slow_val),
                "ema_filter": float(ema_filter_val),
                "ema_fast_prev": float(ema_fast_prev),
                "ema_slow_prev": float(ema_slow_prev),
                "bb_lower": float(bb_lower),
                "bb_mid": float(bb_mid),
                "bb_upper": float(bb_upper),
                "vol_ratio": round(float(vol_ratio), 2),
                "vol_current": float(vol_current),
                "vol_avg": float(vol_avg),
                "price": float(price),
                "wick_ratio": round(float(wick_ratio), 3),
                "highest": float(highest),
                "lowest": float(lowest),
                "high": high,
                "low": low,
                "close": close,
                "open": open_p,
                "sma200": sma200_val,
            }
            
        except Exception as e:
            logger.error(f"regime_adaptive_indicator_error: {e}", exc_info=True)
            return None

    # ================================================================
    # Internal Regime Verification
    # ================================================================
    
    def _verify_regime(self, passed_regime: RegimeType, ind: Dict) -> RegimeType:
        """
        Double-check the regime using our own indicators.
        Trust the passed regime but override in clear cases.
        """
        adx = ind["adx"]
        atr_ratio = ind["atr_ratio"]
        wick_ratio = ind["wick_ratio"]
        
        # Strong evidence of fakeout → override
        if wick_ratio > 0.6 and adx < 20:
            return RegimeType.FAKEOUT
        
        # Extreme volatility → override
        if atr_ratio > 2.0:
            return RegimeType.HIGH_VOLATILITY
        
        # Ultra low volatility → patience
        if atr_ratio < 0.4:
            return RegimeType.LOW_VOLATILITY
        
        # Strong trend evidence but regime says RANGING → override to trending
        if adx > 30 and passed_regime == RegimeType.RANGING:
            if ind["ema_fast"] > ind["ema_slow"]:
                return RegimeType.TRENDING_UP
            else:
                return RegimeType.TRENDING_DOWN
        
        # Weak ADX but regime says trending → trust strategy knows better
        # but if ADX < 18, it's likely ranging
        if adx < 18 and passed_regime in (RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN):
            return RegimeType.RANGING
        
        # Default: trust the passed regime
        return passed_regime

    # ================================================================
    # Module 1: Trend Follower (TRENDING_UP / TRENDING_DOWN)
    # ================================================================
    
    def _trend_follower(
        self, candles: pd.DataFrame, profile: SymbolProfile, 
        ind: Dict, regime: RegimeType, p: dict = None
    ) -> Decision:
        """
        Trend Following with pullback entry.
        
        Logic:
        1. EMA 9 > EMA 21 > EMA 50 → Uptrend (or reverse for downtrend)
        2. ADX > threshold → Trend is strong enough
        3. Price pulls back to EMA 21 zone (within ATR tolerance)
        4. RSI confirms momentum (not overbought for buy, not oversold for sell)
        5. Candle pattern confirmation (engulfing, pin bar at EMA)
        """
        if p is None:
            p = self.params
        symbol = profile.symbol
        price = ind["price"]
        atr = ind["atr"]
        
        if atr <= 0:
            return self.create_hold(symbol, "ATR is zero")
        
        confidence = 0
        reasons = []
        
        is_uptrend = regime == RegimeType.TRENDING_UP
        
        # --- 1. EMA Alignment Check (strict: require full triple alignment or fresh cross) ---
        if is_uptrend:
            ema_aligned = ind["ema_fast"] > ind["ema_slow"] > ind["ema_filter"]
            ema_cross = ind["ema_fast"] > ind["ema_slow"] and ind["ema_fast_prev"] <= ind["ema_slow_prev"]
        else:
            ema_aligned = ind["ema_fast"] < ind["ema_slow"] < ind["ema_filter"]
            ema_cross = ind["ema_fast"] < ind["ema_slow"] and ind["ema_fast_prev"] >= ind["ema_slow_prev"]
        
        if ema_aligned:
            confidence += 25
            reasons.append("EMA aligned")
        elif ema_cross:
            confidence += 20
            reasons.append("EMA cross")
        else:
            # No partial alignment allowed — strict mode
            return self.create_hold(symbol, "EMA not fully aligned with trend")
        
        # --- 1.3 SMA200 Long-Term Trend Gate (Option C: 60%+ WR) ---
        sma200_enabled = p.get("sma200_filter_enabled", True)
        sma200 = ind.get("sma200")
        if sma200_enabled and sma200 is not None:
            if is_uptrend and price < sma200:
                return self.create_hold(symbol, f"SMA200 disagrees: price {price:.2f} < SMA200 {sma200:.2f} (no BUY)")
            elif not is_uptrend and price > sma200:
                return self.create_hold(symbol, f"SMA200 disagrees: price {price:.2f} > SMA200 {sma200:.2f} (no SELL)")
            else:
                confidence += 10
                reasons.append("SMA200 aligned")
        
        # --- 1.5 DI+/DI- Directional Confirmation ---
        if is_uptrend and ind["di_plus"] > ind["di_minus"]:
            confidence += 5
            reasons.append("DI+ > DI-")
        elif not is_uptrend and ind["di_minus"] > ind["di_plus"]:
            confidence += 5
            reasons.append("DI- > DI+")
        elif is_uptrend and ind["di_minus"] > ind["di_plus"] + 5:
            # DI disagrees strongly with trend direction → skip
            return self.create_hold(symbol, f"DI disagrees with uptrend (DI+ {ind['di_plus']} < DI- {ind['di_minus']})")
        elif not is_uptrend and ind["di_plus"] > ind["di_minus"] + 5:
            return self.create_hold(symbol, f"DI disagrees with downtrend (DI- {ind['di_minus']} < DI+ {ind['di_plus']})")
        
        # --- 2. ADX Strength ---
        if ind["adx"] >= p["trend_adx_min"]:
            confidence += 20
            reasons.append(f"ADX {ind['adx']} (strong)")
        else:
            # V4: No moderate ADX — reject outright for high WR
            return self.create_hold(symbol, f"ADX {ind['adx']} < {p['trend_adx_min']} (too weak)")
        
        # --- 3. Pullback to EMA 21 Zone ---
        pullback_distance = abs(price - ind["ema_slow"]) / atr
        
        # Hard reject if chasing too far from EMA
        if pullback_distance > p["trend_max_chase"]:
            return self.create_hold(
                symbol, f"Chasing: price {pullback_distance:.1f} ATR from EMA21 > max {p['trend_max_chase']}"
            )
        
        if pullback_distance <= p["trend_pullback_tolerance"]:
            # Price is near EMA 21 — perfect pullback entry
            confidence += 25
            reasons.append(f"Pullback to EMA21 ({pullback_distance:.1f} ATR)")
        else:
            # V4: No partial credit — must be in tight pullback zone
            return self.create_hold(
                symbol, f"Pullback {pullback_distance:.1f} ATR > tolerance {p['trend_pullback_tolerance']}"
            )
        
        # --- 4. RSI Momentum Confirmation ---
        rsi = ind["rsi"]
        if is_uptrend:
            if 40 <= rsi <= p["trend_rsi_buy_max"]:
                confidence += 15
                reasons.append(f"RSI {rsi} (bullish momentum)")
            elif rsi > p["trend_rsi_buy_max"]:
                confidence -= 15
                reasons.append(f"RSI {rsi} OVERBOUGHT — skip")
        else:
            if p["trend_rsi_sell_min"] <= rsi <= 60:
                confidence += 15
                reasons.append(f"RSI {rsi} (bearish momentum)")
            elif rsi < p["trend_rsi_sell_min"]:
                confidence -= 15
                reasons.append(f"RSI {rsi} OVERSOLD — skip")
        
        # --- 5. Candle Pattern (REQUIRED for V4 high-WR) ---
        last_candle_bullish = ind["close"].iloc[-1] > ind["open"].iloc[-1]
        prev_candle_bearish = ind["close"].iloc[-2] < ind["open"].iloc[-2] if len(candles) > 1 else False
        prev_candle_bullish = ind["close"].iloc[-2] > ind["open"].iloc[-2] if len(candles) > 1 else False
        
        if is_uptrend and last_candle_bullish:
            confidence += 10
            reasons.append("Bullish candle")
            if prev_candle_bearish:
                confidence += 5
                reasons.append("Engulfing pattern")
        elif not is_uptrend and not last_candle_bullish:
            confidence += 10
            reasons.append("Bearish candle")
            if prev_candle_bullish:
                confidence += 5
                reasons.append("Engulfing pattern")
        else:
            # V4: candle must agree with direction
            return self.create_hold(symbol, "Candle disagrees with trend direction")
        
        # --- Volume Confirmation ---
        if ind["vol_ratio"] > 1.2:
            confidence += 5
            reasons.append(f"Volume spike {ind['vol_ratio']:.1f}x")
        
        # --- Decision (use trend-specific confidence threshold) ---
        trend_min_conf = p.get("trend_min_confidence", p["min_confidence"])
        if confidence < trend_min_conf:
            return self.create_hold(
                symbol, 
                f"Trend: conf {confidence} < {trend_min_conf} | {'; '.join(reasons)}"
            )
        
        action = Action.BUY if is_uptrend else Action.SELL
        
        # --- SL/TP Calculation (Anti-Stop-Hunt Protected) ---
        direction = "BUY" if action == Action.BUY else "SELL"
        sl = apply_anti_hunt_sl(
            candles, price, atr, direction,
            atr_mult=p["trend_sl_atr_mult"],
            min_sl_distance=atr * 0.5,
        )
        if action == Action.BUY:
            tp = price + atr * p["trend_tp_atr_mult"]
        else:
            tp = price - atr * p["trend_tp_atr_mult"]
        
        risk = abs(price - sl)
        reward = abs(tp - price)
        rr = reward / risk if risk > 0 else 0
        
        if rr < p["trend_min_rr"]:
            return self.create_hold(symbol, f"Trend: RR {rr:.2f} < {p['trend_min_rr']}")
        
        return Decision(
            symbol=symbol,
            action=action,
            confidence=round(confidence / 100, 2),
            reason=" + ".join(reasons),
            stop_loss=round(sl, profile.digits),
            take_profit=round(tp, profile.digits),
            risk_reward_ratio=round(rr, 2),
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["trend_follower"],
            debug={
                "sub_module": "trend_follower",
                "pullback_dist": round(pullback_distance, 2),
            }
        )

    # ================================================================
    # Module 2: Range Trader (RANGING / LOW_VOLATILITY)
    # ================================================================
    
    def _range_trader(
        self, candles: pd.DataFrame, profile: SymbolProfile, ind: Dict, p: dict = None
    ) -> Decision:
        """
        Mean Reversion in Sideways Markets.
        
        Logic:
        1. ADX < 25 confirms no trend
        2. Price near Bollinger Band extremes
        3. RSI oversold/overbought confluence
        4. Keltner Channel squeeze detection for bonus confidence
        5. Conservative TP = middle BB band
        """
        if p is None:
            p = self.params
        symbol = profile.symbol
        price = ind["price"]
        atr = ind["atr"]
        
        if atr <= 0:
            return self.create_hold(symbol, "ATR is zero")
        
        # Double-check ranging condition
        if ind["adx"] > p["range_adx_max"] + 5:  # small tolerance
            return self.create_hold(symbol, f"ADX {ind['adx']} too high for range trading")
        
        confidence = 0
        reasons = []
        action = Action.HOLD
        
        bb_range = ind["bb_upper"] - ind["bb_lower"]
        if bb_range <= 0:
            return self.create_hold(symbol, "BB range is zero")
        
        # --- Position within Bollinger Bands ---
        bb_position = (price - ind["bb_lower"]) / bb_range  # 0=lower, 1=upper
        
        # --- BUY: Price near lower band ---
        # --- BUY: Price near lower band ---
        if bb_position <= p.get("range_bb_buy_zone", 0.08):
            confidence += 30
            reasons.append(f"Near Lower BB ({bb_position:.0%})")
            
            # RSI Oversold confirmation (REQUIRED for V4)
            if ind["rsi"] < p["range_rsi_os"]:
                confidence += 25
                reasons.append(f"RSI {ind['rsi']} Oversold")
            else:
                # V4: RSI must confirm — no trades without RSI agreement
                return self.create_hold(symbol, f"Range BUY: RSI {ind['rsi']} not oversold (need < {p['range_rsi_os']})")
            
            # Bullish candle rejection
            if ind["close"].iloc[-1] > ind["open"].iloc[-1]:
                confidence += 10
                reasons.append("Bullish candle")
            
            # Volume on bounce
            if ind["vol_ratio"] > 1.3:
                confidence += 5
                reasons.append("Volume on bounce")
            
            range_min_conf = p.get("range_min_confidence", p["min_confidence"])
            if confidence >= range_min_conf:
                action = Action.BUY
        
        # --- SELL: Price near upper band ---
        elif bb_position >= p.get("range_bb_sell_zone", 0.92):
            confidence += 30
            reasons.append(f"Near Upper BB ({bb_position:.0%})")
            
            # RSI Overbought confirmation (REQUIRED for V4)
            if ind["rsi"] > p["range_rsi_ob"]:
                confidence += 25
                reasons.append(f"RSI {ind['rsi']} Overbought")
            else:
                return self.create_hold(symbol, f"Range SELL: RSI {ind['rsi']} not overbought (need > {p['range_rsi_ob']})")
            
            # Bearish candle
            if ind["close"].iloc[-1] < ind["open"].iloc[-1]:
                confidence += 10
                reasons.append("Bearish candle")
            
            if ind["vol_ratio"] > 1.3:
                confidence += 5
                reasons.append("Volume on rejection")
            
            range_min_conf = p.get("range_min_confidence", p["min_confidence"])
            if confidence >= range_min_conf:
                action = Action.SELL
        
        # --- Keltner Squeeze Bonus ---
        # If BB is inside Keltner Channel → squeeze → expect big move
        try:
            kc = ta.kc(
                ind["high"], ind["low"], ind["close"],
                length=p["range_keltner_length"],
                scalar=p["range_keltner_mult"]
            )
            if kc is not None:
                kc_lower = kc.iloc[-1, 0]
                kc_upper = kc.iloc[-1, 2]
                squeeze = ind["bb_lower"] > kc_lower and ind["bb_upper"] < kc_upper
                if squeeze:
                    confidence += 5
                    reasons.append("KC Squeeze")
        except Exception:
            pass  # Keltner is a bonus, not required
        
        if action == Action.HOLD:
            return self.create_hold(
                symbol, 
                f"Range: no extremes (BB pos {bb_position:.0%}, RSI {ind['rsi']:.0f})"
            )
        
        # --- SL/TP (Anti-Stop-Hunt Protected) ---
        direction = "BUY" if action == Action.BUY else "SELL"
        sl = apply_anti_hunt_sl(
            candles, price, atr, direction,
            atr_mult=p["range_sl_atr_mult"],
            min_sl_distance=atr * 0.3,
        )
        if action == Action.BUY:
            tp = ind["bb_mid"]  # Conservative: target middle
        else:
            tp = ind["bb_mid"]
        
        risk = abs(price - sl)
        reward = abs(tp - price)
        rr = reward / risk if risk > 0 else 0
        
        if rr < p["range_min_rr"]:
            return self.create_hold(symbol, f"Range: RR {rr:.2f} too low")
        
        return Decision(
            symbol=symbol,
            action=action,
            confidence=round(confidence / 100, 2),
            reason=" + ".join(reasons),
            stop_loss=round(sl, profile.digits),
            take_profit=round(tp, profile.digits),
            risk_reward_ratio=round(rr, 2),
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["range_trader", "mean_reversion"],
            debug={
                "sub_module": "range_trader",
                "bb_position": round(bb_position, 3),
                "bb_width": round(bb_range, profile.digits),
            }
        )

    # ================================================================
    # Module 3: Volatility Surfer (HIGH_VOLATILITY)
    # ================================================================
    
    def _volatility_surfer(
        self, candles: pd.DataFrame, profile: SymbolProfile, ind: Dict, p: dict = None
    ) -> Decision:
        """
        Breakout Trading in High Volatility.
        
        Logic:
        1. Price breaks above/below recent N-bar range
        2. Volume spike confirms institutional participation
        3. RSI direction agrees with breakout
        4. Wide SL (ATR × 2.5) to survive noise
        5. Big TP (ATR × 4.0) to capture momentum
        """
        if p is None:
            p = self.params
        symbol = profile.symbol
        price = ind["price"]
        atr = ind["atr"]
        
        if atr <= 0:
            return self.create_hold(symbol, "ATR is zero")
        
        confidence = 0
        reasons = []
        action = Action.HOLD
        
        highest = ind["highest"]
        lowest = ind["lowest"]
        range_size = highest - lowest
        
        if range_size <= 0:
            return self.create_hold(symbol, "No range detected")
        
        # --- Breakout Detection ---
        # Current close vs recent range
        close_vals = ind["close"]
        prev_close = close_vals.iloc[-2]
        
        breakout_up = price > highest and prev_close <= highest
        breakout_down = price < lowest and prev_close >= lowest
        
        # Near breakout (within ATR distance) → potential breakout
        near_breakout_up = (highest - price) / atr <= 0.3 and price > ind["ema_fast"]
        near_breakout_down = (price - lowest) / atr <= 0.3 and price < ind["ema_fast"]
        
        if breakout_up:
            confidence += 35
            reasons.append(f"Breakout above {highest:.{profile.digits}f}")
            action = Action.BUY
        elif breakout_down:
            confidence += 35
            reasons.append(f"Breakout below {lowest:.{profile.digits}f}")
            action = Action.SELL
        else:
            # V4: No near-breakout entries — confirmed breakouts only
            return self.create_hold(symbol, "No confirmed breakout")
        
        # --- Volume Confirmation (REQUIRED for V4) ---
        if ind["vol_ratio"] >= p["vol_volume_spike"]:
            confidence += 20
            reasons.append(f"Volume spike {ind['vol_ratio']:.1f}x")
        else:
            # V4: Volume spike is mandatory for breakout trades
            return self.create_hold(symbol, f"Volume {ind['vol_ratio']:.1f}x < {p['vol_volume_spike']}x required")
        
        # --- RSI Agreement ---
        if action == Action.BUY and ind["rsi"] >= p["vol_rsi_min_buy"]:
            confidence += 15
            reasons.append(f"RSI {ind['rsi']} agrees (bullish)")
        elif action == Action.SELL and ind["rsi"] <= p["vol_rsi_max_sell"]:
            confidence += 15
            reasons.append(f"RSI {ind['rsi']} agrees (bearish)")
        else:
            confidence -= 10
            reasons.append(f"RSI divergence ({ind['rsi']})")
        
        # --- Momentum (EMA direction) ---
        if action == Action.BUY and ind["ema_fast"] > ind["ema_slow"]:
            confidence += 10
            reasons.append("EMA momentum UP")
        elif action == Action.SELL and ind["ema_fast"] < ind["ema_slow"]:
            confidence += 10
            reasons.append("EMA momentum DOWN")
        
        vol_min_conf = p.get("vol_min_confidence", p["min_confidence"])
        if confidence < vol_min_conf:
            return self.create_hold(
                symbol,
                f"Volatility: conf {confidence} < {vol_min_conf} | {'; '.join(reasons)}"
            )
        
        # --- SL/TP (Wide for volatility + Anti-Stop-Hunt) ---
        direction = "BUY" if action == Action.BUY else "SELL"
        sl = apply_anti_hunt_sl(
            candles, price, atr, direction,
            atr_mult=p["vol_sl_atr_mult"],
            min_sl_distance=atr * 0.5,
        )
        if action == Action.BUY:
            tp = price + atr * p["vol_tp_atr_mult"]
        else:
            tp = price - atr * p["vol_tp_atr_mult"]
        
        risk = abs(price - sl)
        reward = abs(tp - price)
        rr = reward / risk if risk > 0 else 0
        
        if rr < p["vol_min_rr"]:
            return self.create_hold(symbol, f"Volatility: RR {rr:.2f} too low")
        
        return Decision(
            symbol=symbol,
            action=action,
            confidence=round(confidence / 100, 2),
            reason=" + ".join(reasons),
            stop_loss=round(sl, profile.digits),
            take_profit=round(tp, profile.digits),
            risk_reward_ratio=round(rr, 2),
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["volatility_surfer", "breakout"],
            trailing_config={
                "enabled": True,
                "activation_r": 1.0,
                "trail_atr_mult": 1.5,
            },
            debug={
                "sub_module": "volatility_surfer",
                "breakout_direction": action.value,
                "range_high": round(highest, profile.digits),
                "range_low": round(lowest, profile.digits),
            }
        )

    # ================================================================
    # Module 4: Fakeout Hunter (FAKEOUT / LIQUIDITY_SWEEP)
    # ================================================================
    
    def _fakeout_hunter(
        self, candles: pd.DataFrame, profile: SymbolProfile, ind: Dict, p: dict = None
    ) -> Decision:
        """
        Trap Reversal after Liquidity Sweep / Fakeout.
        
        Logic (Ghost Protocol inspired):
        1. Detect wick extension beyond recent high/low
        2. Price returns inside prior range within 1-3 candles
        3. Enter on reversal confirmation
        4. SL outside the sweep wick
        5. TP at mid-range or opposite liquidity
        """
        if p is None:
            p = self.params
        symbol = profile.symbol
        atr = ind["atr"]
        
        if atr <= 0:
            return self.create_hold(symbol, "ATR is zero")
        
        confidence = 0
        reasons = []
        
        high = ind["high"]
        low = ind["low"]
        close = ind["close"]
        open_p = ind["open"]
        
        lb = p["fake_wick_lookback"]
        return_bars = p["fake_return_bars"]
        
        if len(candles) < lb + return_bars + 2:
            return self.create_hold(symbol, "Insufficient data for fakeout detection")
        
        # Recent range (excluding last few candles for sweep detection)
        range_high = high.iloc[-(lb + return_bars):-return_bars].max()
        range_low = low.iloc[-(lb + return_bars):-return_bars].min()
        range_mid = (range_high + range_low) / 2
        
        # --- Detect sweep above range ---
        sweep_up = False
        sweep_down = False
        sweep_wick_high = 0.0
        sweep_wick_low = 0.0
        
        # Check if any of the last N candles wicked above the range then closed below
        for i in range(-return_bars, 0):
            candle_high = high.iloc[i]
            candle_close = close.iloc[i]
            candle_low = low.iloc[i]
            candle_open = open_p.iloc[i]
            
            # Sweep UP: wick above range high, then closed back below
            if candle_high > range_high and candle_close < range_high:
                wick = candle_high - max(candle_close, candle_open)
                body = abs(candle_close - candle_open)
                total_range = candle_high - candle_low
                if total_range > 0 and wick / total_range > p["fake_wick_ratio_min"]:
                    sweep_up = True
                    sweep_wick_high = candle_high
                    
            # Sweep DOWN: wick below range low, then closed back above
            if candle_low < range_low and candle_close > range_low:
                wick = min(candle_close, candle_open) - candle_low
                body = abs(candle_close - candle_open)
                total_range = candle_high - candle_low
                if total_range > 0 and wick / total_range > p["fake_wick_ratio_min"]:
                    sweep_down = True
                    sweep_wick_low = candle_low
        
        price = ind["price"]
        action = Action.HOLD
        
        # --- Sweep UP detected → SELL (reversal down) ---
        if sweep_up and price < range_high:
            confidence += 35
            reasons.append(f"Liquidity sweep above {range_high:.{profile.digits}f}")
            
            # Reversal confirmation: current candle is bearish
            if close.iloc[-1] < open_p.iloc[-1]:
                confidence += 15
                reasons.append("Bearish reversal candle")
            
            # RSI was overbought but turning down
            if ind["rsi"] < 60 and ind["rsi_series"] is not None and len(ind["rsi_series"]) >= 2:
                if ind["rsi_series"].iloc[-1] < ind["rsi_series"].iloc[-2]:
                    confidence += 15
                    reasons.append("RSI turning down")
            
            # Volume on sweep
            if ind["vol_ratio"] > 1.3:
                confidence += 10
                reasons.append(f"Volume on sweep ({ind['vol_ratio']:.1f}x)")
            
            if confidence >= p["min_confidence"]:
                action = Action.SELL
        
        # --- Sweep DOWN detected → BUY (reversal up) ---
        elif sweep_down and price > range_low:
            confidence += 35
            reasons.append(f"Liquidity sweep below {range_low:.{profile.digits}f}")
            
            # Reversal: current candle is bullish
            if close.iloc[-1] > open_p.iloc[-1]:
                confidence += 15
                reasons.append("Bullish reversal candle")
            
            # RSI was oversold but turning up
            if ind["rsi"] > 40 and ind["rsi_series"] is not None and len(ind["rsi_series"]) >= 2:
                if ind["rsi_series"].iloc[-1] > ind["rsi_series"].iloc[-2]:
                    confidence += 15
                    reasons.append("RSI turning up")
            
            if ind["vol_ratio"] > 1.3:
                confidence += 10
                reasons.append(f"Volume on sweep ({ind['vol_ratio']:.1f}x)")
            
            if confidence >= p["min_confidence"]:
                action = Action.BUY
        
        if action == Action.HOLD:
            return self.create_hold(
                symbol,
                f"Fakeout: no clear sweep/reversal (conf {confidence})"
            )
        
        # --- SL/TP ---
        if action == Action.BUY:
            # SL outside the sweep low (the wick)
            sl = (sweep_wick_low if sweep_wick_low > 0 else price) - atr * p["fake_sl_atr_mult"]
            tp = range_mid  # Target mid-range
            # Extend TP if range is large enough
            if abs(tp - price) < atr:
                tp = price + atr * p["fake_tp_atr_mult"]
        else:
            sl = (sweep_wick_high if sweep_wick_high > 0 else price) + atr * p["fake_sl_atr_mult"]
            tp = range_mid
            if abs(price - tp) < atr:
                tp = price - atr * p["fake_tp_atr_mult"]
        
        risk = abs(price - sl)
        reward = abs(tp - price)
        rr = reward / risk if risk > 0 else 0
        
        if rr < p["fake_min_rr"]:
            return self.create_hold(symbol, f"Fakeout: RR {rr:.2f} too low")
        
        return Decision(
            symbol=symbol,
            action=action,
            confidence=round(confidence / 100, 2),
            reason=" + ".join(reasons),
            stop_loss=round(sl, profile.digits),
            take_profit=round(tp, profile.digits),
            risk_reward_ratio=round(rr, 2),
            strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["fakeout_hunter", "ghost_protocol", "mean_reversion"],
            debug={
                "sub_module": "fakeout_hunter",
                "sweep_up": sweep_up,
                "sweep_down": sweep_down,
                "range_high": round(range_high, profile.digits),
                "range_low": round(range_low, profile.digits),
            }
        )
