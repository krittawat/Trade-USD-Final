import json
from pathlib import Path

class SymbolMapper:
    """
    Handles translation between standard symbols (XAUUSD) and broker-specific symbols (XAUUSDc).
    """
    def __init__(self, config_path: str = "d:/VibeCode/Trade/backend/trader/config/settings.json"):
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        self.mapping = self.config.get("symbols", {})
        # Reverse mapping for translating broker symbols back to standard
        self.reverse_mapping = {v: k for k, v in self.mapping.items()}

    def to_broker(self, standard_symbol: str) -> str:
        """Translates standard symbol to broker symbol (e.g., XAUUSD -> XAUUSDc)."""
        return self.mapping.get(standard_symbol, standard_symbol)

    def to_standard(self, broker_symbol: str) -> str:
        """Translates broker symbol back to standard symbol (e.g., XAUUSDc -> XAUUSD)."""
        return self.reverse_mapping.get(broker_symbol, broker_symbol)

# Singleton instance
mapper = SymbolMapper()
