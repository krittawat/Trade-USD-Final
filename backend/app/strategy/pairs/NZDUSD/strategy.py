from app.strategy.templates.trend_rider import TrendRiderStrategy
from app.domain.models import SymbolProfile, Decision
from app.domain.enums import RegimeType
import pandas as pd

class NzdUsdTrendStrategy(TrendRiderStrategy):
    """
    NZDUSD Specialized Strategy (Trend Rider Base).
    - Uses H1 Trend Following
    - Wider SL handled by base class logic (FOREX_PREFIXES)
    """
    name = "nzdusd_trend"
    pass
