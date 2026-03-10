"""Quick JTG backtest — saves results to JSON file."""
import sys, os, json, time, logging
from pathlib import Path

# Suppress ALL logging before importing anything
for h in logging.root.handlers[:]:
    logging.root.removeHandler(h)
logging.basicConfig(level=logging.CRITICAL)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

import MetaTrader5 as mt5
import pandas as pd

# Connect MT5
init_args = {}
for k, v in [("path", "MT5_PATH"), ("login", "MT5_LOGIN"), ("password", "MT5_PASSWORD"), ("server", "MT5_SERVER")]:
    val = os.environ.get(v, "").strip()
    if val:
        init_args[k] = int(val) if k == "login" else val
if not mt5.initialize(**init_args):
    mt5.initialize()

# Fetch data
rates = mt5.copy_rates_from_pos("XAUUSDc", mt5.TIMEFRAME_M15, 0, 5760)
df = pd.DataFrame(rates)
df["time"] = pd.to_datetime(df["time"], unit="s")
if "tick_volume" not in df.columns:
    df["tick_volume"] = 0

print(f"candles={len(df)}", flush=True)

# Run backtest
from app.strategy.templates.jtg_zone_fvg import JTGZoneFVGStrategy
from app.execution.backtester import Backtester
from app.risk.cooldown_manager import CooldownManager
from app.risk.risk_dampener import RiskDampener
from app.risk.session_guard import SessionGuard

s = JTGZoneFVGStrategy()
bt = Backtester(
    strategy=s, initial_equity=10000.0, risk_per_trade=0.02,
    commission_per_lot=0.0, slippage_points=0.02, warmup_bars=250,
    cooldown_mgr=CooldownManager(cooldown_minutes=0, max_consecutive_losses=999),
    risk_dampener=RiskDampener(),
    session_guard=SessionGuard(max_trades_per_session=999),
    max_trades_per_session=999,
)

t0 = time.monotonic()
r = bt.run(candles=df, symbol="XAUUSDc", contract_size=100.0, point=0.01)
elapsed = time.monotonic() - t0

# Save to JSON
result = {
    "trades": r.total_trades,
    "wins": r.winning_trades,
    "losses": r.losing_trades,
    "win_rate": r.win_rate,
    "profit_factor": r.profit_factor,
    "pnl": r.total_profit_usd,
    "max_dd_pct": r.max_drawdown_pct,
    "max_dd_usd": r.max_drawdown_usd,
    "initial_equity": r.initial_equity,
    "final_equity": r.final_equity,
    "expectancy": r.expectancy,
    "avg_rr": r.avg_rr,
    "sharpe": r.sharpe_ratio,
    "avg_bars": r.avg_bars_held,
    "duration_s": round(elapsed, 1),
    "per_regime": r.per_regime,
    "criteria": {
        "wr_pass": r.win_rate >= 50,
        "pf_pass": r.profit_factor >= 1.3,
        "dd_pass": r.max_drawdown_pct <= 6.0,
        "live_ready": r.win_rate >= 50 and r.profit_factor >= 1.3 and r.max_drawdown_pct <= 6.0,
    }
}

out_path = Path(__file__).resolve().parent.parent / "reports" / "jtg_backtest_result.json"
out_path.parent.mkdir(exist_ok=True)
with open(out_path, "w") as f:
    json.dump(result, f, indent=2, default=str)

print(f"SAVED: {out_path}", flush=True)
mt5.shutdown()
