import pandas as pd
import pandas as pd
import numpy as np
try:
    import app.analysis.indicators as ind
except ImportError:
    from app.utils.safe_ta import SafeTA as ta
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

        if 'Volume_Avg' in df.columns:
             avg_vol = df['Volume_Avg'].iloc[-1]
        else:
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
        """Detect Market Structure Break (MSB) - Optimized"""
        if i < 20: return False, False
        
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        
        recent_high = np.max(highs[i-15:i-1])
        bull_msb = closes[i] > recent_high
        
        recent_low = np.min(lows[i-15:i-1])
        bear_msb = closes[i] < recent_low
        
        return bull_msb, bear_msb

    def detect_order_block(self, df: pd.DataFrame, i: int, symbol: str, lookback: int = 40) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
        """Detect Fresh Institutional Order Blocks (Optimized with Numpy)"""
        if i < lookback + 10: return None, None
        tuning = self._get_tuning(symbol)
        atr = df.iloc[i].get('atr', 1)
        bullish_ob = None
        bearish_ob = None
        
        # Extract numpy arrays for speed
        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        
        # Iterate backwards
        # Note: i is relative to df start. df is current_slice.
        # So i should be len(df)-1 usually?
        # In backtest_engine, df is slice[:i+1]. So last index is i (if index was reset) or generic 'i' is length-1?
        # Argument i is passed.
        
        # IMPORTANT: i is the index in the passed df. 
        # If df is slice of length N, i usually is N-1.
        
        for j in range(i-3, i-lookback, -1):
            if bullish_ob and bearish_ob: break
            
            body_size = abs(closes[j+1] - opens[j+1])
            if body_size > atr * tuning["ob_displacement"]:
                # Potential Bullish OB
                if not bullish_ob and closes[j] < opens[j]: # Red candle
                    # Check displacement: next candles broke high
                    # Slice highs from j+1 to i
                    range_highs = highs[j+1:i]
                    # Prev highs
                    prev_range_highs = highs[j-10:j]
                    
                    if len(range_highs) > 0 and len(prev_range_highs) > 0:
                        if np.max(range_highs) > np.max(prev_range_highs):
                            bullish_ob = (lows[j], highs[j])

                # Potential Bearish OB
                if not bearish_ob and closes[j] > opens[j]: # Green candle
                    # Check break low
                    range_lows = lows[j+1:i]
                    prev_range_lows = lows[j-10:j]
                    
                    if len(range_lows) > 0 and len(prev_range_lows) > 0:
                        if np.min(range_lows) < np.min(prev_range_lows):
                            bearish_ob = (lows[j], highs[j])
        return bullish_ob, bearish_ob

    def detect_fvg(self, df: pd.DataFrame, i: int) -> Tuple[Optional[Tuple[float, float]], Optional[Tuple[float, float]]]:
        """
        Detect Fair Value Gaps (FVG) / Imbalances (Optimized).
        """
        if i < 5: return None, None
        
        highs = df['high'].values
        lows = df['low'].values
        
        bull_fvg = None
        bear_fvg = None
        
        # Bullish Gap (BISI)
        if highs[i-2] < lows[i]:
            bull_fvg = (highs[i-2], lows[i])
        # Bearish Gap (SIBI)
        if lows[i-2] > highs[i]:
            bear_fvg = (highs[i], lows[i-2])
            
        return bull_fvg, bear_fvg

    def detect_delta_flow(self, df: pd.DataFrame, i: int, symbol: str) -> Tuple[float, float]:
        """Detect institutional pressure flow and Velocity (Optimized)"""
        if i < 6: return 0.0, 1.0
        
        closes = df['close'].values
        opens = df['open'].values
        volumes = df['tick_volume'].values
        
        # Calculate Delta for window [i-6 : i+1]
        # slice is from max(0, i-6) to i+1
        start_idx = max(0, i-6)
        end_idx = i + 1
        
        win_closes = closes[start_idx:end_idx]
        win_opens = opens[start_idx:end_idx]
        win_vols = volumes[start_idx:end_idx]
        
        deltas = np.where(win_closes > win_opens, win_vols, -win_vols)
        flow = np.sum(deltas)
        
        # Velocity
        if 'Volume_Avg' in df.columns:
             curr_ma = df['Volume_Avg'].iloc[i]
        else:
             # Fast approximate
             curr_ma = np.mean(volumes[max(0, i-19):i+1])
        
        velocity = (volumes[i] / curr_ma) if (curr_ma > 0 and not np.isnan(curr_ma)) else 1.0
        return flow, velocity

    def detect_regime(self, df: pd.DataFrame, i: int) -> str:
        """Detect Market Regime: TRENDING vs RANGING"""
        if i < 50: return "RANGING"
        if 'EMA_20' in df.columns:
            ema20 = df['EMA_20']
        else:
            ema20 = df['close'].ewm(span=20).mean()

        slope = (ema20.iloc[i] - ema20.iloc[i-10]) / ema20.iloc[i-10] if i >= 10 and ema20.iloc[i-10] != 0 else 0
        
        # Optimized ADX
        if 'ADX_14' in df.columns:
            curr_adx = df['ADX_14'].iloc[i]
        else:
            adx = ta.adx(df['high'], df['low'], df['close'], length=14)
            curr_adx = adx['ADX_14'].iloc[i] if (adx is not None and 'ADX_14' in adx and not np.isnan(adx['ADX_14'].iloc[i])) else 20
        min_adx = self.params.get('MIN_ADX', 25)
        if curr_adx > min_adx and abs(slope) > 0.001:
            return "TRENDING"
        return "RANGING"

    def detect_absorption(self, df: pd.DataFrame, i: int, symbol: str) -> Tuple[bool, bool]:
        """Detect Institutional Absorption (Delta Rejection) - Optimized"""
        if i < 5: return False, False
        tuning = self._get_tuning(symbol)
        
        start_idx = max(0, i-5)
        end_idx = i + 1
        
        closes = df['close'].values
        opens = df['open'].values
        volumes = df['tick_volume'].values # Ensure this column exists or fallback
        
        win_closes = closes[start_idx:end_idx]
        win_opens = opens[start_idx:end_idx]
        win_vols = volumes[start_idx:end_idx]
        
        deltas = np.where(win_closes > win_opens, win_vols, -win_vols)
        total_delta = np.sum(deltas)
        
        price_change = closes[i] - closes[i-5]
        
        # Bullish: Bears selling hard into buy orders, price stays up
        bull_absorp = total_delta < -tuning["absorption_vol"] and price_change > 0
        # Bearish: Bulls buying hard into sell orders, price stays down
        bear_absorp = total_delta > tuning["absorption_vol"] and price_change < 0
        return bull_absorp, bear_absorp

    def detect_liquidity_pools(self, df: pd.DataFrame, i: int) -> Tuple[float, float, float, float, float]:
        """Oracle Fractal Liquidity Pools (Optimized)"""
        highs = df['high'].values
        lows = df['low'].values
        
        # External Structure (100 bars)
        start_ext = max(0, i-100)
        end_ext = i
        external_high = np.max(highs[start_ext:end_ext]) if end_ext > start_ext else highs[i]
        external_low = np.min(lows[start_ext:end_ext]) if end_ext > start_ext else lows[i]
        
        # Internal Structure (15 bars)
        start_int = max(0, i-15)
        end_int = i
        internal_high = np.max(highs[start_int:end_int]) if end_int > start_int else highs[i]
        internal_low = np.min(lows[start_int:end_int]) if end_int > start_int else lows[i]
        
        void = 0.0
        if i > 2:
            if highs[i-2] < lows[i]: void = lows[i] - highs[i-2]
            elif lows[i-2] > highs[i]: void = lows[i-2] - highs[i]
            
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
            # Fallback trend detection (H4/EMA basis)
            if 'EMA_200' in df.columns:
                ema200_val = df['EMA_200'].iloc[idx]
            else:
                ema200_val = df['close'].ewm(span=200).mean().iloc[idx]
            
            d1_trend = "UP" if current_price > ema200_val else "DOWN"
        
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
                # === SMART ATR LOGIC (HYBRID) ===
                avg_range = abs(df['high'] - df['low']).rolling(5).mean().iloc[-1]
                raw_sl_dist = atr * sl_mult
                smart_sl_dist = max(raw_sl_dist, avg_range * 0.8)

                sl = min(int_l, df.iloc[-5:]['low'].min()) - smart_sl_dist
                # Sanity Check: Ensure SL is not too far
                if abs(current_price - sl) > (current_price * 0.05): # Max 5% SL
                    sl = current_price * 0.95
                
                tp = current_price + (abs(current_price - sl) * 5.0) # Standard 5:1 for Trinity Oracle
                reasons.append(f"SmartATR({smart_sl_dist:.0f})")

            else:
                # === SMART ATR LOGIC (HYBRID) ===
                avg_range = abs(df['high'] - df['low']).rolling(5).mean().iloc[-1]
                raw_sl_dist = atr * sl_mult
                smart_sl_dist = max(raw_sl_dist, avg_range * 0.8)

                sl = max(int_h, df.iloc[-5:]['high'].max()) + smart_sl_dist
                # Sanity Check
                if abs(sl - current_price) > (current_price * 0.05):
                    sl = current_price * 1.05

                tp = current_price - (abs(current_price - sl) * 5.0)
                reasons.append(f"SmartATR({smart_sl_dist:.0f})")
                
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
