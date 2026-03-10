"""
Antigravity Fusion - Hybrid Strategy V1
Combines High-Frequency Scalping (Dual Scalp) with Precision Trend Following (Sniper Pro).
"""
import pandas as pd
from typing import Dict, Optional, List
from .sniper_pro import SniperProStrategy, Signal as SniperSignalEnum
from .dual_scalp import DualScalpStrategy, Signal as ScalpSignalEnum
from dataclasses import dataclass

@dataclass
class FusionSignal:
    signal: str
    confidence: float
    method: str
    sl: float
    tp: float
    reasons: List[str]

class AntigravityFusionStrategy:
    def __init__(self):
        self.sniper = SniperProStrategy()
        self.scalp = DualScalpStrategy()

    def analyze(self, df: pd.DataFrame, direction: str = "AUTO") -> FusionSignal:
        # Get signals from both strategies
        sniper_sig = self.sniper.analyze(df, direction)
        scalp_sig = self.scalp.analyze(df, direction)
        
        reasons = []
        
        # 1. PRIORITY: Sniper Pro (Trend Entry)
        if sniper_sig.signal.name in ["BUY", "SELL"]:
            reasons.append(f"🔥 Sniper Pro Signal: {sniper_sig.signal.name}")
            reasons.extend(sniper_sig.reasons)
            return FusionSignal(
                signal=sniper_sig.signal.name,
                confidence=sniper_sig.confidence,
                method=f"FUSION_SNIPER_{sniper_sig.entry_method}",
                sl=sniper_sig.stop_loss,
                tp=sniper_sig.take_profit,
                reasons=reasons
            )
            
        # 2. SECONDARY: Dual Scalp (High Frequency)
        if scalp_sig.signal.name in ["BUY", "SELL"]:
            reasons.append(f"⚡ Scalp Signal: {scalp_sig.signal.name}")
            reasons.extend(scalp_sig.reasons)
            return FusionSignal(
                signal=scalp_sig.signal.name,
                confidence=scalp_sig.confidence,
                method=f"FUSION_SCALP_{scalp_sig.entry_method}",
                sl=scalp_sig.stop_loss,
                tp=scalp_sig.take_profit,
                reasons=reasons
            )
            
        return FusionSignal(
            signal="NONE",
            confidence=0,
            method="NONE",
            sl=0,
            tp=0,
            reasons=["Scanning for Hybrid opportunities..."]
        )

antigravity_fusion = AntigravityFusionStrategy()
