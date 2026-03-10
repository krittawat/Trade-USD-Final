"""
Integration Script: SmartSniper Backtest + Auto Coach Analysis.

1. Runs SmartSniper backtest (60 days).
2. Feeds results to AutoCoach.
3. Generates and saves Coaching Report (Markdown).

Usage:
    python backend/scripts/run_smart_sniper_with_coach.py
"""

import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import logging

# pandas_ta compat
try:
    _orig_setattr = pd.Series.__setattr__
    def _safe_setattr(self, name, value):
        if name == "category" and isinstance(value, str):
            try: _orig_setattr(self, name, value)
            except (AttributeError, ValueError): pass
        else: _orig_setattr(self, name, value)
    pd.Series.__setattr__ = _safe_setattr
except Exception:
    pass

from app.core.config import Settings
from app.core.logging import get_logger
from app.execution.backtester import Backtester, BacktestResult
from app.strategy.templates.smart_sniper import SmartSniper
from app.brain.auto_coach import AutoCoach, SessionData

# Setup Logger
logger = get_logger("SmartCoachIntegration")
logging.getLogger().setLevel(logging.INFO)

def run_integration():
    SYMBOL = "XAUUSDc"
    DAYS = 60
    
    print(f"\n{'='*70}")
    print(f"  🤖 INTELLIGENT TRADING LAB: SmartSniper + AutoCoach")
    print(f"  Symbol: {SYMBOL} | Period: {DAYS} days")
    print(f"{'='*70}\n")
    
    # 1. Connect MT5
    if not mt5.initialize():
        print(f"[ERROR] MT5 not connected: {mt5.last_error()}")
        return

    # 2. Fetch Data
    print(f"  📥 Fetching M15 Data...", flush=True)
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS)
    rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M15, utc_from, utc_to)
    
    if rates is None or len(rates) == 0:
        print(f"[ERROR] No data fetched for {SYMBOL}")
        return

    candles = pd.DataFrame(rates)
    candles["time"] = pd.to_datetime(candles["time"], unit="s")
    print(f"     Fetched {len(candles)} bars.", flush=True)

    # 3. Run SmartSniper Backtest
    print(f"\n  ▶️ Running SmartSniper Backtest...", flush=True)
    settings = Settings()
    strategy = SmartSniper(settings)
    
    bt = Backtester(
        strategy=strategy,
        initial_equity=1000.0, 
        risk_per_trade=0.02,
        warmup_bars=200
    )
    
    try:
        result = bt.run(candles, symbol=SYMBOL)
        print(f"     ✅ Backtest Complete.")
        print(f"     Trades: {result.total_trades} | PnL: ${result.total_profit_usd}")
        
    except Exception as e:
        print(f"     ❌ Backtest Error: {e}")
        import traceback
        traceback.print_exc()
        return

    # 4. Auto Coach Analysis
    print(f"\n  🧠 Auto Coach Analysis...", flush=True)
    coach = AutoCoach()
    
    # Convert BacktestResult to SessionData
    session = SessionData(
        trades=result.trades, # List of dicts (BacktestResult.trades is list[dict] in backtester.py?)
        # Wait, BacktestResult.trades in backtester.py is list[dict] inside result object?
        # Let's check backtester.py line 87: trades: list[dict] = field(default_factory=list)
        # Yes, it is list[dict].
        equity_curve=result.equity_curve,
        symbol=SYMBOL,
        starting_balance=result.initial_equity,
        ending_balance=result.final_equity,
        max_drawdown_pct=result.max_drawdown_pct,
        max_drawdown_usd=result.max_drawdown_usd,
        strategy_name="SmartSniper"
    )
    
    report = coach.analyze(session)
    report_text = coach.format_report_text(report)
    
    # 5. Save Report
    output_dir = Path(__file__).parent.parent.parent / "logs"
    output_dir.mkdir(exist_ok=True)
    report_path = output_dir / f"coach_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
        
    print(f"\n  📝 Coach Report Generated!")
    print(f"     Saved to: {report_path}")
    print(f"\n{'='*70}")
    print(report_text)
    print(f"{'='*70}\n")

    mt5.shutdown()

if __name__ == "__main__":
    run_integration()
