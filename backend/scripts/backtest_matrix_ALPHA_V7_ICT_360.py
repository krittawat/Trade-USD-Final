import sys
import os
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import itertools

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import MetaTrader5 as mt5
    from app.execution.backtester import Backtester
    from app.strategy.templates.alpha_v7_ict import AlphaV7ICTStrategy
    from app.domain.models import SymbolProfile
except ImportError as e:
    print(f"Import error: {e}")
    sys.exit(1)

def run_matrix_bt(symbol: str, timeframe: int, days: int, param_grid: dict):
    if not mt5.initialize():
        print(f"MT5 Init failed: {mt5.last_error()}")
        return []

    settings_path = Path(__file__).resolve().parent.parent / "trader" / "config" / "settings.json"
    with open(settings_path, 'r', encoding='utf-8') as f:
        settings = json.load(f)

    symbols_map = settings.get("symbols", {})
    mt5_sym = symbols_map.get(symbol, symbol)
    
    # Contract size & precision setup
    contract_size = 1.0
    point = 0.01
    if "XAU" in symbol: contract_size = 100.0; point = 0.01
    elif "XAG" in symbol: contract_size = 5000.0; point = 0.001
    elif "BTC" in symbol: contract_size = 1.0; point = 0.01
    elif "US30" in symbol: contract_size = 1.0; point = 0.1

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    
    rates = mt5.copy_rates_range(mt5_sym, timeframe, utc_from, utc_to)
    if rates is None or len(rates) < 500:
        print(f"[{symbol}] SKIPPED: Insufficient data")
        return []

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df = df.drop_duplicates(subset=["time"])
    df.set_index("time", inplace=True)
    df = df.sort_index()
    # Optimization: Make index TZ-aware to skip copies in _prepare_frame
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    # --- VECTORIZED PRE-CALCULATION (Massive speedup!) ---
    from app.analysis.indicators import atr, ema, adx, rsi, macd, bulls_power, bears_power
    print(f"  [VECTOR] Pre-calculating indicators for {symbol}...", flush=True)
    
    df["atr"] = atr(df["high"], df["low"], df["close"], 14)
    df["ema_fast"] = ema(df["close"], 20)
    df["ema_slow"] = ema(df["close"], 50)
    df["ema_trend"] = ema(df["close"], 200)
    
    adx_data = adx(df["high"], df["low"], df["close"], 14)
    df["adx"] = adx_data["ADX_14"]
    df["plus_di"] = adx_data["DMP_14"]
    df["minus_di"] = adx_data["DMN_14"]
    
    df["rsi"] = rsi(df["close"], 14)
    
    macd_data = macd(df["close"], 12, 26, 9)
    df["macd_hist"] = macd_data["MACDh_12_26_9"]
    
    df["bull_power"] = bulls_power(df["high"], df["close"], 13)
    df["bear_power"] = bears_power(df["low"], df["close"], 13)
    
    print(f"  [VECTOR] Ready. Starting optimization sweep...", flush=True)

    keys, values = zip(*param_grid.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    print(f"\n🚀 STARTING MATRIX FOR {symbol} | COMBINATIONS: {len(combinations)}")
    results = []

    for i, params in enumerate(combinations):
        strategy = AlphaV7ICTStrategy(symbol=mt5_sym, **params)
        bt = Backtester(strategy, initial_equity=300.0, risk_per_trade=0.01, warmup_bars=300)
        
        try:
            res = bt.run(df, symbol=mt5_sym, contract_size=contract_size, point=point)
            if res.total_trades >= 5: # Reliability filter
                score = (res.total_profit_usd * 1.0) + (res.win_rate * 0.5) + (res.profit_factor * 10.0) - (res.max_drawdown_pct * 2.0)
                result_row = {
                    "symbol": symbol,
                    "score": round(score, 2),
                    "pnl": round(res.total_profit_usd, 2),
                    "wr": round(res.win_rate, 1),
                    "pf": round(res.profit_factor, 2),
                    "dd": round(res.max_drawdown_pct, 1),
                    "trades": res.total_trades,
                    "params": params
                }
                results.append(result_row)
                if i % 5 == 0:
                    print(f"  [{i}/{len(combinations)}] PnL: ${result_row['pnl']} | PF: {result_row['pf']} | Score: {result_row['score']}", flush=True)
        except Exception as e:
            continue

    mt5.shutdown()
    return sorted(results, key=lambda x: x['score'], reverse=True)

def main():
    # Streamlined Institutional Grid (Focus on most impactful parameters)
    param_grid = {
        "min_rr": [2.0, 3.0],
        "fvg_min_size_atr": [0.4, 0.7],
        "fvg_lookback": [4, 7],
        "displacement_body_ratio": [0.5, 0.6],
        "pivot_length": [4]
    } # 2 * 2 * 2 * 2 * 1 = 16 combinations per symbol (32 total) -> (~5-10 mins)

    symbols = ["XAUUSDm", "BTCUSDm"]
    days = 60 # Keep 60 days for robustness
    all_best = {}

    for symbol in symbols:
        results = run_matrix_bt(symbol, mt5.TIMEFRAME_M5, days, param_grid)
        if results:
            best = results[0]
            all_best[symbol] = best
            print(f"\n✅ BEST FOR {symbol} (Ranked by Score):", flush=True)
            for j, r in enumerate(results[:3]):
                print(f"  #{j+1} Score: {r['score']} | PnL: ${r['pnl']} | PF: {r['pf']} | Params: {r['params']}", flush=True)

    # Save Results
    out_path = Path(__file__).resolve().parent.parent / "data" / "exports" / "ict_matrix_best_params.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(all_best, f, indent=4)
        
    print(f"\n💾 Saved optimal parameters to {out_path}", flush=True)

if __name__ == "__main__":
    main()
