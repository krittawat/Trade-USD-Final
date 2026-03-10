"""
Backtest Per-Pair Strategies & Persist to Brain.
Purpose:
    1. Test specialized strategies on their target pairs (100 days M5).
    2. Persist trade results to brain.db for live auto-switching.

Usage:
    python backend/scripts/backtest_gbp_aud_jpy.py
"""

import sys
import os
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import MetaTrader5 as mt5

# Adjust path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain.memory_store import MemoryStore
from app.domain.models import SymbolProfile
from app.brain.regime import classify_regime
from app.domain.enums import RegimeType
from scripts.backtest_full import FullFeatureBacktester

logging.getLogger("tensorflow").setLevel(logging.ERROR)
logging.getLogger("numpy").setLevel(logging.ERROR)

# ─── Per-Pair Strategy Configuration ───
PAIR_CONFIGS = {
    "GBPUSDc": {
        "strategies": [
            ("gbp_session_breakout", "app.strategy.templates.gbp_session_breakout", "GbpSessionBreakoutStrategy"),
            ("forex_precision",      "app.strategy.templates.forex_precision",      "ForexPrecisionStrategy"),
            ("trend_rider",          "app.strategy.templates.trend_rider",          "TrendRiderStrategy"),
        ],
        "contract_size": 100000.0,
        "point": 0.00001,
        "digits": 5,
    },
    "AUDUSDc": {
        "strategies": [
            ("aud_mean_revert", "app.strategy.templates.aud_mean_revert", "AudMeanRevertStrategy"),
            ("ranging_sniper",  "app.strategy.templates.ranging_sniper",  "RangingSniperStrategy"),
            ("forex_precision", "app.strategy.templates.forex_precision",  "ForexPrecisionStrategy"),
        ],
        "contract_size": 100000.0,
        "point": 0.00001,
        "digits": 5,
    },
    "USDJPYc": {
        "strategies": [
            ("jpy_trend_follow", "app.strategy.templates.jpy_trend_follow", "JpyTrendFollowStrategy"),
            ("trend_rider",      "app.strategy.templates.trend_rider",      "TrendRiderStrategy"),
            ("forex_precision",  "app.strategy.templates.forex_precision",  "ForexPrecisionStrategy"),
        ],
        "contract_size": 100000.0,
        "point": 0.001,
        "digits": 3,
    },
}


def load_strategy(module_path: str, class_name: str):
    """Dynamically import and instantiate a strategy."""
    import importlib
    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        return cls()
    except Exception as e:
        print(f"  ❌ Import error: {module_path}.{class_name}: {e}")
        return None


def persist_to_brain(memory: MemoryStore, result, strategy_name: str, symbol: str):
    """Save backtest trades to brain memory."""
    if not result or not result.trades:
        return
    print(f"  💾 Persisting {len(result.trades)} trades to Brain...")

    for trade in result.trades:
        if isinstance(trade, dict):
            regime_val = trade.get("regime", "UNKNOWN")
            entry_time = trade.get("entry_time")
            profit_usd = trade.get("profit_usd", 0.0) or 0.0
        else:
            regime_val = getattr(trade, "regime", "UNKNOWN")
            entry_time = getattr(trade, "entry_time", datetime.now())
            profit_usd = getattr(trade, "profit_usd", 0.0) or 0.0

        if hasattr(regime_val, "value"):
            regime_val = regime_val.value

        hour = entry_time.hour if isinstance(entry_time, datetime) else 0
        session = "ASIA"
        if 8 <= hour < 16: session = "LONDON"
        if 13 <= hour < 22: session = "NY"

        memory.record_trade_outcome(
            strategy_name=strategy_name,
            symbol=symbol,
            regime=str(regime_val),
            session=session,
            profit_usd=profit_usd,
            risk_reward=0.0,
        )


def main():
    print("=" * 60)
    print("🧠 PER-PAIR STRATEGY BACKTEST (100 Days)")
    print("=" * 60)

    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return

    memory = MemoryStore(db_path="backend/data/sqlite/brain.db")
    memory.connect()

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=100)

    best_picks = {}

    for symbol, config in PAIR_CONFIGS.items():
        print(f"\n{'─' * 50}")
        print(f"📊 {symbol}")
        print(f"{'─' * 50}")

        # Fetch data
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, utc_from, utc_to)
        if rates is None or len(rates) == 0:
            print(f"  ❌ No data for {symbol}")
            continue

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        print(f"  📈 {len(df)} bars loaded")

        best = {"pnl": -999999.0, "strat": None, "wr": 0, "pf": 0, "trades": 0}

        for strat_name, mod_path, cls_name in config["strategies"]:
            strategy = load_strategy(mod_path, cls_name)
            if strategy is None:
                continue

            print(f"\n  ▶ Testing {strat_name}...")
            bt = FullFeatureBacktester(strategy, initial_equity=10000.0)
            result = bt.run(
                df, symbol,
                contract_size=config["contract_size"],
                point=config["point"],
                digits=config["digits"],
            )

            pnl = result.total_profit_usd
            wr = result.win_rate
            pf = result.profit_factor
            trades = result.total_trades

            status = "✅" if pnl > 0 and wr >= 50 else "⚠️"
            print(f"    {status} {strat_name:25s}: PnL=${pnl:>8.2f} | WR={wr}% | PF={pf} | Trades={trades}")

            # Persist ALL results to Brain (let the Brain filter)
            persist_to_brain(memory, result, strat_name, symbol)

            if pnl > best["pnl"]:
                best = {"pnl": pnl, "strat": strat_name, "wr": wr, "pf": pf, "trades": trades}

        if best["strat"]:
            best_picks[symbol] = best
            status = "🏆" if best["pnl"] > 0 and best["wr"] >= 50 else "⚠️"
            print(f"\n  {status} BEST for {symbol}: {best['strat']} (${best['pnl']:.2f}, WR={best['wr']}%)")
        else:
            print(f"\n  ❌ No viable strategy for {symbol}")

    # ─── Summary ───
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'Symbol':<12} {'Strategy':<25} {'PnL':>10} {'WR':>6} {'PF':>6} {'Trades':>7}")
    print("-" * 66)
    for sym, s in best_picks.items():
        flag = "✅" if s["pnl"] > 0 and s["wr"] >= 50 else "⚠️"
        print(f"{flag} {sym:<10} {s['strat']:<25} ${s['pnl']:>8.2f} {s['wr']:>5}% {s['pf']:>5} {s['trades']:>6}")

    mt5.shutdown()
    print("\n✅ Done. Brain has been updated.")


if __name__ == "__main__":
    main()
