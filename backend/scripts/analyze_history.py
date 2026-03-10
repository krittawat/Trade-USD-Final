
"""
Analysis Script — Fetch real MT5 history and run AutoCoach analysis.

Usage:
    python backend/scripts/analyze_history.py --days 30 --symbol XAUUSDc
"""

import sys
import argparse
from datetime import datetime, timedelta, timezone
import pandas as pd
from typing import List, Dict

# Add backend to path
import os
sys.path.append(os.path.join(os.path.dirname(__file__), "../../"))

from app.core.config import get_settings
from app.mt5.client import MT5Client
from app.brain.auto_coach import AutoCoach, SessionData

settings = get_settings()

def fetch_and_analyze(days: int, symbol_filter: str = None):
    client = MT5Client(settings)
    if not client.connect():
        print("Failed to connect to MT5")
        return

    now = datetime.now(timezone.utc)
    from_date = now - timedelta(days=days)

    print(f"Fetching history from {from_date} to {now}...")
    deals = client.get_history_deals(from_date, now, group=symbol_filter or "*")
    
    if not deals:
        print("No deals found.")
        return

    print(f"Found {len(deals)} deals. Processing into trades...")
    trades = process_deals_into_trades(deals)
    print(f"Constructed {len(trades)} trades.")

    if not trades:
        print("No complete trades found (only unmatched deals?).")
        return

    # Group by symbol
    trades_by_symbol = {}
    for t in trades:
        sym = t['symbol']
        if sym not in trades_by_symbol:
            trades_by_symbol[sym] = []
        trades_by_symbol[sym].append(t)

    coach = AutoCoach()

    for sym, sym_trades in trades_by_symbol.items():
        print(f"\n{'='*50}")
        print(f"ANALYZING: {sym} ({len(sym_trades)} trades)")
        print(f"{'='*50}")

        # Construct SessionData
        # Need equity curve... approximated from trades for now
        equity_curve = []
        running_balance = 10000.0 # Assumption unique to this analysis script
        max_dd = 0.0
        peak = running_balance

        for t in sym_trades:
            running_balance += t['pnl']
            equity_curve.append({
                "time": t['exit_time'],
                "equity": running_balance
            })
            if running_balance > peak:
                peak = running_balance
            dd = (peak - running_balance) / peak * 100
            if dd > max_dd:
                max_dd = dd

        session = SessionData(
            trades=sym_trades,
            equity_curve=equity_curve,
            symbol=sym,
            starting_balance=10000.0, # Dummy
            ending_balance=running_balance,
            max_drawdown_pct=max_dd,
            max_drawdown_usd=0.0 # TODO
        )

        report = coach.analyze(session)
        
        # Print Text Report
        print(coach.format_report_text(report))
        print("\nJSON Output (Partial):")
        print(report.to_dict()['trader_personality'])

    client.disconnect()

def process_deals_into_trades(deals: List[Dict]) -> List[Dict]:
    """
    Convert MT5 deals to Trade dicts expected by AutoCoach.
    
    AutoCoach expects:
    {
        "id": int,
        "pnl": float,
        "pnl_net": float,
        "entry": float,
        "exit": float,
        "sl": float,
        "tp": float,
        "rr": float,
        "reason": str, ("SL", "TP", "MANUAL")
        "bars": int,
        "regime": str,
        "session": str,
        "strategy_name": str,
        "lot_size": float
    }
    """
    # Group by position_id
    positions = {}
    for d in deals:
        pos_id = d['position_id']
        if pos_id not in positions:
            positions[pos_id] = []
        positions[pos_id].append(d)

    trades = []
    for pos_id, pos_deals in positions.items():
        # sort by time
        pos_deals.sort(key=lambda x: x['time_msc'])
        
        entry_deal = None
        exit_deal = None
        
        # Simple heuristic: First IN is entry, Last OUT is exit
        # Validation: check entry type
        in_deals = [d for d in pos_deals if d['entry'] == 0] # DEAL_ENTRY_IN
        out_deals = [d for d in pos_deals if d['entry'] == 1] # DEAL_ENTRY_OUT
        
        if not in_deals or not out_deals:
            continue
            
        entry_deal = in_deals[0]
        exit_deal = out_deals[-1] # take the last one
        
        # Calculate Net PnL (sum of all deals in position)
        net_profit = sum(d['profit'] + d['commission'] + d['swap'] + d['fee'] for d in pos_deals)
        
        # Calculate Duration (approx bars)
        duration_sec = (exit_deal['time'] - entry_deal['time'])
        # Approx M5 bars
        bars = max(1, duration_sec // 300)
        
        # Infer Exit Reason
        reason = "MANUAL"
        if exit_deal['reason'] == 4: # DEAL_REASON_SL
            reason = "SL"
        elif exit_deal['reason'] == 5: # DEAL_REASON_TP
            reason = "TP"
        
        # Infer RR
        # Need SL/TP from history (not always available in deals, usually in Orders)
        # But we can simulate realized RR
        risk = 0.0
        # If we can't find SL, we can't calculate R:R accurately
        # But AutoCoach calculates realized RR as pnl / risk (if risk known) OR just generic
        
        # Metric construction
        trade = {
            "id": pos_id,
            "symbol": entry_deal['symbol'],
            "entry_time": datetime.fromtimestamp(entry_deal['time'], tz=timezone.utc).isoformat(),
            "exit_time": datetime.fromtimestamp(exit_deal['time'], tz=timezone.utc).isoformat(),
            "pnl": net_profit,
            "lot_size": entry_deal['volume'],
            "entry": entry_deal['price'],
            "exit": exit_deal['price'],
            "bars": bars,
            "reason": reason,
            "rr": 0.0, # Placeholder
            "sl": 0.0, # Placeholder
            "tp": 0.0, # Placeholder
            "regime": "UNKNOWN", # Need market data to know this
            "session": get_session(entry_deal['time']),
            "strategy_name": "MANUAL" if entry_deal['magic'] == 0 else f"BOT_{entry_deal['magic']}"
        }
        
        trades.append(trade)
        
    return trades

def get_session(timestamp: int) -> str:
    # timestamp is unix epoch
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    h = dt.hour
    if 0 <= h < 8: return "ASIA"
    if 8 <= h < 16: return "LONDON"
    return "NEW_YORK"

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="Days to look back")
    parser.add_argument("--symbol", type=str, help="Filter by symbol")
    args = parser.parse_args()
    
    fetch_and_analyze(args.days, args.symbol)
