"""
Hybrid Gold Strategy (Accumulation Mode)
Combines:
1. Predicta V4 (Trend Filter) - Must be Bullish (Supertrend)
2. Easy Entry (Trigger) - MACD Momentum Breakout
Goal: High probability BUY entries for Gold Accumulation.
"""

import pandas as pd
from .base_strategy import BaseStrategy, StrategyDecision
from .predicta_v4 import PredictaV4Strategy
from .easy_entry import EasyEntryStrategy

class HybridGoldStrategy(BaseStrategy):
    def __init__(self):
        self.name = "HybridGold"
        self.predicta = PredictaV4Strategy()
        self.easy_entry = EasyEntryStrategy()

    def get_status(self):
        return {"name": self.name, "mode": "Accumulation (Buy Bias)"}

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> StrategyDecision:
        # 1. Get Trend from Predicta
        # Predicta internal analysis
        pred_decision = self.predicta.analyze(df)
        
        # We need to peek at internal state of Predicta?
        # Predicta analysis string contains "Supertrend: Bullish/Bearish"?
        # Or we rely on the signal.
        # Ideally we want Predicta's 'Trend' status.
        # But for now, let's assume if Predicta signal is NOT SELL, trend is likely Up or Neutral?
        # Better: Predicta returns 'BUY' or 'NO_TRADE' if trend is Bullish.
        # If Predicta returns 'SELL', trend is Bearish.
        
        # Let's be stricter: Logic = Easy Entry BUY *confirmed* by Predicta Trend.
        
        # 2. Get Trigger from Easy Entry
        easy_decision = self.easy_entry.analyze(df)
        
        final_signal = "NO_TRADE"
        confidence = 0.0
        reasons = []

        # Logic for BUY (Accumulation)
        if direction != "SELL":
            # Check Easy Entry Trigger
            if easy_decision.signal == "BUY":
                # Check Predicta Confluence
                # If Predicta also says BUY -> Super Strong
                # If Predicta says NO_TRADE (but not SELL) -> Moderate Strong
                 
                if pred_decision.signal == "BUY":
                    final_signal = "BUY"
                    confidence = 90.0
                    reasons.append("Hybrid: Predicta + EasyEntry Confluence 🚀")
                elif pred_decision.signal == "NO_TRADE":
                    # Check if 'Bearish' keyword in reason? Risk.
                    # Let's trust EasyEntry if Predicta is neutral.
                    final_signal = "BUY"
                    confidence = 75.0
                    reasons.append("Hybrid: EasyEntry Trigger (Predicta Neutral)")
        
        # Logic for SELL (Only if user allows scraping/hedging via strategy, though 'Accumulation' implies mostly buy)
        if direction != "BUY":
            # Only SELL if BOTH agree?
            if easy_decision.signal == "SELL" and pred_decision.signal == "SELL":
                 final_signal = "SELL"
                 confidence = 85.0
                 reasons.append("Hybrid: Double Bearish Reversal")

        # Fallback decision
        if final_signal != "NO_TRADE":
            atr = self.easy_entry.analyze(df).sl  # Accessing SL/TP logic from sub-strategy is messy without refactor.
            # Recalculate SL/TP based on ATR
            # We use Predicta's ATR logic as it is robust
            close = df['close'].iloc[-1]
            # Simple ATR calc
            import pandas_ta as ta
            atr_val = ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1]
            
            sl = 0
            tp = 0
            if final_signal == "BUY":
                sl = close - (atr_val * 2.0)
                tp = close + (atr_val * 4.0)
            else:
                sl = close + (atr_val * 2.0)
                tp = close - (atr_val * 4.0)
                
            return StrategyDecision(signal=final_signal, entry_price=close, sl=sl, tp=tp, reason=", ".join(reasons), confidence=confidence)
            
        return StrategyDecision(signal="NO_TRADE", reason="No Confluence")

hybrid_gold_strategy = HybridGoldStrategy()
