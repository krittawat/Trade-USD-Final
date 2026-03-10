"""
Currency Adapter — Handles conversion between USD and Account Currency (e.g. USC).

Acts as a single source of truth for:
1. Money Normalization (Account Currency -> USD)
2. Display Formatting (USD -> Account Currency)
3. Lot Size Conversion (Standard -> Cent)
4. Symbol Mapping (e.g. XAUUSD -> XAUUSDc)

Design:
    - Internal logic always uses USD & Standard Lots.
    - Conversion happens only at I/O boundaries (MT5, API, Logs).
"""

from typing import Literal, Optional
from app.core.config import Settings
from app.core.mode_resolver import RuntimeMode, ModeResolver

class CurrencyAdapter:
    """
    Adapter for handling currency and lot conversions.
    """

    def __init__(self, settings: Settings, mode: Optional[RuntimeMode] = None):
        self.settings = settings
        # Default mode if not injected (safe fallback)
        self.mode = mode or RuntimeMode(
            account_currency="USD",
            lot_mode="STANDARD",
            symbol_suffix="",
            cent_multiplier=1.0
        )
        # ─── SAFETY: Ensure cent_multiplier consistency ───
        if self.mode.lot_mode == "CENT" and self.mode.cent_multiplier != 100.0:
            self.mode.cent_multiplier = 100.0

    def update_mode(self, mode: RuntimeMode) -> None:
        """Update runtime mode after auto-detection."""
        self.mode = mode
        # ─── SAFETY: Ensure cent_multiplier consistency ───
        if self.mode.lot_mode == "CENT" and self.mode.cent_multiplier != 100.0:
            self.mode.cent_multiplier = 100.0

    @property
    def is_cent(self) -> bool:
        return self.mode.account_currency == "USC"

    @property
    def is_cent_lot(self) -> bool:
        return self.mode.lot_mode == "CENT"

    # ─── Money Conversions ───

    def usd_to_account(self, usd_amount: float) -> float:
        """Convert USD amount to Account Currency (e.g. 1 USD -> 100 USC)."""
        return usd_amount * self.mode.cent_multiplier

    def account_to_usd(self, account_amount: float) -> float:
        """Convert Account Currency to USD (e.g. 100 USC -> 1 USD)."""
        if self.mode.cent_multiplier == 0:
            return 0.0
        return account_amount / self.mode.cent_multiplier

    def normalize_money(self, amount: float) -> float:
        """Alias for account_to_usd — normalizes raw MT5 money to USD."""
        return self.account_to_usd(amount)

    # ─── Lot Conversions ───

    # ─── PER-SYMBOL BROKER LOT CEILINGS ───
    # User mandate: strict max lots on MT5 terminal per symbol
    SYMBOL_LOT_CAPS = {
        "XAUUSD": 3.0,   # Gold
        "XAGUSD": 1.0,   # Silver
        "BTCUSD": 1.0,   # BTC
        "US30": 0.5,     # Indices (High Margin)
        "USTEC": 0.5,    # Tech (High Margin)
        "USOIL": 1.0,    # Oil
    }
    DEFAULT_BROKER_LOT_CAP = 5.0  # Fallback for unlisted symbols

    def _get_broker_lot_cap(self, symbol: str = "") -> float:
        """Get the per-symbol broker lot cap for the MT5 terminal."""
        sym_upper = symbol.upper()
        # Strip trailing 'C' suffix (e.g. XAUUSDc -> XAUUSD) but NOT mid-word C (e.g. BTCUSD)
        if sym_upper.endswith("C") and not sym_upper.endswith("USDC"):
            sym_upper = sym_upper[:-1]
        for key, cap in self.SYMBOL_LOT_CAPS.items():
            if key in sym_upper:
                return cap
        return self.DEFAULT_BROKER_LOT_CAP

    def to_broker_lots(self, standard_lots: float, symbol: str = "") -> float:
        """
        Convert Standard Lots (internal) to Broker Lots.
        
        Example (Cent Mode):
            0.01 Standard -> 1.00 Cent Lot
        
        SAFETY: Output is capped per-symbol (Gold=3.0, Silver=2.0, BTC=1.0).
        """
        if self.is_cent_lot:
            broker_lots = standard_lots * 100.0
        else:
            broker_lots = standard_lots
        
        # ─── FINAL SAFETY NET: Per-symbol cap ───
        cap = self._get_broker_lot_cap(symbol)
        if broker_lots > cap:
            broker_lots = cap
        return broker_lots

    def from_broker_lots(self, broker_lots: float) -> float:
        """
        Convert Broker Lots to Standard Lots (internal).
        
        Example (Cent Mode):
            1.00 Cent Lot -> 0.01 Standard
        """
        if self.is_cent_lot:
            return broker_lots / 100.0
        return broker_lots

    def get_max_lot_cap(self, symbol: str = "") -> float:
        """
        Get max lot size allowed in STANDARD lots (internal).
        Per-symbol caps (Cent Mode):
            Gold:   0.03 Standard = 3.0 Cent Lots
            Silver: 0.02 Standard = 2.0 Cent Lots
            BTC:    0.01 Standard = 1.0 Cent Lot
        """
        broker_cap = self._get_broker_lot_cap(symbol)
        if self.is_cent_lot:
            return broker_cap / 100.0  # Convert broker lots -> standard lots
        return broker_cap

    def get_min_lot(self) -> float:
        """Get min lot size allowed (internal standard lots)."""
        # In Cent mode: min broker lot is 0.01 Cent => 0.0001 Standard
        # In Std mode: min broker lot is 0.01 Std
        return 0.0001 if self.is_cent_lot else 0.01

    def map_symbol(self, symbol: str) -> str:
        """
        Map internal symbol to broker symbol.
        e.g. XAUUSD -> XAUUSDc (if cent mode)
        Prevents appending duplicate suffix (e.g. BTCUSDc -> BTCUSDCc)
        """
        suffix = self.mode.symbol_suffix
        if suffix and not symbol.lower().endswith(suffix.lower()):
            # Edge case handling for BTCUSDC vs BTCUSDCc if needed
            if symbol.endswith("USDC") and suffix == "c":
                return f"{symbol}{suffix}"
            return f"{symbol}{suffix}"
        return symbol

    def unmap_symbol(self, symbol: str) -> str:
        """
        Map broker symbol to internal symbol.
        e.g. XAUUSDc -> XAUUSD
        """
        suffix = self.mode.symbol_suffix
        if suffix and symbol.lower().endswith(suffix.lower()):
            # Ensure we don't accidentally chop off USDC if it's the actual asset
            if symbol.endswith("USDC") and suffix == "c":
                return symbol
            return symbol[:-len(suffix)]
        return symbol

    # ─── Display Helpers ───

    def format_money(self, usd_amount: float, use_symbol: bool = True) -> str:
        """
        Format USD amount for display in Account Currency.
        e.g. 5 USD -> "500 USC" or "500.00"
        """
        amount = self.usd_to_account(usd_amount)
        suffix = f" {self.mode.account_currency}" if use_symbol else ""
        return f"{amount:,.2f}{suffix}"


# Global instance cache
_adapter_instance = None

def get_adapter(settings: Settings) -> CurrencyAdapter:
    """Get or create singleton CurrencyAdapter instance."""
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = CurrencyAdapter(settings)
    return _adapter_instance
