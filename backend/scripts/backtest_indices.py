import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import MetaTrader5 as mt5

# Setup paths
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.strategy.templates.alpha_v6_smc import AlphaV6SMCStrategy
from app.execution.backtester import Backtester
from app.domain.models import SymbolProfile
from app.domain.enums import Action

def run_index_backtest(symbol: str, days: int = 15):
    print(f"\n🚀 Running Backtest for {symbol} ({days} Days)...")
    
    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, utc_from, utc_to)
    
    if rates is None or len(rates) == 0:
        print(f"❌ No data for {symbol}")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"✅ Loaded {len(df)} candles.")

    # Get symbol info for contract size, etc.
    info = mt5.symbol_info(symbol)
    if not info:
        print(f"❌ Could not get symbol info for {symbol}")
        mt5.shutdown()
        return

    profile = SymbolProfile(
        symbol=symbol, 
        digits=info.digits, 
        point=info.point,
        contract_size=info.trade_contract_size, 
        volume_min=info.volume_min,
        volume_max=info.volume_max, 
        volume_step=info.volume_step,
        timeframe="M15"
    )

    strategy = AlphaV6SMCStrategy(symbol=symbol)
    bt = Backtester(strategy, initial_equity=1000.0) 
    
    try:
        res = bt.run(df, symbol=symbol, contract_size=info.trade_contract_size, point=info.point)
        print(f"\n🏆 Results for {symbol}:")
        print(f"  Total Trades: {res.total_trades}")
        print(f"  Win Rate:     {res.win_rate:.2f}%")
        print(f"  Profit Factor: {res.profit_factor:.2f}")
        print(f"  Total P&L:    ${res.total_profit_usd:.2f}")
        print(f"  Max DD:       {res.max_drawdown_pct:.2f}%")
    except Exception as e:
        print(f"❌ Backtest failed: {e}")
        import traceback
        traceback.print_exc()

    mt5.shutdown()

if __name__ == "__main__":
    run_index_backtest("US30m", days=15)
    run_index_backtest("USTECm", days=15)
