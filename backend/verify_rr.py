"""Verify Forex-specific SL multipliers."""
from app.strategy.templates.scalping import _get_sl_rr

tests = [
    ("EURUSDc", 3.0, 2.0),
    ("GBPUSDc", 3.0, 2.0),
    ("USDJPYc", 3.0, 2.0),
    ("AUDUSDc", 3.0, 2.0),
    ("USDCADc", 3.0, 2.0),
    ("NZDUSDc", 3.0, 2.0),
    ("BTCUSDc", 2.5, 2.0),
    ("XAUUSDc", 2.0, 2.0),
    ("XAGUSDc", 2.0, 2.0),
]

all_ok = True
for sym, expected_sl, expected_rr in tests:
    sl, rr = _get_sl_rr(sym)
    ok = sl == expected_sl and rr == expected_rr
    status = "OK" if ok else "FAIL"
    print(f"  {status} {sym:12s} SL={sl} RR={rr}")
    if not ok:
        all_ok = False
        print(f"       Expected SL={expected_sl} RR={expected_rr}")

print()
if all_ok:
    print("ALL PASSED - Per-asset SL multipliers correct")
else:
    print("SOME FAILED")
