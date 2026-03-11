import json
from pathlib import Path

from backend.trader.config.paths import SETTINGS_PATH

class SymbolMapper:
    """
    Handles translation between standard symbols (XAUUSD) and broker-specific symbols (XAUUSDc).
    """
    def __init__(self, config_path: str | Path = SETTINGS_PATH):
        with open(Path(config_path), "r", encoding="utf-8") as f:
            self.config = json.load(f)
        self.mapping = self.config.get("symbols", {})
        # Reverse mapping for translating broker symbols back to canonical standard symbols.
        # Prefer non-self aliases like "USOIL" -> "USOILm" over passthrough aliases
        # like "USOILm" -> "USOILm" so risk/settings lookups stay canonical.
        self.reverse_mapping = {}
        for standard_symbol, broker_symbol in self.mapping.items():
            if broker_symbol not in self.reverse_mapping or self.reverse_mapping[broker_symbol] == broker_symbol:
                self.reverse_mapping[broker_symbol] = standard_symbol

    def to_broker(self, standard_symbol: str) -> str:
        """Translates standard symbol to broker symbol (e.g., XAUUSD -> XAUUSDc)."""
        return self.mapping.get(standard_symbol, standard_symbol)

    def to_standard(self, broker_symbol: str) -> str:
        """Translates broker symbol back to standard symbol (e.g., XAUUSDc -> XAUUSD)."""
        return self.reverse_mapping.get(broker_symbol, broker_symbol)

# Singleton instance
mapper = SymbolMapper()
