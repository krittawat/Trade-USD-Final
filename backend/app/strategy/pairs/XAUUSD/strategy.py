from app.strategy.templates import gold_scalp_pro as gold_scalp_pro_template
from app.domain.models import SymbolProfile, Decision
from app.domain.enums import RegimeType
import pandas as pd

class XauUsdProStrategy(gold_scalp_pro_template.GoldScalpProStrategy):
    """
    XAUUSD Specialized Strategy (Gold Scalp Pro Wrapper).
    - Unchanged "God-Tier" logic
    - Explicitly mapped to XAUUSD
    """
    name = "xauusd_pro"

    def __init__(self):
        super().__init__()
        self.name = "xauusd_pro"
