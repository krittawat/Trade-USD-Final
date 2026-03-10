from app.strategy.templates.usdjpy_elite import UsdjpyEliteStrategy
from app.domain.models import SymbolProfile, Decision
from app.domain.enums import RegimeType
import pandas as pd


class UsdJpyStrategy(UsdjpyEliteStrategy):
    """
    USDJPY Pair Strategy — inherits UsdjpyEliteStrategy.
    - JPY Point/Pip handling via SymbolProfile (point=0.001, digits=3).
    - Elite dual-mode logic handles all market conditions.
    """
    name = "usdjpy_pair"
