import sys
import os
import pandas as pd
import MetaTrader5 as mt5
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.strategy.templates.alpha_v6_smc import AlphaV6SMCStrategy
from app.domain.models import SymbolProfile, AccountState, Decision
from app.execution.backtester import Backtester
from app.core.config import get_settings

def run_verification():
    print("🚀 Starting BTC Verification Backtest...")
    if not mt5.initialize():
        print("❌ MT5 Initialization failed")
        return

    symbol = "BTCUSDm"
    days = 30
    timeframe = mt5.TIMEFRAME_M15

    # 1. Fetch Data
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, timeframe, utc_from, utc_to)
    
    if rates is None or len(rates) == 0:
        print(f"❌ No data for {symbol}")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    print(f"✅ Loaded {len(df)} bars for {symbol}")

    # 2. Setup Strategy with Optimized Params (Applied to the file already, but can override here for safety)
    # The file already has: smc_pivot=11, ssl_baseline=110, tp_mult=1.5, mss_displacement=1.4, adx_threshold=23, risk_per_trade=0.016
    strategy = AlphaV6SMCStrategy(symbol=symbol)
    
    # 3. Setup Backtester
    # Note: We use a simple Backtester for verification of the core logic
    bt = Backtester(strategy, initial_equity=10000.0)
    
    # Run
    # We need to provide contract_size, point, etc. for BTC
    # Standard BTCUSDm: contract_size=1, point=1.0 (or similar)
    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        print(f"❌ Could not get symbol info for {symbol}")
        mt5.shutdown()
        return
        
    result = bt.run(
        df, 
        symbol=symbol, 
        contract_size=symbol_info.trade_contract_size,
        point=symbol_info.point
    )

    # 4. Report
    print("\n" + "="*50)
    print(f"VERIFICATION RESULT: {symbol} | Strategy: {strategy.name}")
    print("="*50)
    print(f"Total Trades: {result.total_trades}")
    print(f"Win Rate:     {result.win_rate}%")
    print(f"Profit Factor: {result.profit_factor:.2f}")
    print(f"Total Profit: ${result.total_profit_usd:.2f}")
    print(f"Max Drawdown:  {result.max_drawdown_pct:.2f}%")
    print("="*50)
    
    if result.max_drawdown_pct <= 6.0:
        print("✅ SUCCESS: Max DD is within the 6% limit!")
    else:
        print("❌ FAILURE: Max DD still exceeds 6% limit.")

    mt5.shutdown()

if __name__ == "__main__":
    run_verification()
