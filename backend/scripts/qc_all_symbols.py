import sys
import os
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import MetaTrader5 as mt5
    from app.execution.backtester import Backtester
    from app.strategy.templates.alpha_v6_smc import AlphaV6SMCStrategy
    from app.domain.models import SymbolProfile
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

def main():
    if not mt5.initialize():
        print(f"MT5 Init failed: {mt5.last_error()}")
        sys.exit(1)

    # 1. Load settings
    settings_path = Path(__file__).resolve().parent.parent / "trader" / "config" / "settings.json"
    with open(settings_path, 'r', encoding='utf-8') as f:
        settings = json.load(f)

    symbols_map = settings.get("symbols", {})
    days = 30
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)

    print("========================================")
    print(" QUICK QC BACKTEST: ALPHA V6 (30 DAYS)")
    print("========================================")

    results = []

    for name, mt5_sym in symbols_map.items():
        if name == "USOILm": continue # duplicate
        
        # Determine contract size & points based on typical Exness standard math
        contract_size = 100000.0 if "USD" in mt5_sym[-3:] and name[:3] in ["EUR","GBP","AUD","NZD"] else 100.0
        point = 0.00001
        digits = 5
        
        if "XAU" in name: contract_size = 100.0; point = 0.01; digits = 2
        elif "XAG" in name: contract_size = 5000.0; point = 0.001; digits = 3
        elif "BTC" in name: contract_size = 1.0; point = 0.01; digits = 2
        elif "USOIL" in name: contract_size = 1000.0; point = 0.01; digits = 2
        elif "US30" in name: contract_size = 1.0; point = 0.1; digits = 1
        elif "USTEC" in name: contract_size = 1.0; point = 0.1; digits = 1
        elif "JPY" in mt5_sym: contract_size = 100000.0; point = 0.001; digits = 3

        rates = mt5.copy_rates_range(mt5_sym, mt5.TIMEFRAME_M15, utc_from, utc_to)
        if rates is None or len(rates) < 300:
            print(f"{mt5_sym:10s} : SKIPPED (No data)")
            continue

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")

        strategy = AlphaV6SMCStrategy(symbol=mt5_sym)
        # Force strict limits down to let the strategy trade
        # Actually it uses ParamLoader, but let's just see out of the box performance.
        bt = Backtester(strategy, initial_equity=5000.0, risk_per_trade=0.01, warmup_bars=200)
        
        try:
            res = bt.run(df, symbol=mt5_sym, contract_size=contract_size, point=point)
            if res.total_trades > 0:
                print(f"{mt5_sym:10s} : TRADES={res.total_trades:<4} WR={res.win_rate:<5.1f}% PF={res.profit_factor:<5.2f} P&L=${res.total_profit_usd:<8.2f} DD={res.max_drawdown_pct:<5.1f}%")
                results.append(res)
            else:
                print(f"{mt5_sym:10s} : NO TRADES")
        except Exception as e:
            print(f"{mt5_sym:10s} : ERROR - {e}")

    mt5.shutdown()
    print("========================================")

if __name__ == "__main__":
    main()
