"""
Seed Strategy Params + Registry — Populate DB with default values.

Usage:
    python scripts/seed_strategy_params.py

This script is IDEMPOTENT — re-running it will update existing entries.
It uses UPSERT (INSERT ... ON CONFLICT DO UPDATE).

Tables populated:
    1. strategy_params  — default params for strategies that have them
    2. strategy_registry — all active strategies + regime/asset mapping
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.config import Settings
from app.db.sqlite import SQLiteStore
from app.strategy.templates.gold_elite import GOLD_ELITE_DEFAULTS
from app.strategy.templates.silver_elite import SILVER_ELITE_DEFAULTS
from app.strategy.templates.btc_elite import BTC_ELITE_DEFAULTS


# ====================================================================
# 1. Strategy Params (default tuning parameters)
# ====================================================================
STRATEGY_PARAMS = [
    ("gold_elite", "XAUUSDc", GOLD_ELITE_DEFAULTS, "Gold Elite Defaults"),
    ("silver_elite", "XAGUSDc", SILVER_ELITE_DEFAULTS, "Silver Elite V1_AggressiveMR"),
    ("btc_elite", "BTCUSDc", BTC_ELITE_DEFAULTS, "BTC Elite Defaults"),
]


# ====================================================================
# 2. Strategy Registry — regime + asset class mapping
#    (strategy_name, class_name, module_path, timeframe, asset_class,
#     suitable_regimes, priority, is_active)
# ====================================================================
STRATEGY_REGISTRY = [
    # ── Gold Strategies ──
    ("gold_elite", "GoldEliteStrategy",
     "app.strategy.templates.gold_elite", "M5", "gold",
     ["trending_up", "trending_down", "high_volatility", "breakout", "ranging"], 100, True),

    ("gold_scalp_pro", "GoldScalpProStrategy",
     "app.strategy.templates.gold_scalp_pro", "M5", "gold",
     ["trending_up", "trending_down", "high_volatility"], 90, True),

    ("gold_precision", "GoldPrecisionStrategy",
     "app.strategy.templates.gold_precision", "M5", "gold",
     ["ranging", "low_volatility", "trending_up", "trending_down"], 85, True),

    ("gold_break_checklist", "GoldBreakChecklistStrategy",
     "app.strategy.templates.gold_break_checklist", "M5", "gold",
     ["breakout", "high_volatility", "trending_up"], 80, True),

    ("gold_smart_money", "GoldSmartMoneyStrategy",
     "app.strategy.templates.gold_smart_money", "M5", "gold",
     ["trending_up", "trending_down", "breakout"], 75, True),

    ("gold_session_breakout", "GoldSessionBreakout",
     "app.strategy.templates.gold_session_breakout", "M5", "gold",
     ["breakout", "high_volatility"], 70, True),

    ("gold_silver_wr60", "GoldSilverWr60Strategy",
     "app.strategy.templates.gold_silver_wr60", "M5", "gold",
     ["trending_up", "trending_down", "ranging"], 60, True),

    # ── Silver Strategies ──
    ("silver_elite", "SilverEliteStrategy",
     "app.strategy.templates.silver_elite", "M5", "silver",
     ["trending_up", "trending_down", "ranging", "high_volatility", "breakout"], 100, True),

    ("silver_mean_rev", "SilverMeanRevStrategy",
     "app.strategy.templates.silver_mean_rev", "M5", "silver",
     ["ranging", "low_volatility"], 85, True),

    # ── Crypto Strategies ──
    ("btc_elite", "BtcEliteStrategy",
     "app.strategy.templates.btc_elite", "M5", "crypto",
     ["trending_up", "trending_down", "high_volatility", "breakout", "ranging"], 100, True),

    # ── Forex Strategies ──
    ("forex_precision", "ForexPrecisionStrategy",
     "app.strategy.templates.forex_precision", "M5", "forex",
     ["trending_up", "trending_down", "ranging"], 90, True),

    ("fx_sniper", "FxSniperStrategy",
     "app.strategy.templates.fx_sniper", "M5", "forex",
     ["trending_up", "trending_down", "breakout"], 85, True),

    ("usdjpy_elite", "UsdjpyEliteStrategy",
     "app.strategy.templates.usdjpy_elite", "M5", "forex",
     ["trending_up", "trending_down", "ranging", "breakout"], 80, True),

    # ── Universal Strategies (all asset classes) ──
    ("scalping", "ScalpingStrategy",
     "app.strategy.templates.scalping", "M5", "*",
     ["trending_up", "trending_down", "high_volatility"], 50, True),

    ("sniper", "SniperStrategy",
     "app.strategy.templates.sniper", "M5", "*",
     ["trending_up", "trending_down", "breakout"], 50, True),

    ("sniper_pro", "SniperProStrategy",
     "app.strategy.templates.sniper_pro", "M5", "*",
     ["trending_up", "trending_down", "breakout"], 55, True),

    ("trend_rider", "TrendRiderStrategy",
     "app.strategy.templates.trend_rider", "M5", "*",
     ["trending_up", "trending_down", "strong_trend"], 50, True),

    ("ranging_sniper", "RangingSniperStrategy",
     "app.strategy.templates.ranging_sniper", "M5", "*",
     ["ranging", "low_volatility"], 50, True),
]


def main():
    settings = Settings()
    store = SQLiteStore(settings)
    store.connect()

    # ── 1. Seed strategy_params ──
    print("=" * 60)
    print("Seeding strategy_params...")
    print("=" * 60)
    for strategy_name, symbol, defaults, label in STRATEGY_PARAMS:
        store.save_strategy_params(
            symbol=symbol,
            strategy_name=strategy_name,
            params=defaults,
            label=label,
        )
        print(f"  [OK] {strategy_name} / {symbol}: {len(defaults)} params ({label})")

    # ── 2. Seed strategy_registry ──
    print()
    print("=" * 60)
    print("Seeding strategy_registry...")
    print("=" * 60)
    for (name, class_name, module_path, timeframe,
         asset_class, regimes, priority, is_active) in STRATEGY_REGISTRY:
        store.save_strategy_registry(
            strategy_name=name,
            class_name=class_name,
            module_path=module_path,
            timeframe=timeframe,
            asset_class=asset_class,
            suitable_regimes=regimes,
            priority=priority,
            is_active=is_active,
        )
        print(f"  [OK] {name:25s} | {asset_class:6s} | prio={priority:3d} | regimes={regimes}")

    # ── 3. Verify ──
    print()
    print("=" * 60)
    print("Verification")
    print("=" * 60)

    all_params = store.get_all_strategy_params()
    print(f"\nstrategy_params rows: {len(all_params)}")
    for row in all_params:
        p = row.get("params", {})
        print(f"  {row['strategy_name']:20s} | {row['symbol']:12s} | {len(p)} keys | label={row.get('label', '')}")

    all_registry = store.get_strategy_registry(active_only=False)
    print(f"\nstrategy_registry rows: {len(all_registry)}")
    for row in all_registry:
        print(f"  {row['strategy_name']:25s} | {row.get('asset_class', '?'):6s} | prio={row.get('priority', 0):3d} | active={row.get('is_active', 0)}")

    print(f"\n[DONE] {len(STRATEGY_REGISTRY)} strategies registered in DB.")
    print("Strategies will load from DB at next startup.")


if __name__ == "__main__":
    main()
