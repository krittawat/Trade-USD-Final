"""
Mode Resolver — Auto-detects Trading Mode (USD vs USC).

Logic:
1. Checks config.MODE_ACCOUNT_CURRENCY:
   - "USD" -> Force USD mode.
   - "USC" -> Force USC mode.
   - "AUTO" -> Detect from MT5 account info and symbol list.

2. Auto-Detection (if AUTO):
   - If account.currency == "USC" -> USC mode.
   - If account.currency == "USD":
     - Check if "XAUUSDc" or "EURUSDc" exists in symbols -> USC mode (Cent account with USD base).
     - Else -> USD mode.

3. Suffix Resolution:
   - USD mode -> suffix = "" (usually).
   - USC mode -> suffix = "c" (usually).
"""

from dataclasses import dataclass
from typing import List, Optional
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

@dataclass
class RuntimeMode:
    account_currency: str  # "USD" or "USC"
    lot_mode: str          # "STANDARD" or "CENT"
    symbol_suffix: str     # "" or "c"
    cent_multiplier: float # 100.0 if USC, 1.0 if USD

class ModeResolver:
    """
    Resolves the operative currency and lot modes based on config and MT5 environment.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def resolve(self, mt5_account_currency: str, available_symbols: List[str]) -> RuntimeMode:
        """
        Determine the runtime mode.
        
        Args:
            mt5_account_currency: Currency string from MT5 (e.g., "USD", "USC", "EUR").
            available_symbols: List of visible symbols in Market Watch.
            
        Returns:
            RuntimeMode object with resolved settings.
        """
        config_mode = self.settings.account_currency.upper()
        config_lot = self.settings.lot_mode.upper()

        # 1. Resolve Account Currency
        resolved_currency = "USD"
        
        if config_mode in ["USD", "USC"]:
            resolved_currency = config_mode
        else: # AUTO
            # Auto-detect
            if mt5_account_currency == "USC":
                resolved_currency = "USC"
            elif mt5_account_currency == "USD":
                # Check for Cent suffix indicators if currency is USD (some cent accounts use USD but cent symbols)
                # Look for common cent suffixes
                has_cent_suffix = any(s.endswith("c") for s in available_symbols if "XAUUSD" in s or "EURUSD" in s)
                if has_cent_suffix:
                    resolved_currency = "USC"
                    logger.info("mode_resolver_auto_detected_usc_by_suffix", extra={"symbols_sample": available_symbols[:5]})
                else:
                    resolved_currency = "USD"
            else:
                # Fallback for other currencies (EUR, etc.) - assume Standard for now unless configured
                resolved_currency = "USD" # Default normalization target
                logger.warning("mode_resolver_unknown_currency_default_usd", extra={"mt5_currency": mt5_account_currency})

        # 2. Resolve Lot Mode
        # If config is AUTO, match Account Currency
        if config_lot == "AUTO":
             resolved_lot = "CENT" if resolved_currency == "USC" else "STANDARD"
        else:
             resolved_lot = config_lot

        # 3. Resolve Suffix
        # Prefer "m" or "c" based on what symbols are actually available in Market Watch
        detected_suffix = ""
        for s in available_symbols:
            if "XAUUSD" in s:
                if s.endswith("m"):
                    detected_suffix = "m"
                    break
                elif s.endswith("c"):
                    detected_suffix = "c"
                    break
        
        resolved_suffix = detected_suffix or ("c" if resolved_currency == "USC" else "")
        
        # 4. Multiplier — SAFETY: lot_mode=CENT MUST always pair with cent_multiplier=100.0
        #    Bug Fix: If lot_mode is CENT but currency resolved to USD, force USC + 100.0
        if resolved_lot == "CENT" and resolved_currency != "USC":
            logger.warning("mode_resolver_cent_currency_mismatch", extra={
                "config_currency": config_mode,
                "resolved_currency": resolved_currency,
                "lot_mode": resolved_lot,
                "fix": "Forcing currency=USC, multiplier=100.0 for CENT lot mode",
            })
            resolved_currency = "USC"
            if not resolved_suffix:
                resolved_suffix = "c"

        multiplier = 100.0 if resolved_lot == "CENT" else 1.0

        mode = RuntimeMode(
            account_currency=resolved_currency,
            lot_mode=resolved_lot,
            symbol_suffix=resolved_suffix,
            cent_multiplier=multiplier
        )

        logger.info("mode_resolved", extra={
            "config_currency": config_mode,
            "mt5_currency": mt5_account_currency,
            "resolved": mode.__dict__
        })

        return mode
