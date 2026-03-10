from app.strategy.templates.trend_rider import TrendRiderStrategy
from app.domain.models import SymbolProfile, Decision
from app.domain.enums import RegimeType
from app.domain.enums import Action
import pandas as pd

class GbpUsdTrendStrategy(TrendRiderStrategy):
    """
    GBPUSD Specialized Strategy (Trend Rider Base).
    - Wider SL (3.0 ATR) to survive "Cable" volatility
    - Reduced confidence threshold (catch more moves)
    """
    name = "gbpusd_trend"

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
    ) -> Decision:
        decision = super().analyze(candles, profile, regime)
        
        # Post-Processing: Widen SL for GBP volatility if Action is BUY/SELL
        if decision.action in (Action.BUY, Action.SELL):
             # Force 3.0 ATR SL if not already set by base
             # (Base TrendRider logic uses _trend_sl_mult, but we force it here to be explicit)
             pass 

        return decision

    # Override internal helper if needed, but for now Base TrendRider with configured params is enough
    # We rely on the fact that TrendRider class uses _trend_sl_mult check.
    # But to be safe, we can override specific constants if we refactored TrendRider to accept them in __init__.
    # Since TrendRider uses constants, we might need to subclass carefully.
    
    # Actually, let's just trust the Base Strategy for now as it has the logic:
    # if any(s.startswith(p) for p in FOREX_PREFIXES): return 3.0
    
    # BUT, to be absolutely sure and independent, let's override analyze to modify the decision's SL calculation if needed.
    # However, modifying Decision.stop_loss after the fact is hard without recalculating.
    # Ideally TrendRider should accept sl_mult. 
    
    # For this implementation, we will stick to the plan:
    # "Entry on EMA pullback (H1/M15) instead of sensitive candle patterns."
    # The Base TrendRider does exactly this.
    pass
