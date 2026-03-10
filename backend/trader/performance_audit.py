import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

# Setup basic logging for terminal output
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("performance_audit")

def calculate_metrics():
    if not mt5.initialize():
        logger.error("❌ MT5 initialization failed")
        return

    # 1. Fetch Deals History (Last 30 days)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=30)
    
    deals = mt5.history_deals_get(start_date, end_date)
    if not deals:
        logger.warning(f"⚠️ No deals found from {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
        mt5.shutdown()
        return

    # Convert to DataFrame
    df = pd.DataFrame(list(deals), columns=deals[0]._asdict().keys())
    
    # Filter for closing deals (entry out) and trade types (BUY/SELL)
    # entry: 0=IN, 1=OUT, 2=INOUT
    # type: 0=BUY, 1=SELL
    df = df[(df['entry'] == 1) & (df['type'].isin([0, 1]))]
    
    if df.empty:
        logger.warning("⚠️ No completed trades found in history.")
        mt5.shutdown()
        return

    # 2. Basic Metrics
    total_trades = len(df)
    winning_trades = df[df['profit'] > 0]
    losing_trades = df[df['profit'] < 0]
    
    win_rate = (len(winning_trades) / total_trades) * 100 if total_trades > 0 else 0
    total_profit = winning_trades['profit'].sum()
    total_loss = abs(losing_trades['profit'].sum())
    profit_factor = total_profit / total_loss if total_loss > 0 else float('inf')
    
    avg_win = winning_trades['profit'].mean() if not winning_trades.empty else 0
    avg_loss = losing_trades['profit'].mean() if not losing_trades.empty else 0
    
    # 3. Drawdown (Approximation from balance curve)
    df['cum_pnl'] = df['profit'].cumsum()
    peak = df['cum_pnl'].cummax()
    drawdown = peak - df['cum_pnl']
    max_drawdown = drawdown.max()
    
    # 4. Asset Breakdown
    asset_perf = df.groupby('symbol')['profit'].agg(['count', 'sum']).rename(columns={'count': 'Trades', 'sum': 'Net PnL'})

    # 5. Output Report
    logger.info("\n" + "="*50)
    logger.info("📊 ANTIGRAVITY PERFORMANCE AUDIT (Last 30 Days)")
    logger.info("="*50)
    logger.info(f"📅 Period: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    logger.info(f"📈 Total Trades:  {total_trades}")
    logger.info(f"✅ Win Rate:      {win_rate:.2f}% ({len(winning_trades)}/{total_trades})")
    logger.info(f"💰 Profit Factor: {profit_factor:.2f}")
    logger.info(f"📉 Max Drawdown:  ${max_drawdown:.2f}")
    logger.info(f"💵 Net PnL:       ${df['profit'].sum():.2f}")
    logger.info(f"🟢 Avg Win:       ${avg_win:.2f}")
    logger.info(f"🔴 Avg Loss:      ${avg_loss:.2f}")
    
    logger.info("\n🌍 ASSET BREAKDOWN:")
    logger.info(asset_perf.to_string())
    
    logger.info("\n" + "="*50)
    if win_rate >= 50 and profit_factor >= 1.3 and max_drawdown < 100:
         logger.info("✅ STATUS: INSTITUTIONAL PERFORMANCE GATES PASSED")
    else:
         logger.info("⚠️ STATUS: OPTIMIZATION REQUIRED")
    logger.info("="*50)

    mt5.shutdown()

if __name__ == "__main__":
    calculate_metrics()
