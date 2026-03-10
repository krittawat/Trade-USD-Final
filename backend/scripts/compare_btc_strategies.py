import sys
import os
import pandas as pd
import MetaTrader5 as mt5
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.execution.backtester import Backtester

# Load Strategies
from app.strategy.templates.btc_elite import BtcEliteStrategy
from app.strategy.templates.btc_momentum import BtcMomentum
from app.strategy.templates.btc_mean_reversion import BtcMeanReversion
from app.strategy.templates.btc_stop_hunt import BtcStopHuntStrategy
from app.strategy.templates.ADD_18022026.btc_pro_strategy import BTCProStrategy
from app.strategy.templates.alpha_v6_smc import AlphaV6SMCStrategy

def run_comparison():
    print("🚀 Starting BTC Strategies Comparison (30 Days, M15)...")
    if not mt5.initialize():
        print("❌ MT5 Initialization failed")
        return

    symbol = "BTCUSDm"
    days = 30
    timeframe = mt5.TIMEFRAME_M15

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

    symbol_info = mt5.symbol_info(symbol)
    if not symbol_info:
        print(f"❌ Could not get symbol info for {symbol}")
        mt5.shutdown()
        return

    strategies = [
        AlphaV6SMCStrategy(symbol=symbol),
        BtcEliteStrategy(),
        BtcMomentum(),
        BTCProStrategy()
    ]

    results_list = []

    for strategy in strategies:
        # Normalize name
        s_name = "Unknown"
        if hasattr(strategy, 'name'): s_name = strategy.name
        elif hasattr(strategy, 'get_name'): s_name = strategy.get_name()
        else: s_name = strategy.__class__.__name__

        print(f"⏳ Testing {s_name}...")
        
        try:
            bt = Backtester(strategy, initial_equity=10000.0)
            result = bt.run(
                df, 
                symbol=symbol, 
                contract_size=symbol_info.trade_contract_size,
                point=symbol_info.point
            )
            
            results_list.append({
                "Strategy": s_name,
                "Trades": result.total_trades,
                "Win Rate (%)": round(result.win_rate, 2),
                "Profit Factor": round(result.profit_factor, 2),
                "Total Profit ($)": round(result.total_profit_usd, 2),
                "Max DD (%)": round(result.max_drawdown_pct, 2)
            })
        except Exception as e:
            print(f"❌ Error testing {s_name}: {e}")

    mt5.shutdown()

    print("\n" + "="*70)
    print("🏆 BTC STRATEGIES COMPARISON RANKING 🏆")
    print("="*70)
    
    if not results_list:
        print("No results to display.")
        return

    df_results = pd.DataFrame(results_list)
    # Sort by Profit Factor and Win Rate
    df_results = df_results.sort_values(by=["Win Rate (%)", "Profit Factor"], ascending=False)
    
    # Manual Table Display (No tabulate dependency)
    header = f"{'Strategy':<20} | {'Trades':<6} | {'Win Rate (%)':<12} | {'PF':<6} | {'Profit ($)':<12} | {'Max DD (%)':<10}"
    print(header)
    print("-" * len(header))
    for _, row in df_results.iterrows():
        print(f"{row['Strategy']:<20} | {int(row['Trades']):<6} | {row['Win Rate (%)']:<12.2f} | {row['Profit Factor']:<6.2f} | {row['Total Profit ($)']:<12.2f} | {row['Max DD (%)']:<10.2f}")
    print("="*70)

if __name__ == "__main__":
    run_comparison()
