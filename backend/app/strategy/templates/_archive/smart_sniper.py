"""
Smart Sniper Strategy — Evolution of Sniper with Forensic Intelligence.

Inherits: SmartStrategy
Regime: Trend Following (Primary), Volatility Breakout (Secondary)
Filters:
    - ADX > 20 (Avoid Choppy Markets)
    - Session Bleed Check (Avoid Bad Sessions)
    - Dynamic Confidence based on Confluence & Audit Win Rate
"""

import pandas as pd
import app.analysis.indicators as ind

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.smart_base import SmartStrategy
from app.strategy.templates.sniper import SniperStrategy

logger = get_logger(__name__)

class SmartSniper(SmartStrategy, SniperStrategy):
    """
    Smart Sniper — แม่นยำกว่า ปลอดภัยกว่า ด้วย Data-Driven Filters.
    """
    
    name = "smart_sniper"
    timeframe = "M15"
    suitable_regimes = [RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN]
    
    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        session: str = "UNKNOWN",
        h1_candles: pd.DataFrame = None,
    ) -> Decision:
        
        # 1. Smart Gate: Check Session Safety First
        if not self.is_safe_session(session):
            return self.create_hold(
                symbol=profile.symbol,
                reason=f"Unsafe Session ({session}) based on Audit Bleed"
            )

        # 2. Smart Gate: Check Regime Metrics (ADX)
        metrics = self.get_regime_metrics(candles)
        adx = metrics.get("adx", 0.0)
        
        # ADX Filter: ต่ำกว่า 20 ถือว่า Sideways น่าเบื่อ -> ไม่เทรด (ยกเว้นมี Signal แรงจริงๆ อาจจะ bypass ได้ในอนาคต)
        if adx < 20.0:
            return self.create_hold(
                profile.symbol,
                reason=f"Low Volatility (ADX={adx:.1f} < 20)"
            )
            
        # 3. Use Logic from Original Sniper (Parent Class)
        # เราเรียก analyze ของ SniperStrategy (ซึ่งเป็น Parent ผ่าน MRO หรือเรียกตรง)
        # แต่ SniperStrategy.analyze ไม่ได้รับ h1_candles หรือ session ใน signature เดิม
        # ต้องระวังเรื่อง MRO. SmartSniper สืบทอด SmartStrategy, SniperStrategy.
        # SniperStrategy inherit BaseStrategy.
        
        # Call Sniper logic
        decision = SniperStrategy.analyze(self, candles, profile, regime)
        
        if decision.action == Action.HOLD:
            return decision

        # 4. Enhance Decision with Smart Factors
        
        # 4.1 Audit Win Rate Boost
        # ถ้า Symbol นี้ Win Rate ในอดีตดี -> เพิ่ม confidence
        audit = self.get_audit_feedback(profile.symbol)
        # สมมติ audit.summary มี win_rate (ระดับ Global หรือ Symbol ถ้าทำละเอียด)
        summary = audit.get("summary", {})
        win_rate = summary.get("win_rate", 0.0)
        
        if win_rate > 60.0:
            decision.confidence += 0.1
            decision.reason += f"; High Historical WinRate ({win_rate}%)"
            
        # 4.2 Multi-Timeframe Confirmation (Optional, if H1 provided)
        if h1_candles is not None and not h1_candles.empty:
            h1_ema200 = ta.ema(h1_candles["close"], length=200).iloc[-1]
            h1_close = h1_candles["close"].iloc[-1]
            
            h1_bullish = h1_close > h1_ema200
            
            if decision.action == Action.BUY and h1_bullish:
                decision.confidence += 0.1
                decision.reason += "; H1 Trend Aligned"
            elif decision.action == Action.SELL and not h1_bullish:
                decision.confidence += 0.1
                decision.reason += "; H1 Trend Aligned"
            else:
                # Conflicting timeframes -> reduce confidence or block
                decision.confidence -= 0.1
                decision.reason += "; H1 Trend Conflict"

        # Cap confidence
        decision.confidence = min(1.0, max(0.0, decision.confidence))
        
        # 5. Final Safety Check
        if decision.confidence < 0.6: # Smart Sniper ต้องการความชัวร์สูงกว่าเดิม (0.58 -> 0.6)
            return self.create_hold(
                profile.symbol,
                reason=f"Confidence too low after Smart Filters ({decision.confidence:.2f} < 0.6)"
            )

        return decision
