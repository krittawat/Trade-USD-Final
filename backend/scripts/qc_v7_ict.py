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
    from app.strategy.templates.alpha_v7_ict import AlphaV7ICTStrategy
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
    # Use 15 days for recent ICT compliance
    days = 15
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)

    print("==========================================")
    print(" 🛡️  QC BACKTEST: ALPHA V7 ICT (MAX EFFICIENCY)")
    print(f" PERIOD: {utc_from.strftime('%Y-%m-%d')} to {utc_to.strftime('%Y-%m-%d')}")
    print("==========================================")

    results = []
    test_symbols = ["XAUUSDm", "BTCUSDm"]

    for name in test_symbols:
        mt5_sym = symbols_map.get(name, name)
        
        # Contract size & precision setup
        contract_size = 1.0
        point = 0.01
        digits = 2
        
        if "XAU" in name: contract_size = 100.0; point = 0.01; digits = 2
        elif "XAG" in name: contract_size = 5000.0; point = 0.001; digits = 3
        elif "BTC" in name: contract_size = 1.0; point = 0.01; digits = 2
        elif "US30" in name: contract_size = 1.0; point = 0.1; digits = 1

        # ICT Silver Bullet & Kill Zones work best on M5
        timeframe = mt5.TIMEFRAME_M5
        rates = mt5.copy_rates_range(mt5_sym, timeframe, utc_from, utc_to)
        
        if rates is None or len(rates) < 500:
            print(f"{mt5_sym:10s} : SKIPPED (Insufficient data)")
            continue

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        # Ensure unique index to avoid pandas comparison bottlenecks
        df = df.drop_duplicates(subset=["time"])
        df.set_index("time", inplace=True)
        df = df.sort_index()

        # Initialize strategy
        strategy = AlphaV7ICTStrategy(symbol=mt5_sym)
        
        # Risk settings for $150 tiny account simulation
        bt = Backtester(strategy, initial_equity=150.0, risk_per_trade=0.01, warmup_bars=300)
        
        try:
            res = bt.run(df, symbol=mt5_sym, contract_size=contract_size, point=point)
            if res.total_trades > 0:
                print(f"{mt5_sym:10s} : TRADES={res.total_trades:<4} WR={res.win_rate:<5.1f}% PF={res.profit_factor:<5.2f} P&L=${res.total_profit_usd:<8.2f} DD={res.max_drawdown_pct:<5.1f}%")
                results.append(res)
            else:
                print(f"{mt5_sym:10s} : NO TRADES")
        except Exception as e:
            print(f"{mt5_sym:10s} : ERROR - {e}")
            import traceback
            traceback.print_exc()

    mt5.shutdown()
    
    if results:
        total_pnl = sum(r.total_profit_usd for r in results)
        avg_wr = sum(r.win_rate for r in results) / len(results)
        print("==========================================")
        print(f" FINAL SUMMARY: P&L=${total_pnl:.2f} | AVG WR={avg_wr:.1f}%")
        print("==========================================")
    else:
        print("==========================================")
        print(" NO DATA COLLECTED ")
        print("==========================================")

if __name__ == "__main__":
    main()
