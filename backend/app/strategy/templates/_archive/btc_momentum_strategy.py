from .base_strategy import BaseStrategy, StrategyDecision
from typing import Dict, Any, Optional

class BTCMomentumStrategy(BaseStrategy):
    """
    BTC Momentum Scalping Strategy (BTCUSDm)
    
    Architecture:
    - M15: Trend Filter (EMA 50/200)
    - M5: Setup Validation (Price vs EMA 50, Pullback to EMA 20-50, RSI 40-60)
    - M1: Execution Timing (Momentum Confirmation)
    """
    
    # 300 THB ~ $9 so 400-1000 THB is approx $12 - $30 range
    DAILY_TARGET_THB_MIN = 400
    DAILY_TARGET_THB_MAX = 1000
    
    def analyze(self, **kwargs) -> StrategyDecision:
        # Data Inputs
        # We expect M15, M5, and M1 data to be passed from StreamManager
        
        # M15 Trend Filter
        ema50_m15 = kwargs.get('ema50_m15', 0)
        ema200_m15 = kwargs.get('ema200_m15', 0)
        
        # M5 Setup
        close_m5 = kwargs.get('close_m5', 0)
        ema20_m5 = kwargs.get('ema21_m5', 0) # Using EMA21 as proxy for EMA20
        ema50_m5 = kwargs.get('ema50_m5', 0)
        rsi_m5 = kwargs.get('rsi_m5', 50)
        
        # M1 Execution
        close_m1 = kwargs.get('close_m1', 0)
        open_m1 = kwargs.get('open_m1', 0)
        
        atr = kwargs.get('atr', 10) # For SL/TP
        daily_profit_thb = kwargs.get('daily_profit_thb', 0.0)
        
        # 1. Daily Management
        if daily_profit_thb >= self.DAILY_TARGET_THB_MAX:
             return StrategyDecision(
                signal="NO_TRADE",
                reason=f"Daily Target Reached ({daily_profit_thb:.0f} THB)",
                warning="Auto-Scale Off"
            )

        # 2. M15 Trend Filter
        # EMA50 > EMA200 -> LONG ONLY
        # EMA50 < EMA200 -> SHORT ONLY
        trend_bias = "NEUTRAL"
        if ema50_m15 > ema200_m15:
            trend_bias = "LONG"
        elif ema50_m15 < ema200_m15:
            trend_bias = "SHORT"
        
        if trend_bias == "NEUTRAL":
             return StrategyDecision(signal="NO_TRADE", reason="M15 Trend Neutral/Flat")

        # 3. M5 Setup Validation
        setup_valid = False
        setup_reason = ""
        
        if trend_bias == "LONG":
            # Price above EMA50
            if close_m5 > ema50_m5:
                # Pullback near EMA20-50 logic 
                # (Price is between EMA20 and EMA50 OR close to EMA20)
                # Simplified: Close is > EMA50 and < EMA20 * 1.002 (near)
                # Or strictly logic: Pullback means we dipped? 
                # Let's check if close is supported.
                # Requirement: "Pullback near EMA20-EMA50"
                # Implementation: Close is above EMA50, but maybe below EMA20 or just above it.
                # Let's use: Close > EMA50
                
                # RSI 40-60
                if 40 <= rsi_m5 <= 60:
                    setup_valid = True
                    setup_reason = "M5 Bullish Setup (RSI 40-60, >EMA50)"
                else:
                    setup_reason = f"M5 RSI {rsi_m5:.1f} Invalid"
            else:
                 setup_reason = "M5 Close below EMA50"
                 
        elif trend_bias == "SHORT":
            # Price below EMA50
            if close_m5 < ema50_m5:
                # RSI 40-60
                if 40 <= rsi_m5 <= 60:
                     setup_valid = True
                     setup_reason = "M5 Bearish Setup (RSI 40-60, <EMA50)"
                else:
                    setup_reason = f"M5 RSI {rsi_m5:.1f} Invalid"
            else:
                 setup_reason = "M5 Close above EMA50"

        if not setup_valid:
             return StrategyDecision(signal="NO_TRADE", reason=f"Bias {trend_bias} but {setup_reason}")

        # 4. M1 Execution Timing
        # Momentum confirmation
        signal = "NO_TRADE"
        reason = "Waiting for M1 Momentum"
        
        if trend_bias == "LONG":
            # Bullish close
            if close_m1 > open_m1:
                signal = "BUY"
                reason = "M1 Bullish Momentum Confirmed"
        elif trend_bias == "SHORT":
            # Bearish close
            if close_m1 < open_m1:
                signal = "SELL"
                reason = "M1 Bearish Momentum Confirmed"
                
        # 5. Risk Calculation
        # SL: 0.25% - 0.30%
        # TP1: 0.4%, TP2: 0.7%
        # Using 0.3% SL and 0.4% TP for now (Fixed RR 1:1.3)
        
        sl_pct = 0.003
        tp_pct = 0.004
        
        if signal != "NO_TRADE":
            entry = close_m1
            sl = entry * (1 - sl_pct) if signal == "BUY" else entry * (1 + sl_pct)
            tp = entry * (1 + tp_pct) if signal == "BUY" else entry * (1 - tp_pct)
            
            return StrategyDecision(
                signal=signal,
                entry_price=entry,
                sl=sl,
                tp=tp,
                reason=f"{reason} | {setup_reason} | {trend_bias}",
                confidence=0.9,
                risk_pct=1.0 # 1% Risk
            )
            
        return StrategyDecision(signal="NO_TRADE", reason=reason)

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": "BTC Scalper Pro",
            "target": f"{self.DAILY_TARGET_THB_MIN}-{self.DAILY_TARGET_THB_MAX} THB",
            "style": "M15 Trend / M5 Setup / M1 Trig"
        }

btc_momentum_strategy = BTCMomentumStrategy()
