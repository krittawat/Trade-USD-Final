"""
Smart Pullback-Rejection Backtest — DuckDB/MT5 Edition (1 Year)
================================================================
ฝึกเทรดจากข้อมูลกราฟ 1 ปี ผ่าน DuckDB parquet หรือ MT5 direct.

Logic (เหมือน backtest_pullback.py แต่ข้อมูลเยอะกว่า + Walk-Forward):
    - แบ่งข้อมูลเป็น Train (80%) / Test (20%)
    - หาค่า param ที่ดีที่สุดจาก Train period
    - ทดสอบจริงกับ Test period → ตรวจ overfitting
    - BUY on Pullback (uptrend) / SELL on Rejection (downtrend)

Data Source Priority:
    1. DuckDB parquet (data/exports/mtf/{SYMBOL}_M5.parquet)
    2. MT5 direct (fallback if parquet ไม่พอ)

Usage:
    cd d:\\VibeCode\\Trade\\backend
    $env:PYTHONPATH="."; python scripts/backtest_pullback_1y.py
    $env:PYTHONPATH="."; python scripts/backtest_pullback_1y.py --symbol XAGUSDc
    $env:PYTHONPATH="."; python scripts/backtest_pullback_1y.py --symbol XAUUSDc --days 365
"""
print("🧠 AI Pullback-Rejection Trainer (1Y) — Starting...", flush=True)

import sys
import argparse
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.disable(logging.CRITICAL)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np

# ── Symbol Configs ──
SYMBOL_CONFIGS = {
    "XAUUSDc": {"contract_size": 100.0, "point": 0.01, "digits": 2, "name": "Gold"},
    "XAGUSDc": {"contract_size": 5000.0, "point": 0.001, "digits": 3, "name": "Silver"},
    "BTCUSDc": {"contract_size": 1.0, "point": 0.01, "digits": 2, "name": "Bitcoin"},
}

# ── Extended Parameter Grid ──
PARAM_GRID = [
    {"label": "EMA21_50_RR2",   "ema_fast": 21, "ema_slow": 50,  "rr_ratio": 2.0, "sl_atr": 1.5, "pullback_pct": 0.3, "rsi_buy_max": 50, "rsi_sell_min": 50},
    {"label": "EMA21_50_RR1.5", "ema_fast": 21, "ema_slow": 50,  "rr_ratio": 1.5, "sl_atr": 1.5, "pullback_pct": 0.3, "rsi_buy_max": 50, "rsi_sell_min": 50},
    {"label": "EMA21_50_RR3",   "ema_fast": 21, "ema_slow": 50,  "rr_ratio": 3.0, "sl_atr": 1.5, "pullback_pct": 0.3, "rsi_buy_max": 45, "rsi_sell_min": 55},
    {"label": "EMA10_21_RR2",   "ema_fast": 10, "ema_slow": 21,  "rr_ratio": 2.0, "sl_atr": 1.2, "pullback_pct": 0.4, "rsi_buy_max": 50, "rsi_sell_min": 50},
    {"label": "EMA10_21_RR1.5", "ema_fast": 10, "ema_slow": 21,  "rr_ratio": 1.5, "sl_atr": 1.2, "pullback_pct": 0.4, "rsi_buy_max": 55, "rsi_sell_min": 45},
    {"label": "EMA21_100_RR2",  "ema_fast": 21, "ema_slow": 100, "rr_ratio": 2.0, "sl_atr": 2.0, "pullback_pct": 0.2, "rsi_buy_max": 45, "rsi_sell_min": 55},
    {"label": "EMA21_50_WS2",   "ema_fast": 21, "ema_slow": 50,  "rr_ratio": 2.0, "sl_atr": 2.0, "pullback_pct": 0.3, "rsi_buy_max": 50, "rsi_sell_min": 50},
    # ── Optimized from 30-day results ──
    {"label": "EMA13_34_RR1.8", "ema_fast": 13, "ema_slow": 34,  "rr_ratio": 1.8, "sl_atr": 1.3, "pullback_pct": 0.35, "rsi_buy_max": 48, "rsi_sell_min": 52},
    {"label": "EMA8_21_RR2.5",  "ema_fast": 8,  "ema_slow": 21,  "rr_ratio": 2.5, "sl_atr": 1.5, "pullback_pct": 0.35, "rsi_buy_max": 50, "rsi_sell_min": 50},
    {"label": "EMA21_50_T_RR2", "ema_fast": 21, "ema_slow": 50,  "rr_ratio": 2.0, "sl_atr": 1.8, "pullback_pct": 0.25, "rsi_buy_max": 48, "rsi_sell_min": 52},
]


# ════════════════════════════════════════════════════════════════════
# Indicators (vectorized for speed on large datasets)
# ════════════════════════════════════════════════════════════════════

def calc_ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False).mean()

def calc_rsi(s: pd.Series, period: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss = (-delta).clip(lower=0).ewm(span=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift(1)).abs()
    lc = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def is_bullish_candle(row) -> bool:
    body = abs(row["close"] - row["open"])
    total = row["high"] - row["low"]
    if total == 0: return False
    lower_wick = min(row["open"], row["close"]) - row["low"]
    if row["close"] > row["open"]: return True
    if lower_wick > body * 2 and body / total < 0.3: return True
    return False

def is_bearish_candle(row) -> bool:
    body = abs(row["close"] - row["open"])
    total = row["high"] - row["low"]
    if total == 0: return False
    upper_wick = row["high"] - max(row["open"], row["close"])
    if row["close"] < row["open"]: return True
    if upper_wick > body * 2 and body / total < 0.3: return True
    return False


# ════════════════════════════════════════════════════════════════════
# Signal Generator
# ════════════════════════════════════════════════════════════════════

def generate_signal(df: pd.DataFrame, i: int, params: dict) -> dict | None:
    if i < max(params["ema_slow"], 50) + 5:
        return None

    row = df.iloc[i]
    close = row["close"]
    high = row["high"]
    low = row["low"]
    ema_fast = df["_ema_fast"].iloc[i]
    ema_slow = df["_ema_slow"].iloc[i]
    rsi = df["_rsi"].iloc[i]
    atr = df["_atr"].iloc[i]

    if pd.isna(ema_fast) or pd.isna(ema_slow) or pd.isna(rsi) or pd.isna(atr) or atr <= 0:
        return None

    pullback_zone = atr * params["pullback_pct"]
    sl_dist = atr * params["sl_atr"]
    rr = params["rr_ratio"]
    trend_gap = abs(ema_fast - ema_slow)
    if trend_gap < atr * 0.1:
        return None

    # BUY PULLBACK
    if ema_fast > ema_slow:
        near_ema = low <= ema_fast + pullback_zone and close > ema_fast - pullback_zone
        rsi_ok = 25 <= rsi <= params["rsi_buy_max"]
        candle_ok = is_bullish_candle(row)
        above_slow = close > ema_slow
        if near_ema and rsi_ok and candle_ok and above_slow:
            sl = close - sl_dist
            tp = close + sl_dist * rr
            recent_lows = df["low"].iloc[max(0, i-5):i+1]
            sl = min(sl, recent_lows.min() - atr * 0.2)
            return {"action": "BUY", "sl": round(sl, 5), "tp": round(tp, 5),
                    "reason": f"Pullback BUY @ EMA{params['ema_fast']}"}

    # SELL REJECTION
    elif ema_fast < ema_slow:
        near_ema = high >= ema_fast - pullback_zone and close < ema_fast + pullback_zone
        rsi_ok = params["rsi_sell_min"] <= rsi <= 75
        candle_ok = is_bearish_candle(row)
        below_slow = close < ema_slow
        if near_ema and rsi_ok and candle_ok and below_slow:
            sl = close + sl_dist
            tp = close - sl_dist * rr
            recent_highs = df["high"].iloc[max(0, i-5):i+1]
            sl = max(sl, recent_highs.max() + atr * 0.2)
            return {"action": "SELL", "sl": round(sl, 5), "tp": round(tp, 5),
                    "reason": f"Rejection SELL @ EMA{params['ema_fast']}"}

    return None


# ════════════════════════════════════════════════════════════════════
# Backtester Engine
# ════════════════════════════════════════════════════════════════════

def run_backtest(df: pd.DataFrame, symbol: str, config: dict, params: dict,
                 equity_start: float = 18000.0, label_suffix: str = "") -> dict:
    contract_size = config["contract_size"]
    point = config["point"]

    df = df.copy()
    df["_ema_fast"] = calc_ema(df["close"], params["ema_fast"])
    df["_ema_slow"] = calc_ema(df["close"], params["ema_slow"])
    df["_rsi"] = calc_rsi(df["close"], 14)
    df["_atr"] = calc_atr(df, 14)

    equity = equity_start
    peak_equity = equity
    max_dd_pct = 0.0
    trades = []
    open_trade = None
    trade_id = 0
    is_cent = symbol.upper().endswith("C")
    vol_step = 0.0001 if is_cent else 0.01
    vol_min = 0.0001 if is_cent else 0.01
    risk_pct = 0.01
    warmup = max(params["ema_slow"], 50) + 10
    total_bars = len(df)

    for i in range(warmup, total_bars):
        bar = df.iloc[i]
        bar_high = bar["high"]; bar_low = bar["low"]; bar_close = bar["close"]
        bar_time = str(bar.get("time", i))

        if open_trade is not None:
            open_trade["bars"] += 1
            exit_happened = False; exit_price = 0; exit_reason = ""
            if open_trade["action"] == "BUY":
                if bar_low <= open_trade["sl"]:
                    exit_price, exit_reason, exit_happened = open_trade["sl"], "SL", True
                elif bar_high >= open_trade["tp"]:
                    exit_price, exit_reason, exit_happened = open_trade["tp"], "TP", True
                elif open_trade["bars"] >= 60:
                    exit_price, exit_reason, exit_happened = bar_close, "TIMEOUT", True
            else:
                if bar_high >= open_trade["sl"]:
                    exit_price, exit_reason, exit_happened = open_trade["sl"], "SL", True
                elif bar_low <= open_trade["tp"]:
                    exit_price, exit_reason, exit_happened = open_trade["tp"], "TP", True
                elif open_trade["bars"] >= 60:
                    exit_price, exit_reason, exit_happened = bar_close, "TIMEOUT", True

            # Break-even at +1R
            if not exit_happened and open_trade["bars"] >= 3:
                if open_trade["action"] == "BUY":
                    sd = open_trade["entry"] - open_trade["sl"]
                    if sd > 0 and bar_close >= open_trade["entry"] + sd:
                        open_trade["sl"] = open_trade["entry"] + point * 2
                else:
                    sd = open_trade["sl"] - open_trade["entry"]
                    if sd > 0 and bar_close <= open_trade["entry"] - sd:
                        open_trade["sl"] = open_trade["entry"] - point * 2

            if exit_happened:
                if open_trade["action"] == "BUY":
                    pnl = (exit_price - open_trade["entry"]) * open_trade["lot"] * contract_size
                else:
                    pnl = (open_trade["entry"] - exit_price) * open_trade["lot"] * contract_size
                equity += pnl
                trades.append({"pnl": round(pnl, 2), "action": open_trade["action"],
                               "reason": exit_reason, "bars": open_trade["bars"],
                               "entry": open_trade["entry"], "exit": exit_price})
                open_trade = None

        if equity > peak_equity: peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
        if dd > max_dd_pct: max_dd_pct = dd
        if i % 3 != 0 or open_trade is not None: continue

        signal = generate_signal(df, i, params)
        if signal is None: continue
        sl_dist = abs(bar_close - signal["sl"])
        if sl_dist <= 0: continue
        raw_lot = (equity * risk_pct) / (sl_dist * contract_size)
        lot = max(vol_min, round(raw_lot / vol_step) * vol_step)
        trade_id += 1
        open_trade = {"id": trade_id, "action": signal["action"], "entry": bar_close,
                      "sl": signal["sl"], "tp": signal["tp"], "lot": lot, "bars": 0}

    if open_trade:
        bc = df.iloc[-1]["close"]
        pnl = ((bc - open_trade["entry"]) if open_trade["action"] == "BUY"
               else (open_trade["entry"] - bc)) * open_trade["lot"] * contract_size
        equity += pnl
        trades.append({"pnl": round(pnl, 2), "action": open_trade["action"],
                       "reason": "END", "bars": open_trade["bars"],
                       "entry": open_trade["entry"], "exit": bc})

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total = len(trades)
    wr = len(wins) / total * 100 if total > 0 else 0
    tp_sum = sum(t["pnl"] for t in wins)
    tl_sum = abs(sum(t["pnl"] for t in losses))
    pf = tp_sum / tl_sum if tl_sum > 0 else (10.0 if tp_sum > 0 else 0)
    net = sum(t["pnl"] for t in trades)
    exp = net / total if total > 0 else 0
    buys = [t for t in trades if t["action"] == "BUY"]
    sells = [t for t in trades if t["action"] == "SELL"]
    b_wr = len([t for t in buys if t["pnl"] > 0]) / len(buys) * 100 if buys else 0
    s_wr = len([t for t in sells if t["pnl"] > 0]) / len(sells) * 100 if sells else 0

    return {"total": total, "wins": len(wins), "losses": len(losses),
            "wr": round(wr, 1), "pf": round(pf, 2), "net_pnl": round(net, 2),
            "max_dd": round(max_dd_pct, 2), "exp": round(exp, 2),
            "final_eq": round(equity, 2), "trades": trades,
            "buy_n": len(buys), "buy_wr": round(b_wr, 1),
            "sell_n": len(sells), "sell_wr": round(s_wr, 1)}


# ════════════════════════════════════════════════════════════════════
# Data Loader (DuckDB Parquet → MT5 fallback)
# ════════════════════════════════════════════════════════════════════

def load_data(symbol: str, days: int) -> pd.DataFrame | None:
    """Load M5 candles: try parquet first, then MT5."""

    pq_path = Path(f"data/exports/mtf/{symbol}_M5.parquet")

    # ── Try Parquet ──
    if pq_path.exists():
        print(f"📦 Loading from Parquet: {pq_path}...", end=" ", flush=True)
        try:
            import duckdb
            conn = duckdb.connect(":memory:")
            safe = str(pq_path).replace("\\", "/")
            df = conn.execute(f"SELECT * FROM read_parquet('{safe}') ORDER BY time ASC").df()
            conn.close()

            if "time" in df.columns:
                if not pd.api.types.is_datetime64_any_dtype(df["time"]):
                    df["time"] = pd.to_datetime(df["time"], unit="s", errors="coerce")

            # Filter to requested days
            if days and len(df) > 0:
                cutoff = df["time"].max() - timedelta(days=days)
                df = df[df["time"] >= cutoff].reset_index(drop=True)

            if len(df) >= 500:
                print(f"✅ {len(df)} bars ({df['time'].iloc[0]} → {df['time'].iloc[-1]})")
                return df
            else:
                print(f"⚠️ Only {len(df)} bars in parquet (need ≥500)")
        except Exception as e:
            print(f"⚠️ Parquet error: {e}")

    # ── Fallback: MT5 ──
    print(f"📡 Loading from MT5 ({days} days)...", end=" ", flush=True)
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            print("❌ MT5 init failed")
            return None
        utc_to = datetime.now(timezone.utc)
        utc_from = utc_to - timedelta(days=days)
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, utc_from, utc_to)
        mt5.shutdown()
        if rates is None or len(rates) == 0:
            print("❌ No data from MT5")
            return None
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        print(f"✅ {len(df)} bars ({df['time'].iloc[0]} → {df['time'].iloc[-1]})")
        return df
    except Exception as e:
        print(f"❌ MT5 error: {e}")
        return None


# ════════════════════════════════════════════════════════════════════
# Walk-Forward Validation
# ════════════════════════════════════════════════════════════════════

def walk_forward(df: pd.DataFrame, symbol: str, config: dict, equity: float, train_ratio: float = 0.8):
    """Split data → train on 80% → test on 20% → detect overfitting."""
    split_idx = int(len(df) * train_ratio)
    df_train = df.iloc[:split_idx].reset_index(drop=True)
    df_test = df.iloc[split_idx:].reset_index(drop=True)

    print(f"\n{'='*110}")
    print(f"📚 WALK-FORWARD: Train {len(df_train)} bars | Test {len(df_test)} bars | Split {train_ratio*100:.0f}/{(1-train_ratio)*100:.0f}")
    print(f"   Train: {df_train['time'].iloc[0]} → {df_train['time'].iloc[-1]}")
    print(f"   Test:  {df_test['time'].iloc[0]} → {df_test['time'].iloc[-1]}")
    print(f"{'='*110}")

    # ── Phase 1: Train ──
    print(f"\n🏋️ TRAINING PHASE ({len(df_train)} bars)")
    print(f"{'─'*110}")
    train_results = []
    for idx, params in enumerate(PARAM_GRID, 1):
        label = params.get("label", f"SET_{idx}")
        t0 = time.monotonic()
        r = run_backtest(df_train, symbol, config, params, equity_start=equity)
        elapsed = time.monotonic() - t0
        is_live = r["wr"] >= 50 and r["pf"] >= 1.3 and r["max_dd"] <= 6 and r["total"] >= 10
        icon = "🟢" if is_live else ("⭐" if r["wr"] >= 50 else "")
        print(f"  [{idx}/{len(PARAM_GRID)}] {label:<22s} N={r['total']:>4} WR={r['wr']:>5.1f}% "
              f"PF={r['pf']:>5.2f} P&L=${r['net_pnl']:>+9.2f} DD={r['max_dd']:>4.1f}% ({elapsed:.1f}s) {icon}")
        train_results.append((params, r))

    # Sort by PF * WR
    train_results.sort(key=lambda x: x[1]["pf"] * x[1]["wr"], reverse=True)
    best_params = train_results[0][0]
    best_train = train_results[0][1]

    print(f"\n🏆 Best Training Config: {best_params.get('label','?')} "
          f"→ WR={best_train['wr']}% PF={best_train['pf']} P&L=${best_train['net_pnl']:+.2f}")

    # ── Phase 2: Test ──
    print(f"\n🧪 TESTING PHASE ({len(df_test)} bars)")
    print(f"{'─'*110}")

    # Test top 3 configs
    top_configs = train_results[:3]
    test_results = []
    for idx, (params, train_r) in enumerate(top_configs, 1):
        label = params.get("label", f"SET_{idx}")
        t0 = time.monotonic()
        r = run_backtest(df_test, symbol, config, params, equity_start=equity)
        elapsed = time.monotonic() - t0
        is_live = r["wr"] >= 50 and r["pf"] >= 1.3 and r["max_dd"] <= 6 and r["total"] >= 5
        icon = "🟢" if is_live else ""
        overfit = "⚠️ OVERFIT" if train_r["wr"] > r["wr"] + 15 or (train_r["pf"] > r["pf"] * 2) else ""
        print(f"  [{idx}/3] {label:<22s} N={r['total']:>4} WR={r['wr']:>5.1f}% "
              f"PF={r['pf']:>5.2f} P&L=${r['net_pnl']:>+9.2f} DD={r['max_dd']:>4.1f}% "
              f"({elapsed:.1f}s) {icon} {overfit}")
        print(f"         Train: WR={train_r['wr']}% PF={train_r['pf']} → Test: WR={r['wr']}% PF={r['pf']}")
        test_results.append((params, train_r, r))

    return train_results, test_results


# ════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Pullback-Rejection 1Y Backtest (DuckDB)")
    parser.add_argument("--symbol", default="XAUUSDc", choices=list(SYMBOL_CONFIGS.keys()))
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--equity", type=float, default=18000.0)
    args = parser.parse_args()

    symbol = args.symbol
    days = args.days
    config = SYMBOL_CONFIGS[symbol]

    print("=" * 110)
    print(f"🧠 AI PULLBACK-REJECTION TRAINER — {config['name']} ({symbol})")
    print(f"   Period: {days} days | TF: M5 | Equity: ${args.equity:,.0f} | Configs: {len(PARAM_GRID)}")
    print("=" * 110)

    # ── Load Data ──
    df = load_data(symbol, days)
    if df is None or len(df) < 500:
        print("❌ Insufficient data")
        return

    total_days = (df["time"].iloc[-1] - df["time"].iloc[0]).days
    print(f"   Data range: {total_days} days | {len(df)} M5 bars")

    # ── Full Backtest ──
    print(f"\n{'='*110}")
    print(f"📊 FULL PERIOD BACKTEST ({len(df)} bars)")
    print(f"{'='*110}")

    full_results = []
    for idx, params in enumerate(PARAM_GRID, 1):
        label = params.get("label", f"SET_{idx}")
        t0 = time.monotonic()
        r = run_backtest(df, symbol, config, params, equity_start=args.equity)
        elapsed = time.monotonic() - t0
        is_live = r["wr"] >= 50 and r["pf"] >= 1.3 and r["max_dd"] <= 6 and r["total"] >= 20
        icon = "🟢" if is_live else ("⭐" if r["wr"] >= 50 else "")
        print(f"  [{idx}/{len(PARAM_GRID)}] {label:<22s} N={r['total']:>4} WR={r['wr']:>5.1f}% "
              f"PF={r['pf']:>5.2f} P&L=${r['net_pnl']:>+9.2f} DD={r['max_dd']:>4.1f}% "
              f"B={r['buy_n']}({r['buy_wr']:.0f}%) S={r['sell_n']}({r['sell_wr']:.0f}%) ({elapsed:.1f}s) {icon}")
        full_results.append((params, r))

    # ── Summary Table ──
    sorted_results = sorted(full_results, key=lambda x: (x[1]["pf"] * x[1]["wr"], x[1]["net_pnl"]), reverse=True)

    print(f"\n{'='*130}")
    print(f"  {'Config':<22s}│{'N':>4}│{'W':>4}│{'L':>4}│{'WR%':>6}│{'PF':>5}│{'Net P&L':>11}│{'DD%':>5}│"
          f"{'Exp':>7}│{'BUY':>6}│{'B_WR':>5}│{'SELL':>5}│{'S_WR':>5}│ Status")
    print("─" * 130)

    best = None
    for params, r in sorted_results:
        label = params.get("label", "?")
        is_live = r["wr"] >= 50 and r["pf"] >= 1.3 and r["max_dd"] <= 6 and r["total"] >= 20
        status = "🟢 LIVE" if is_live else ""
        if is_live and best is None:
            best = (params, r)
            prefix = ">>> "
        else:
            prefix = "    "
        print(f"{prefix}{label:<18s}│{r['total']:>4}│{r['wins']:>4}│{r['losses']:>4}│"
              f"{r['wr']:>5.1f}%│{r['pf']:>5.2f}│${r['net_pnl']:>+9.2f}│{r['max_dd']:>4.1f}%│"
              f"${r['exp']:>+5.02f}│{r['buy_n']:>5}B│{r['buy_wr']:>4.0f}%│"
              f"{r['sell_n']:>4}S│{r['sell_wr']:>4.0f}%│ {status}")
    print("=" * 130)

    # ── Walk-Forward Validation ──
    if len(df) >= 2000:
        train_results, test_results = walk_forward(df, symbol, config, args.equity)

        # ── Final Summary ──
        print(f"\n{'='*110}")
        print("📋 FINAL SUMMARY")
        print(f"{'='*110}")

        if best:
            bp, br = best
            print(f"  🏆 Best Full-Period: {bp.get('label','?')} "
                  f"WR={br['wr']}% PF={br['pf']} P&L=${br['net_pnl']:+.2f} DD={br['max_dd']}%")
        else:
            print(f"  ⚠️  No config met live-ready criteria on full period")

        for params, train_r, test_r in test_results:
            label = params.get("label", "?")
            wr_delta = test_r["wr"] - train_r["wr"]
            pf_delta = test_r["pf"] - train_r["pf"]
            verdict = "✅ CONSISTENT" if abs(wr_delta) < 10 and abs(pf_delta) < 0.5 else "⚠️ OVERFIT"
            print(f"  [{label}] Train WR={train_r['wr']}%/PF={train_r['pf']} → "
                  f"Test WR={test_r['wr']}%/PF={test_r['pf']} → {verdict}")

        # ── Recommendation ──
        consistent = [(p, tr, te) for p, tr, te in test_results
                       if abs(te["wr"] - tr["wr"]) < 10 and te["pf"] >= 1.0]
        if consistent:
            bp, btr, bte = consistent[0]
            print(f"\n  🎯 RECOMMENDED CONFIG: {bp.get('label','?')}")
            print(f"     Train: WR={btr['wr']}% PF={btr['pf']} | Test: WR={bte['wr']}% PF={bte['pf']}")
            print(f"     Parameters:")
            for k, v in bp.items():
                if k == "label": continue
                print(f"       {k} = {v}")
        else:
            print(f"\n  ⚠️  No consistent config found — strategy may need redesign for this period")

    elif best:
        bp, br = best
        print(f"\n🏆 BEST CONFIG: {bp.get('label','?')} "
              f"WR={br['wr']}% PF={br['pf']} P&L=${br['net_pnl']:+.2f} DD={br['max_dd']}%")
    else:
        print(f"\n⚠️ No live-ready config found")

    print(f"\n✅ Pullback-Rejection 1Y Backtest Complete — {symbol}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⛔ Cancelled by user.")
    except Exception:
        import traceback
        traceback.print_exc()
