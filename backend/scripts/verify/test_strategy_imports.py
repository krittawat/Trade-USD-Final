"""Strategy Import Smoke Test — verifies all 18 kept strategies can import."""
import sys, traceback, importlib

strategies = [
    "app.strategy.templates.base_strategy",
    "app.strategy.templates.antigravity",
    "app.strategy.templates.gold_scalp_pro",
    "app.strategy.templates.sniper_pro",
    "app.strategy.templates.institutional_scalp",
    "app.strategy.templates.kill_zone_strategy",
    "app.strategy.templates.asian_range_breakout",
    "app.strategy.templates.vfinal_strategy",
    "app.strategy.templates.predicta_strategy",
    "app.strategy.templates.smart_fusion",
    "app.strategy.templates.btc_ultimate_strategy",
    "app.strategy.templates.hyper_scalp",
    "app.strategy.templates.gold_sniper_mini",
    "app.strategy.templates.antichop",
    "app.strategy.templates.gold_scalp_daily",
    "app.strategy.templates.universal_hybrid",
    "app.strategy.templates.scalping",
    "app.strategy.templates.sniper",
    "app.strategy.templates.trend_rider",
]

passed = 0
failed = 0
fail_details = []

for mod_name in strategies:
    short = mod_name.split(".")[-1]
    try:
        importlib.import_module(mod_name)
        print(f"  OK  {short}")
        passed += 1
    except Exception as e:
        msg = str(e).split("\n")[0]
        print(f" FAIL {short}: {msg}")
        failed += 1
        fail_details.append((short, msg))

print(f"\nResult: {passed}/{len(strategies)} passed, {failed} failed")
if fail_details:
    print("\nFailed strategies:")
    for name, err in fail_details:
        print(f"  - {name}: {err}")
