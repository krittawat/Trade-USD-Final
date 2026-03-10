"""Save best strategy params to brain.db."""
import sqlite3, json
from pathlib import Path
from datetime import datetime, timezone

db_path = Path(__file__).resolve().parent.parent / "data" / "sqlite" / "brain.db"
db_path.parent.mkdir(parents=True, exist_ok=True)
conn = sqlite3.connect(str(db_path))
c = conn.cursor()

c.execute("""CREATE TABLE IF NOT EXISTS best_strategy_params (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    version TEXT,
    params JSON NOT NULL,
    backtest_days INTEGER,
    total_trades INTEGER,
    win_rate REAL,
    profit_factor REAL,
    total_profit_usd REAL,
    max_drawdown_pct REAL,
    is_active INTEGER DEFAULT 1
)""")

# Deactivate old gold_elite params
c.execute("UPDATE best_strategy_params SET is_active=0 WHERE strategy='gold_elite' AND symbol='XAUUSDc'")

# Gold Elite MAX PROFIT best params
gold_params = {
    "EMA_FAST": 9, "EMA_MID": 21, "EMA_SLOW": 50, "EMA_TREND": 200,
    "ADX_PERIOD": 14, "ADX_MIN": 20, "ADX_STRONG": 30, "ADX_GATE": 20,
    "RSI_PERIOD": 14, "RSI_OB": 75, "RSI_OS": 25,
    "RSI_EXTREME_HIGH": 80, "RSI_EXTREME_LOW": 20,
    "MACD_FAST": 12, "MACD_SLOW": 26, "MACD_SIGNAL": 9,
    "VOL_MA": 20, "VOL_SPIKE_RATIO": 1.2,
    "SWING_LOOKBACK": 15,
    "ATR_PERIOD": 14, "SL_ATR_MULT": 2.0, "SL_BUFFER_ATR": 0.5,
    "MIN_SCORE_TRADE": 65,
    "RR_CONSERVATIVE": 2.0, "RR_STANDARD": 2.5, "RR_AGGRESSIVE": 3.0,
    "REGIME_BLOCK": ["RANGING", "LOW_VOLATILITY"],
    "SESSION_BONUS": 5,
}

c.execute("""INSERT INTO best_strategy_params 
    (timestamp, symbol, strategy, version, params, backtest_days,
     total_trades, win_rate, profit_factor, total_profit_usd, max_drawdown_pct, is_active)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
    (datetime.now(timezone.utc).isoformat(),
     "XAUUSDc", "gold_elite", "MAX_PROFIT_v1",
     json.dumps(gold_params),
     200, 83, 57.8, 2.07, 1135.43, 1.7))

conn.commit()

# Verify
c.execute("SELECT id, strategy, version, win_rate, profit_factor, total_profit_usd FROM best_strategy_params WHERE is_active=1")
for r in c.fetchall():
    print(f"  OK: id={r[0]} {r[1]} {r[2]} WR={r[3]}% PF={r[4]} P&L=${r[5]}")

conn.close()
print("Done! Best params saved to brain.db")
