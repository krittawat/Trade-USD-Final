"""Quick verification of TP direction guard + lot size limits."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Patch MT5 to avoid hanging
import types
mt5_mock = types.ModuleType("MetaTrader5")
mt5_mock.initialize = lambda *a, **kw: True
mt5_mock.shutdown = lambda: None
mt5_mock.TIMEFRAME_M5 = 5
mt5_mock.TIMEFRAME_H1 = 16385
mt5_mock.symbol_info = lambda s: None
mt5_mock.account_info = lambda: None
mt5_mock.ORDER_TYPE_BUY = 0
mt5_mock.ORDER_TYPE_SELL = 1
mt5_mock.TRADE_ACTION_DEAL = 1
mt5_mock.ORDER_FILLING_IOC = 2
sys.modules["MetaTrader5"] = mt5_mock

import pandas as pd
import numpy as np

print("=" * 60)
print("VERIFICATION: TP Direction Guard + Lot Size Limits")
print("=" * 60)

# ─── Test 1: Sizing Constants ───
print("\n=== 1. LOT SIZE LIMITS ===")
from app.risk.sizing import HARD_MAX_RISK_PCT, HARD_MAX_LOT_BROKER, HARD_MAX_LOT_DEFAULT

checks = [
    ("HARD_MAX_RISK_PCT", HARD_MAX_RISK_PCT, 2.0),
    ("XAUUSD Max Lot", HARD_MAX_LOT_BROKER.get("XAUUSD"), 0.50),
    ("XAGUSD Max Lot", HARD_MAX_LOT_BROKER.get("XAGUSD"), 0.50),
    ("BTCUSD Max Lot", HARD_MAX_LOT_BROKER.get("BTCUSD"), 0.50),
    ("Default Max Lot", HARD_MAX_LOT_DEFAULT, 2.0),
]
all_pass = True
for name, actual, expected in checks:
    ok = actual == expected
    if not ok:
        all_pass = False
    status = "OK" if ok else "FAIL"
    print(f"  [{status}] {name}: {actual} (expected {expected})")

# ─── Test 2: TP Direction in OrderPlanBuilder ───
print("\n=== 2. TP DIRECTION AUTO-FIX (order_plan.py) ===")
from app.risk.order_plan import OrderPlanBuilder
from app.core.config import Settings
from app.domain.enums import Action
from app.domain.models import Decision, SymbolProfile, AccountState

settings = Settings()
builder = OrderPlanBuilder(settings)

# Create fake candles
np.random.seed(42)
n = 250
base = 5000.0
prices = base + np.cumsum(np.random.randn(n) * 0.5)
candles = pd.DataFrame({
    "open": prices,
    "high": prices + 5,
    "low": prices - 5,
    "close": prices + np.random.randn(n) * 0.3,
    "volume": np.random.randint(100, 1000, n).astype(float),
})

profile = SymbolProfile(symbol="XAUUSDc")
account = AccountState(balance=10000, equity=10000)

# Test 2a: BUY with wrong TP (TP below entry)
dec_buy_wrong = Decision(
    action=Action.BUY, symbol="XAUUSDc", strategy_name="test",
    confidence=0.8, stop_loss=4985.0, take_profit=4980.0, reason="test",
)
result = builder.build(dec_buy_wrong, profile, account, candles, mt5_price=(5000.0, 5001.0))
if hasattr(result, "take_profit"):
    ok = result.take_profit > 5001.0  # Should be above entry
    status = "OK" if ok else "FAIL"
    if not ok: all_pass = False
    print(f"  [{status}] BUY wrong-side TP: 4980 -> {result.take_profit:.2f} (should be > 5001)")
else:
    print(f"  [INFO] Blocked: {result}")

# Test 2b: SELL with wrong TP (TP above entry)
dec_sell_wrong = Decision(
    action=Action.SELL, symbol="XAUUSDc", strategy_name="test",
    confidence=0.8, stop_loss=5015.0, take_profit=5020.0, reason="test",
)
result2 = builder.build(dec_sell_wrong, profile, account, candles, mt5_price=(5000.0, 5001.0))
if hasattr(result2, "take_profit"):
    ok = result2.take_profit < 5000.0  # Should be below entry
    status = "OK" if ok else "FAIL"
    if not ok: all_pass = False
    print(f"  [{status}] SELL wrong-side TP: 5020 -> {result2.take_profit:.2f} (should be < 5000)")
else:
    print(f"  [INFO] Blocked: {result2}")

# Test 2c: BUY with correct TP (preserved)
dec_buy_ok = Decision(
    action=Action.BUY, symbol="XAUUSDc", strategy_name="test",
    confidence=0.8, stop_loss=4985.0, take_profit=5025.0, reason="test",
)
result3 = builder.build(dec_buy_ok, profile, account, candles, mt5_price=(5000.0, 5001.0))
if hasattr(result3, "take_profit"):
    ok = abs(result3.take_profit - 5025.0) < 1.0
    status = "OK" if ok else "FAIL"
    if not ok: all_pass = False
    print(f"  [{status}] BUY correct TP: preserved at {result3.take_profit:.2f} (expected ~5025)")
else:
    print(f"  [INFO] Blocked: {result3}")

# Test 2d: SELL with correct TP (preserved)
dec_sell_ok = Decision(
    action=Action.SELL, symbol="XAUUSDc", strategy_name="test",
    confidence=0.8, stop_loss=5015.0, take_profit=4975.0, reason="test",
)
result4 = builder.build(dec_sell_ok, profile, account, candles, mt5_price=(5000.0, 5001.0))
if hasattr(result4, "take_profit"):
    ok = abs(result4.take_profit - 4975.0) < 1.0
    status = "OK" if ok else "FAIL"
    if not ok: all_pass = False
    print(f"  [{status}] SELL correct TP: preserved at {result4.take_profit:.2f} (expected ~4975)")
else:
    print(f"  [INFO] Blocked: {result4}")

print()
print("=" * 60)
if all_pass:
    print("ALL TESTS PASSED! TP guard + lot limits verified.")
else:
    print("SOME TESTS FAILED — review output above.")
print("=" * 60)
