"""
Tuned Gold Optimization — Focused on parameters that actually move PF.

Key insight: The strategy's analyze() uses adaptive SL/TP multipliers from
regime detection, overriding the grid's SL_ATR_MULT/TP_ATR_MULT.

This script targets:
1. SuperTrend sensitivity (LEN/MUL) - Signal precision
2. Volume filter threshold - Trade frequency vs quality
3. Profit locking - Preserve gains vs let runners run
4. SL distance / Min SL - Risk per trade
5. RSI/ATR periods - Indicator responsiveness
"""

import sys
import logging
import json
import time
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Suppress logging for speed
logging.getLogger().handlers = []
logging.getLogger().setLevel(logging.CRITICAL)

# ─── TUNED PARAM GRID (16 configs) ───
TUNED_GRID = [
    # ─── Group A: SuperTrend Sensitivity ───
    {
        "label": "ST_FAST_7_2.0",
        "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0,
        "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 2.0,
        "CHANDELIER_MULT": 2.0, "PROFIT_LOCK_1": 1.0, "PROFIT_LOCK_2": 2.0,
        "use_volume_filter": False, "min_rvol": 0.0,
    },
    {
        "label": "ST_SLOW_14_3.5",
        "SUPERTREND_LEN": 14, "SUPERTREND_MUL": 3.5,
        "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 2.0,
        "CHANDELIER_MULT": 2.0, "PROFIT_LOCK_1": 1.0, "PROFIT_LOCK_2": 2.0,
        "use_volume_filter": False, "min_rvol": 0.0,
    },
    {
        "label": "ST_MED_10_2.5",
        "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 2.0,
        "CHANDELIER_MULT": 2.0, "PROFIT_LOCK_1": 1.0, "PROFIT_LOCK_2": 2.0,
        "use_volume_filter": False, "min_rvol": 0.0,
    },

    # ─── Group B: Volume Filter Impact ───
    {
        "label": "VOL_LOW_0.5",
        "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 2.0,
        "CHANDELIER_MULT": 2.0, "PROFIT_LOCK_1": 1.0, "PROFIT_LOCK_2": 2.0,
        "use_volume_filter": True, "min_rvol": 0.5,
    },
    {
        "label": "VOL_MED_1.0",
        "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 2.0,
        "CHANDELIER_MULT": 2.0, "PROFIT_LOCK_1": 1.0, "PROFIT_LOCK_2": 2.0,
        "use_volume_filter": True, "min_rvol": 1.0,
    },
    {
        "label": "VOL_HI_1.5",
        "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 2.0,
        "CHANDELIER_MULT": 2.0, "PROFIT_LOCK_1": 1.0, "PROFIT_LOCK_2": 2.0,
        "use_volume_filter": True, "min_rvol": 1.5,
    },

    # ─── Group C: Profit Locking Styles ───
    {
        "label": "LOCK_TIGHT",
        "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 2.0,
        "CHANDELIER_MULT": 1.5, "PROFIT_LOCK_1": 0.5, "PROFIT_LOCK_2": 1.0,
        "use_volume_filter": False, "min_rvol": 0.0,
    },
    {
        "label": "LOCK_WIDE",
        "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 2.0,
        "CHANDELIER_MULT": 3.0, "PROFIT_LOCK_1": 1.5, "PROFIT_LOCK_2": 3.0,
        "use_volume_filter": False, "min_rvol": 0.0,
    },

    # ─── Group D: SL Distance ───
    {
        "label": "SL_TIGHT_1.5",
        "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 1.5, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 1.5,
        "CHANDELIER_MULT": 2.0, "PROFIT_LOCK_1": 1.0, "PROFIT_LOCK_2": 2.0,
        "use_volume_filter": False, "min_rvol": 0.0,
    },
    {
        "label": "SL_WIDE_3.0",
        "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 3.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 4.0,
        "CHANDELIER_MULT": 2.5, "PROFIT_LOCK_1": 1.0, "PROFIT_LOCK_2": 2.0,
        "use_volume_filter": False, "min_rvol": 0.0,
    },

    # ─── Group E: Indicator Periods ───
    {
        "label": "RSI_FAST_8",
        "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 8, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 2.0,
        "CHANDELIER_MULT": 2.0, "PROFIT_LOCK_1": 1.0, "PROFIT_LOCK_2": 2.0,
        "use_volume_filter": False, "min_rvol": 0.0,
    },
    {
        "label": "ATR_SLOW_21",
        "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 2.5,
        "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 21,
        "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 2.0,
        "CHANDELIER_MULT": 2.0, "PROFIT_LOCK_1": 1.0, "PROFIT_LOCK_2": 2.0,
        "use_volume_filter": False, "min_rvol": 0.0,
    },

    # ─── Group F: COMBO Candidates ───
    {
        "label": "COMBO_SAFE",
        "SUPERTREND_LEN": 14, "SUPERTREND_MUL": 3.0,
        "RSI_PERIOD": 14, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 3.0,
        "CHANDELIER_MULT": 1.5, "PROFIT_LOCK_1": 0.5, "PROFIT_LOCK_2": 1.2,
        "use_volume_filter": False, "min_rvol": 0.0,
    },
    {
        "label": "COMBO_AGGRO",
        "SUPERTREND_LEN": 7, "SUPERTREND_MUL": 2.0,
        "RSI_PERIOD": 10, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 1.8, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 1.5,
        "CHANDELIER_MULT": 2.5, "PROFIT_LOCK_1": 1.2, "PROFIT_LOCK_2": 2.5,
        "use_volume_filter": True, "min_rvol": 0.8,
    },
    {
        "label": "COMBO_BALANCED",
        "SUPERTREND_LEN": 10, "SUPERTREND_MUL": 3.0,
        "RSI_PERIOD": 12, "ADX_PERIOD": 14, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 2.0, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 2.5,
        "CHANDELIER_MULT": 2.0, "PROFIT_LOCK_1": 0.8, "PROFIT_LOCK_2": 1.5,
        "use_volume_filter": True, "min_rvol": 0.5,
    },
    {
        "label": "COMBO_PRECISION",
        "SUPERTREND_LEN": 12, "SUPERTREND_MUL": 2.8,
        "RSI_PERIOD": 10, "ADX_PERIOD": 10, "ATR_PERIOD": 14,
        "SL_ATR_MULT": 1.8, "TP_ATR_MULT": 3.0, "MIN_SL_DISTANCE": 2.0,
        "CHANDELIER_MULT": 1.8, "PROFIT_LOCK_1": 0.7, "PROFIT_LOCK_2": 1.5,
        "use_volume_filter": True, "min_rvol": 0.7,
    },
]


def main():
    import scripts.optimize_gold_300d as opt_module
    import pandas as pd
    import numpy as np
    import MetaTrader5 as mt5

    print("=" * 80)
    print("TUNED GOLD OPTIMIZATION (30 Days, USC Cent)")
    print(f"Configs: {len(TUNED_GRID)}")
    print("Testing: SuperTrend, Volume, ProfitLock, SL, RSI, ATR, Combos")
    print("=" * 80)

    # Monkey-patch the module's PARAM_GRID
    opt_module.PARAM_GRID = TUNED_GRID

    exit_code = opt_module.run_optimization(
        days=30,
        user_symbol="XAUUSDc",
        portfolios=[1000.0, 3000.0],
        csv_path="backend/data/optimization_gold_tuned.csv",
        min_pf=1.05,  # Lower threshold to qualify more
    )

    # Show top results
    try:
        df = pd.read_csv("backend/data/optimization_gold_tuned.csv")
        df_sorted = df.sort_values("profit_factor", ascending=False)
        print("\n" + "=" * 80)
        print("TOP 10 BY PROFIT FACTOR:")
        print("=" * 80)
        cols = ["label", "portfolio", "trades", "win_rate", "profit_factor", "pnl", "roi_pct", "max_dd_pct"]
        available = [c for c in cols if c in df_sorted.columns]
        print(df_sorted[available].head(10).to_string(index=False))
    except Exception as e:
        print(f"Could not show results: {e}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
