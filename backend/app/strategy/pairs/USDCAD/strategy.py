from app.strategy.templates.forex_precision import ForexPrecisionStrategy
from app.domain.models import SymbolProfile, Decision
from app.domain.enums import RegimeType
import pandas as pd

class UsdCadPrecisionStrategy(ForexPrecisionStrategy):
    """
    USDCAD Specialized Strategy (Precision Base).
    - Tight SL (2.0 ATR)
    - High RR (3.0)
    - Strict EMA alignment
    """
    name = "usdcad_precision"

    def analyze(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
    ) -> Decision:
        # Enforce USDCAD specific parameters
        return super().analyze(
            candles, 
            profile, 
            regime, 
            sl_atr_mult=2.0,  # Tight SL for stable pair
            rr_target=3.0     # High Reward targeting
        )
