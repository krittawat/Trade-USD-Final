import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backtest_full import FullFeatureBacktester
from app.strategy.templates.m5_rapid_scalper import M5RapidScalperStrategy
from scripts.train_gold_best import fetch_data

# Fetch 30 days of data
df, _ = fetch_data("XAUUSDc", 30)
if df is not None:
    strategy = M5RapidScalperStrategy()
    bt = FullFeatureBacktester(strategy, initial_equity=10000)
    result = bt.run(df, "XAUUSDc", contract_size=100.0, point=0.01, digits=2)
        
    print("Trades:", result.total_trades)
    print("Pnl:", round(result.total_profit_usd, 2))
    print("Win Rate:", round(result.win_rate, 2))
    print("Max DD:", round(result.max_drawdown_pct, 2))
else:
    print("Failed to fetch data")
