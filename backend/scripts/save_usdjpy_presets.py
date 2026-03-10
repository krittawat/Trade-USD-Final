"""Save USDJPY optimization results to SQLite (brain.db)"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.brain.memory_store import MemoryStore

memory = MemoryStore()
memory.connect()

presets = {
    "QUICK_SCALP": {
        "params": {
            "ema_fast": 50, "ema_slow": 200,
            "adx_period": 14, "adx_threshold": 30,
            "rsi_period": 14, "atr_period": 14,
            "sl_atr_mult": 2.0, "tp_atr_mult": 1.5,
            "pullback_zone_atr": 2.5, "min_confidence": 0.60
        },
        "score": 1.10,
        "result": {
            "win_rate": 59.2, "profit_factor": 1.10,
            "total_trades": 353, "total_pnl": 2174, "max_dd": 13.8
        }
    },
    "TREND_FOLLOW": {
        "params": {
            "ema_fast": 50, "ema_slow": 200,
            "adx_period": 14, "adx_threshold": 25,
            "rsi_period": 14, "atr_period": 14,
            "sl_atr_mult": 2.0, "tp_atr_mult": 5.0,
            "pullback_zone_atr": 2.5, "min_confidence": 0.60
        },
        "score": 1.17,
        "result": {
            "win_rate": 30.7, "profit_factor": 1.17,
            "total_trades": 257, "total_pnl": 4415, "max_dd": 28.0
        }
    },
    "BALANCED": {
        "params": {
            "ema_fast": 50, "ema_slow": 200,
            "adx_period": 14, "adx_threshold": 30,
            "rsi_period": 14, "atr_period": 14,
            "sl_atr_mult": 1.5, "tp_atr_mult": 2.0,
            "pullback_zone_atr": 2.5, "min_confidence": 0.60
        },
        "score": 1.20,
        "result": {
            "win_rate": 45.3, "profit_factor": 1.20,
            "total_trades": 358, "total_pnl": 4471, "max_dd": 26.5
        }
    }
}

for name, data in presets.items():
    strat_name = f"usdjpy_smart_{name}"
    
    # Save evolved params (best DNA)
    memory.save_evolved_params(
        strategy_name=strat_name,
        symbol="USDJPYc",
        regime="ALL",
        params=data["params"],
        score=data["score"]
    )
    
    # Save backtest run
    memory.save_backtest_run(
        strategy_name=strat_name,
        symbol="USDJPYc",
        timeframe="M5",
        config=data["params"],
        result=data["result"]
    )
    
    wr = data["result"]["win_rate"]
    pf = data["result"]["profit_factor"]
    pnl = data["result"]["total_pnl"]
    print(f"Saved: {strat_name} | WR={wr}% | PF={pf} | PnL=${pnl}")

# Verify
rows = memory.get_all_evolved_params()
print(f"\nAll evolved params in DB: {len(rows)} rows")
for r in rows:
    sn = r.get("strategy_name", "")
    if "usdjpy" in sn.lower():
        print(f"  {sn} | score={r['score']}")

memory.disconnect()
print("\nDone!")
