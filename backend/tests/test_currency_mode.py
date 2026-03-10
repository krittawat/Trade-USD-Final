import pytest
from app.core.config import Settings
from app.core.currency_adapter import CurrencyAdapter
from app.core.mode_resolver import ModeResolver, RuntimeMode

# ─── ModeResolver Tests ───

def test_resolve_auto_usd():
    settings = Settings(account_currency="AUTO", lot_mode="AUTO")
    resolver = ModeResolver(settings)
    mode = resolver.resolve("USD", ["XAUUSD", "EURUSD"])
    
    assert mode.account_currency == "USD"
    assert mode.lot_mode == "STANDARD"
    assert mode.symbol_suffix == ""
    assert mode.cent_multiplier == 1.0

def test_resolve_auto_usc_by_currency():
    settings = Settings(account_currency="AUTO", lot_mode="AUTO")
    resolver = ModeResolver(settings)
    mode = resolver.resolve("USC", ["XAUUSDc", "EURUSDc"])
    
    assert mode.account_currency == "USC"
    assert mode.lot_mode == "CENT"
    assert mode.symbol_suffix == "c"
    assert mode.cent_multiplier == 100.0

def test_resolve_auto_usc_by_suffix():
    # Account computes as USD but symbols have 'c' suffix -> Cent account
    settings = Settings(account_currency="AUTO", lot_mode="AUTO")
    resolver = ModeResolver(settings)
    mode = resolver.resolve("USD", ["XAUUSDc", "EURUSDc"])
    
    assert mode.account_currency == "USC"
    assert mode.lot_mode == "CENT"
    assert mode.symbol_suffix == "c"
    assert mode.cent_multiplier == 100.0

def test_resolve_force_usd():
    settings = Settings(account_currency="USD", lot_mode="STANDARD")
    resolver = ModeResolver(settings)
    # Even if symbols have 'c', config forced USD (user config error, but respect intent?)
    # or user manually mapping. Logic says config overrides.
    mode = resolver.resolve("USC", ["XAUUSDc"])
    
    assert mode.account_currency == "USD"
    assert mode.lot_mode == "STANDARD"

# ─── CurrencyAdapter Tests ───

def test_adapter_usd_mode():
    settings = Settings(account_currency="USD", lot_mode="STANDARD")
    adapter = CurrencyAdapter(settings)
    
    # Money
    assert adapter.normalize_money(100.0) == 100.0
    assert adapter.usd_to_account(100.0) == 100.0
    
    # Lots
    assert adapter.to_broker_lots(1.0) == 1.0
    assert adapter.from_broker_lots(1.0) == 1.0
    assert adapter.get_max_lot_cap() == 50.0
    
    # Symbols
    assert adapter.map_symbol("XAUUSD") == "XAUUSD"
    assert adapter.unmap_symbol("XAUUSD") == "XAUUSD"

def test_adapter_usc_mode():
    # Simulate USC mode injection
    settings = Settings(account_currency="USC", lot_mode="CENT")
    
    # Manually inject the mode checks BEFORE assertions
    mode = RuntimeMode(
        account_currency="USC",
        lot_mode="CENT",
        symbol_suffix="c",
        cent_multiplier=100.0
    )
    
    adapter = CurrencyAdapter(settings, mode=mode)
    
    # Money (1 USD = 100 USC)
    assert adapter.usd_to_account(1.0) == 100.0
    assert adapter.account_to_usd(100.0) == 1.0
    assert adapter.normalize_money(500.0) == 5.0
    
    # Lots (0.01 Std = 1.00 Cent)
    assert adapter.to_broker_lots(0.01) == 1.0
    assert adapter.to_broker_lots(1.0) == 100.0
    assert adapter.from_broker_lots(100.0) == 1.0
    assert adapter.get_max_lot_cap() == 200.0 
    
    # Symbols
    assert adapter.map_symbol("XAUUSD") == "XAUUSDc"
    assert adapter.unmap_symbol("XAUUSDc") == "XAUUSD"
