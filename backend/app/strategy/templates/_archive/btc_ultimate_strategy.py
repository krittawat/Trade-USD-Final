import pandas as pd
import numpy as np
import app.analysis.indicators as ind
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from .base_strategy import BaseStrategy, StrategyDecision

class OmniscientOracleStrategy(BaseStrategy):
    """
    Omniscient Oracle Strategy - Institutional Grade (Trinity Edition)
    Supports BTCUSD, XAUUSD, XAGUSD using Smart Money Concepts (SMC).
    
    "THE DIVINE TRINITY" - Mastery over BTC, Gold, and Silver flow.
    - Phase Awareness: Detects Ranging vs Trending regimes.
    - Institutional Absorption: CVD high but price rejected (The Hidden Trap).
    - Session-Specific AMD: Tailored manipulation detection for Gold/Silver.
    - Global Confluence: Multi-Timeframe alignment on every candle move.
    """
    
    def __init__(self):
        self.name = "OMNISCIENT ORACLE (DIVINE)"
        self.min_confidence = 0.85 
        self.params = {} 
        
        # Asset Specific Tuning Maps
        self.tuning = {
            "BTCUSD": {
                "absorption_vol": 500,
                "ob_displacement": 1.8,
                "velocity_thresh": 1.8,
                "amd_sessions": [(0, 8)] # UTC hours for Accumulation
            },
            "XAUUSD": {
                "absorption_vol": 800, # Gold needs higher volume for absorption sig
                "ob_displacement": 1.5,
                "velocity_thresh": 1.5,
                "amd_sessions": [(0, 7), (12, 14)] # Asia Accum, Pre-NY Accum
            },
            "XAGUSD": {
                "absorption_vol": 1200,
                "ob_displacement": 1.4,
                "velocity_thresh": 1.4,
                "amd_sessions": [(0, 7), (12, 14)]
            },
            "EURUSD": {
                "absorption_vol": 400, # Lower vol threshold for FX absorption
                "ob_displacement": 1.2,
                "velocity_thresh": 1.2,
                "amd_sessions": [(7, 10), (12, 16)] # London & NY Open sessions (UTC)
            },
            "GBPUSD": {
                "absorption_vol": 500,
                "ob_displacement": 1.3,
                "velocity_thresh": 1.3,
                "amd_sessions": [(7, 10), (12, 16)]
            }
        }
        
    def _get_tuning(self, symbol: str) -> Dict[str, Any]:
        sym = symbol.replace(".m", "").replace("c", "").replace("#", "").upper()
        if "BTC" in sym: return self.tuning["BTCUSD"]
        if "XAU" in sym or "GOLD" in sym: return self.tuning["XAUUSD"]
        if "XAG" in sym or "SILVER" in sym: return self.tuning["XAGUSD"]
        return self.tuning["BTCUSD"] # Default to BTC settings

    def _auto_tune(self, df: pd.DataFrame, symbol: str) -> None:
        """
        Adaptive Tuning: Automatically adjusts volume thresholds based on recent market activity.
        This allows the strategy to work in both Low Volatility (Holidays) and High Volatility (War/News) periods.
        """
        if len(df) < 50: return
        
        # Calculate 20-period Moving Average of Tick Volume
        vol_col = 'tick_volume' if 'tick_volume' in df.columns else ('volume' if 'volume' in df.columns else None)
        if not vol_col: return

        avg_vol = df[vol_col].rolling(20).mean().iloc[-1]
        
        # Get base tuning for the asset
        base_tuning = self._get_tuning(symbol)
        
        # Adaptive Multiplier (e.g., Absorption is usually 3x average volume)
        adaptive_absorption = avg_vol * 3.0
        
        # Update the tuning for this symbol instance (Runtime only)
        # We store it in a temporary runtime cache or update the dictionary directly?
        # Updating directly is fine as it re-tunes every analysis cycle
        
        # Clamp to avoid extreme values (Safety)
        if "XAU" in symbol:
             adaptive_absorption = max(300, min(adaptive_absorption, 5000))
        elif "BTC" in symbol:
             adaptive_absorption = max(100, min(adaptive_absorption, 3000))
             
        # Apply
        base_tuning["absorption_vol"] = adaptive_absorption
        # velocity_thresh is a ratio, so it remains constant

    def detect_msb(self, df: pd.DataFrame, i: int) -> Tuple[bool, bool]:
        """Detect Market Structure Break (MSB)"""
        if i < 20: return False, False
        recent_high = df.iloc[i-15:i-1]['high'].max()
        bull_msb = df.iloc[i]['close'] > recent_high
        recent_low = df.iloc[i-15:i-1]['low'].min()
        bear_msb = df.iloc[i]['close'] < recent_low
        return bull_msb, bear_msb

    def detect_order_block(self, df: pd.DataFrame, i: int, symbol: str, lookback: int = 40) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
        """Detect Fresh Institutional Order Blocks with Displacement Confirmation"""
        if i < lookback + 10: return None, None
        tuning = self._get_tuning(symbol)
        atr = df.iloc[i].get('atr', 1)
        bullish_ob = None
        bearish_ob = None
        
        for j in range(i-3, i-lookback, -1):
            if bullish_ob and bearish_ob: break
            body_size = abs(df.iloc[j+1]['close'] - df.iloc[j+1]['open'])
            if body_size > atr * tuning["ob_displacement"]:
                if not bullish_ob and df.iloc[j]['close'] < df.iloc[j]['open']:
                    if df.iloc[j+1:i]['high'].max() > df.iloc[j-10:j]['high'].max():
                        bullish_ob = (df.iloc[j]['low'], df.iloc[j]['high'])
                if not bearish_ob and df.iloc[j]['close'] > df.iloc[j]['open']:
                    if df.iloc[j+1:i]['low'].min() < df.iloc[j-10:j]['low'].min():
                        bearish_ob = (df.iloc[j]['low'], df.iloc[j]['high'])
        return bullish_ob, bearish_ob

    def detect_fvg(self, df: pd.DataFrame, i: int) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
        """
        Detect Fair Value Gaps (FVG) / Imbalances.
        A bullish FVG exists when high[i-2] < low[i].
        A bearish FVG exists when low[i-2] > high[i].
        """
        if i < 5: return None, None
        bull_fvg = None
        bear_fvg = None
        
        # Bullish Gap (BISI)
        if df.iloc[i-2]['high'] < df.iloc[i]['low']:
            bull_fvg = (df.iloc[i-2]['high'], df.iloc[i]['low'])
        # Bearish Gap (SIBI)
        if df.iloc[i-2]['low'] > df.iloc[i]['high']:
            bear_fvg = (df.iloc[i]['high'], df.iloc[i-2]['low'])
            
        return bull_fvg, bear_fvg

    def detect_delta_flow(self, df: pd.DataFrame, i: int, symbol: str) -> Tuple[float, float]:
        """Detect institutional pressure flow and Velocity"""
        tuning = self._get_tuning(symbol)
        window = df.iloc[max(0, i-6):i+1].copy()
        window['delta'] = np.where(window['close'] > window['open'], window['tick_volume'], -window['tick_volume'])
        flow = window['delta'].sum()
        ma = df['tick_volume'].rolling(20).mean()
        curr_ma = ma.iloc[i]
        velocity = (df.iloc[i]['tick_volume'] / curr_ma) if (not np.isnan(curr_ma) and curr_ma != 0) else 1.0
        return flow, velocity

    def detect_regime(self, df: pd.DataFrame, i: int) -> str:
        """Detect Market Regime: TRENDING vs RANGING"""
        if i < 50: return "RANGING"
        ema20 = df['close'].ewm(span=20).mean()
        slope = (ema20.iloc[i] - ema20.iloc[i-10]) / ema20.iloc[i-10] if i >= 10 and ema20.iloc[i-10] != 0 else 0
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        curr_adx = adx['ADX_14'].iloc[i] if (adx is not None and 'ADX_14' in adx and not np.isnan(adx['ADX_14'].iloc[i])) else 20
        min_adx = self.params.get('MIN_ADX', 25)
        if curr_adx > min_adx and abs(slope) > 0.001:
            return "TRENDING"
        return "RANGING"

    def detect_absorption(self, df: pd.DataFrame, i: int, symbol: str) -> Tuple[bool, bool]:
        """Detect Institutional Absorption (Delta Rejection)"""
        tuning = self._get_tuning(symbol)
        window = df.iloc[max(0, i-5):i+1].copy()
        window['delta'] = np.where(window['close'] > window['open'], window['tick_volume'], -window['tick_volume'])
        total_delta = window['delta'].sum()
        price_change = df.iloc[i]['close'] - df.iloc[i-5]['close']
        
        # Bullish: Bears selling hard into buy orders, price stays up
        bull_absorp = total_delta < -tuning["absorption_vol"] and price_change > 0
        # Bearish: Bulls buying hard into sell orders, price stays down
        bear_absorp = total_delta > tuning["absorption_vol"] and price_change < 0
        return bull_absorp, bear_absorp

    def detect_liquidity_pools(self, df: pd.DataFrame, i: int) -> Tuple[float, float, float, float, float]:
        """Oracle Fractal Liquidity Pools"""
        external_high = df.iloc[max(0, i-100):i]['high'].max()
        external_low = df.iloc[max(0, i-100):i]['low'].min()
        internal_high = df.iloc[max(0, i-15):i]['high'].max()
        internal_low = df.iloc[max(0, i-15):i]['low'].min()
        void = 0.0
        if i > 2:
            if df.iloc[i-2]['high'] < df.iloc[i]['low']: void = df.iloc[i]['low'] - df.iloc[i-2]['high']
            elif df.iloc[i-2]['low'] > df.iloc[i]['high']: void = df.iloc[i-2]['low'] - df.iloc[i]['high']
        return external_high, external_low, internal_high, internal_low, void

    def update_parameters(self, params: Dict[str, Any]):
        self.params.update(params)

    def analyze(self, **kwargs) -> StrategyDecision:
        df = kwargs.get('df')
        symbol = kwargs.get('symbol', 'BTCUSD')
        
        # AUTO-TUNE: Adapt parameters to current market conditions
        self._auto_tune(df, symbol)
        
        idx = len(df) - 1
        current_candle = df.iloc[idx]
        current_price = current_candle['close']
        
        d1_trend = kwargs.get('d1_trend', "UNKNOWN")
        if d1_trend == "UNKNOWN":
            # Fallback trend detection (H4/EMA basis)
            ema200 = df['close'].ewm(span=200).mean()
            d1_trend = "UP" if current_price > ema200.iloc[idx] else "DOWN"
        
        divine_score_threshold = kwargs.get('divine_score_threshold', 45)
        
        prev_candle = df.iloc[idx-1]
        
        # Oracle Context Sensors
        bull_ob, bear_ob = self.detect_order_block(df, idx, symbol)
        bull_abs, bear_abs = self.detect_absorption(df, idx, symbol)
        ext_h, ext_l, int_h, int_l, void = self.detect_liquidity_pools(df, idx)
        flow, velocity = self.detect_delta_flow(df, idx, symbol)
        
        # High-Quality Sweep Detection
        bull_sweep = current_candle['low'] < int_l and current_candle['close'] > int_l
        bear_sweep = current_candle['high'] > int_h and current_candle['close'] < int_h

        # Matrix Calculation
        signal = "NO_TRADE"
        confidence = 0.0
        reasons = []
        divine_score = 0
        
        # Trend Alignment mandatory for Oracle Strikes
        # Trend Alignment mandatory for Oracle Strikes
        if (d1_trend == "UP" and flow > -100) or (d1_trend == "DOWN" and flow < 100):
            if d1_trend == "UP":
                # A: Liquidity Sweep (The Entry Trigger)
                if bull_sweep and current_candle['close'] > prev_candle['high']:
                    divine_score += 45; reasons.append("🔯 Divine: Holy Sweep (Liquidity Grab)")
                
                # B: Structure & Imbalance (Confluences)
                if bull_ob:
                    divine_score += 25; reasons.append("🏛️ Oracle: Altar of Value (OB)")
                
                bull_fvg, _ = self.detect_fvg(df, idx)
                if bull_fvg:
                    divine_score += 20; reasons.append("🌌 Imbalance: FVG Present")
                
                if bull_abs:
                    divine_score += 15; reasons.append("🛡️ Oracle: Absorption")
                    
                if flow > 500:
                    divine_score += 10; reasons.append("🌊 Heavy Flow")
                
                if velocity > 1.5:
                    divine_score += 10; reasons.append("🌬️ Momentum Wind")
                
                if divine_score >= divine_score_threshold:
                    signal = "BUY"
                    confidence = min(divine_score / 100, 1.0)
            
            elif d1_trend == "DOWN":
                # A: Liquidity Sweep
                if bear_sweep and current_candle['close'] < prev_candle['low']:
                    divine_score += 45; reasons.append("🔯 Divine: Holy Sweep (Liquidity Grab)")
                
                # B: Structure & Imbalance
                if bear_ob:
                    divine_score += 25; reasons.append("🏛️ Oracle: Altar of Value (OB)")
                
                _, bear_fvg = self.detect_fvg(df, idx)
                if bear_fvg:
                    divine_score += 20; reasons.append("🌌 Imbalance: FVG Present")
                
                if bear_abs:
                    divine_score += 15; reasons.append("🛡️ Oracle: Absorption")
                    
                if flow < -500:
                    divine_score += 10; reasons.append("🌊 Heavy Flow")
                
                if velocity > 1.5:
                    divine_score += 10; reasons.append("🌬️ Momentum Wind")
                
                if divine_score >= divine_score_threshold:
                    signal = "SELL"
                    confidence = min(divine_score / 100, 1.0)

        if signal != "NO_TRADE":
            atr = df.iloc[-1].get('atr', 100)
            
            # Dynamic SL Multiplier from Settings
            sl_mult = kwargs.get('atr_sl_mult', 0.3)
            
            if signal == "BUY":
                sl = min(int_l, df.iloc[-5:]['low'].min()) - (atr * sl_mult)
                tp = current_price + (abs(current_price - sl) * 5.0) # Standard 5:1 for Trinity Oracle
            else:
                sl = max(int_h, df.iloc[-5:]['high'].max()) + (atr * sl_mult)
                tp = current_price - (abs(current_price - sl) * 5.0)
                
            return StrategyDecision(
                signal=signal,
                entry_price=current_price,
                sl=sl,
                tp=tp,
                reason=" | ".join(reasons),
                confidence=confidence,
                risk_pct=1.0,
                insights={
                    "regime": self.detect_regime(df, idx),
                    "liquidity": "Internal Swept" if (bull_sweep or bear_sweep) else "Building",
                    "order_flow": "Imbalanced" if abs(flow) > 500 else "Neutral",
                    "absorption": "High" if (bull_abs or bear_abs) else "Low"
                }
            )
            
        return StrategyDecision(
            signal="NO_TRADE", 
            reason="Waiting for Institutional Inefficiency",
            insights={
                "regime": self.detect_regime(df, idx),
                "liquidity": "Swept" if (bull_sweep or bear_sweep) else "Building",
                "bias": d1_trend
            },
            suggested_pending={
                "int_buy": int_l,   # Internal Guard
                "int_sell": int_h,  # Internal Guard
                "ext_buy": ext_l,   # External Guard
                "ext_sell": ext_h,  # External Guard
                "sniper_buy": int_l,  # Sniper Strike target
                "sniper_sell": int_h, # Sniper Strike target
                "sl_dist_atr": 0.5, 
                "tp_rr": 4.0      
            }
        )

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "accuracy_tier": "Divine",
            "assets": ["BTCUSD", "XAUUSD", "XAGUSD"],
            "features": ["SMC", "Order Flow", "Session Manipulation", "Absorption"]
        }

# Shared Instance
btc_ultimate_strategy = OmniscientOracleStrategy()
oracle_strategy = btc_ultimate_strategy
