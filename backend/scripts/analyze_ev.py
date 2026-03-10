
"""
Strategy Audit & EV Analysis Script
===================================
Back-fills market regime data for historical trades and calculates Expected Value (EV).

Usage:
    python backend/scripts/analyze_ev.py --days 30 --symbol XAUUSDc
"""

import sys
import os
import argparse
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add backend to path
sys.path.append(str(Path(__file__).parent.parent.parent / "backend"))

from app.core.config import get_settings
from app.mt5.client import MT5Client
from app.brain.regime import classify_regime, RegimeContext

# Constants
LOOKBACK_BARS = 200  # Bars needed for regime analysis (EMA200, ATR, etc.)

def main():
    parser = argparse.ArgumentParser(description="EV Analysis")
    parser.add_argument("--days", type=int, default=30, help="Days to analyze")
    parser.add_argument("--symbol", type=str, default="XAUUSDc", help="Symbol to analyze")
    args = parser.parse_args()

    settings = get_settings()
    client = MT5Client(settings)
    
    if not client.connect():
        print("❌ Failed to connect to MT5")
        return

    print(f"📡 Fetching history for {args.symbol} (Last {args.days} days)...")
    
    # 1. Fetch History Deals/Trades
    now = datetime.now(timezone.utc)
    from_date = now - timedelta(days=args.days)
    
    deals = client.get_history_deals(from_date, now, group=args.symbol)
    if not deals:
        print("⚠️ No deals found.")
        return
        
    trades = process_deals(deals)
    print(f"✅ Found {len(trades)} trades.")
    
    # 2. Fetch Market Data for Context Back-filling
    # We need M5 candles covering the entire period + buffer
    print("🕯️ Fetching M5 market data for regime classification...")
    
    import MetaTrader5 as mt5
    
    # Add buffer for indicators
    data_start = from_date - timedelta(days=5) 
    
    # Use copy_rates_range to get historical data
    rates = mt5.copy_rates_range(args.symbol, mt5.TIMEFRAME_M5, data_start, now)
    
    if rates is None or len(rates) == 0:
        print(f"❌ Failed to fetch candles. MT5 Error: {mt5.last_error()}")
        return
        
    df_m5 = pd.DataFrame(rates)
    df_m5['time'] = pd.to_datetime(df_m5['time'], unit='s', utc=True)
    df_m5.set_index('time', inplace=True)
    df_m5.sort_index(inplace=True)
    
    # Rename tick_volume to volume if needed
    if 'tick_volume' in df_m5.columns:
        df_m5['volume'] = df_m5['tick_volume']
    
    print(f"📊 Market Data: {len(df_m5)} bars from {df_m5.index[0]} to {df_m5.index[-1]}")

    # 3. Analyze Each Trade (Back-fill Regime)
    print("🧠 Back-filling Market Regimes...")
    
    enriched_trades = []
    
    for t in trades:
        entry_time = t['entry_time']
        
        # Slice data leading up to entry_time
        # We need the row at or immediately before entry_time
        # Use asof to find nearest timestamp
        # Ensure entry_time is timezone-aware matching df index
        if entry_time.tzinfo is None:
             entry_time = entry_time.replace(tzinfo=timezone.utc)
             
        # Check if entry time is within data range
        if entry_time < df_m5.index[0] or entry_time > df_m5.index[-1]:
             continue

        idx_loc = df_m5.index.get_indexer([entry_time], method='pad')[0]
        
        if idx_loc < LOOKBACK_BARS:
            # Not enough data available before this trade
            t['regime'] = "UNKNOWN"
            enriched_trades.append(t)
            continue
            
        # Context Window
        window = df_m5.iloc[idx_loc - LOOKBACK_BARS : idx_loc + 1]
        
        # Classify
        regime_ctx = classify_regime(window)
        
        t['regime'] = regime_ctx.regime.value if hasattr(regime_ctx.regime, 'value') else str(regime_ctx.regime)
        t['adx'] = regime_ctx.details.get('adx', 0)
        t['volatility'] = regime_ctx.details.get('volatility_score', 0)
        enriched_trades.append(t)

        sys.stdout.write(".")
        sys.stdout.flush()
        
    print("\n✅ Regime back-fill complete.")
    
    # 4. EV Analysis & Reporting
    generate_audit_report(enriched_trades, args.symbol)

def process_deals(deals):
    """Simplified deal processor (from analyze_history.py logic)"""
    positions = {}
    for d in deals:
        pid = d['position_id']
        if pid not in positions: positions[pid] = []
        positions[pid].append(d)
        
    trades = []
    for pid, deal_list in positions.items():
        deal_list.sort(key=lambda x: x['time_msc'])
        entry = next((d for d in deal_list if d['entry'] == 0), None)
        exit = next((d for d in deal_list if d['entry'] == 1), None)
        
        if not entry or not exit: continue
        
        net_profit = sum(d['profit'] + d['commission'] + d['swap'] + d['fee'] for d in deal_list)
        
        trades.append({
            "id": pid,
            "entry_time": datetime.fromtimestamp(entry['time'], tz=timezone.utc),
            "pnl": net_profit,
            "strategy": "MANUAL" if entry['magic'] == 0 else f"BOT_{entry['magic']}",
            "type": "BUY" if entry['type'] == 0 else "SELL"
        })
    return trades

def classify_historical_regime(df: pd.DataFrame) -> RegimeContext:
    """Wrapper not needed anymore, calling classify_regime directly."""
    pass


def generate_audit_report(trades: list, symbol: str):
    """Calculate EV/PF tables and save to Markdown."""
    df = pd.DataFrame(trades)
    
    # Aggregation
    if df.empty:
        print("No trades to analyze.")
        return

    # Group by Strategy + Regime
    stats = df.groupby(['strategy', 'regime']).agg(
        trades=('id', 'count'),
        wins=('pnl', lambda x: (x > 0).sum()),
        losses=('pnl', lambda x: (x <= 0).sum()),
        avg_win=('pnl', lambda x: x[x > 0].mean() if (x > 0).any() else 0),
        avg_loss=('pnl', lambda x: x[x <= 0].mean() if (x <= 0).any() else 0),
        net_pnl=('pnl', 'sum'),
        max_dd=('pnl', lambda x: x.min()) # Approximation
    ).reset_index()
    
    # Calculate derived metrics
    stats['win_rate'] = (stats['wins'] / stats['trades']) * 100
    stats['profit_factor'] = abs( (stats['wins'] * stats['avg_win']) / (stats['losses'] * stats['avg_loss']).replace(0, -1) )
    stats['ev'] = ( (stats['win_rate']/100) * stats['avg_win'] ) + ( (1 - stats['win_rate']/100) * stats['avg_loss'] )
    
    # Format Report
    report_lines = []
    report_lines.append(f"# 🕵️ Strategy Audit & EV Analysis: {symbol}")
    report_lines.append(f"**Generated:** {datetime.now().isoformat()}\n")
    
    report_lines.append("## 🔥 Heatmap: Expected Value (EV) per Regime")
    report_lines.append("EV = Average Profit per Trade (USD). Negative EV means you are *paying* to trade this setup.\n")
    
    report_lines.append("| Strategy | Regime | Trades | Win% | EV ($) | Profit Factor | Status |")
    report_lines.append("|---|---|---|---|---|---|---|")
    
    toxic_rules = []
    
    for _, row in stats.iterrows():
        status = "✅ Healthy"
        if row['ev'] < 0:
            status = "❌ TOXIC (Negative EV)"
            toxic_rules.append(f"BLOCK `{row['strategy']}` in `{row['regime']}`")
        elif row['profit_factor'] < 1.0:
             status = "⚠️ UNPROFITABLE"
        elif row['trades'] < 5:
             status = "❓ Low Sample"
             
        ev_str = f"**{row['ev']:.2f}**" if row['ev'] > 0 else f"🔴 {row['ev']:.2f}"
        
        report_lines.append(
            f"| **{row['strategy']}** | {row['regime']} | {row['trades']} | {row['win_rate']:.1f}% | {ev_str} | {row['profit_factor']:.2f} | {status} |"
        )
        
    report_lines.append("\n## ☢️ Toxic Combinations (Recommend Blocking)")
    if toxic_rules:
        for rule in toxic_rules:
            report_lines.append(f"- {rule}")
    else:
        report_lines.append("- No highly toxic combinations found (or insufficient data).")
        
    # Save
    out_path = Path("logs/strategy_audit.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(report_lines), encoding="utf-8")
    
    print(f"\n📄 Report saved to: {out_path}")
    print("\nSnippet:")
    print("\n".join(report_lines[:15]))

if __name__ == "__main__":
    main()
