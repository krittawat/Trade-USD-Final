"""
Smart Pullback-Rejection Backtest Strategy
=============================================
ฉลาด BUY ตอน Pullback (ราคาย่อลงมาหา EMA ใน uptrend)
ฉลาด SELL เมื่อ Rejection (ราคาโดนปฏิเสธที่ EMA/resistance ใน downtrend)

Logic:
    TREND: EMA21 > EMA50 = Uptrend, EMA21 < EMA50 = Downtrend
    BUY PULLBACK:
        - Uptrend (EMA21 > EMA50)
        - Price pulls back to EMA21 zone (low touches or near EMA21)
        - RSI(14) 30-50 (oversold but not crashed)
        - Bullish candle confirmation (close > open, or hammer)
        - SL = below EMA50 or swing low
        - TP = 2R or recent high

    SELL REJECTION:
        - Downtrend (EMA21 < EMA50)
        - Price rises to EMA21 zone (high touches or near EMA21)
        - RSI(14) 50-70 (overbought in downtrend)
        - Bearish candle confirmation (close < open, or shooting star)
        - SL = above EMA50 or swing high
        - TP = 2R or recent low

Usage:
    cd d:\\VibeCode\\Trade\\backend
    $env:PYTHONPATH="."; python scripts/backtest_pullback.py
    $env:PYTHONPATH="."; python scripts/backtest_pullback.py --symbol XAGUSDc --days 30
"""
print("🎯 Smart Pullback-Rejection Backtest — Starting...", flush=True)

import sys
import argparse
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.disable(logging.CRITICAL)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

# ── Symbol Configs ──
SYMBOL_CONFIGS = {
    "XAUUSDc": {"contract_size": 100.0, "point": 0.01, "digits": 2, "atr_sl_mult": 1.5, "name": "Gold"},
    "XAGUSDc": {"contract_size": 5000.0, "point": 0.001, "digits": 3, "atr_sl_mult": 1.5, "name": "Silver"},
    "BTCUSDc": {"contract_size": 1.0, "point": 0.01, "digits": 2, "atr_sl_mult": 2.0, "name": "Bitcoin"},
}

# ── Parameter Grid ──
PARAM_GRID = [
    # Standard configurations
    {"label": "EMA21_50_RR2",   "ema_fast": 21, "ema_slow": 50, "rr_ratio": 2.0, "sl_atr": 1.5, "pullback_pct": 0.3, "rsi_buy_max": 50, "rsi_sell_min": 50},
    {"label": "EMA21_50_RR1.5", "ema_fast": 21, "ema_slow": 50, "rr_ratio": 1.5, "sl_atr": 1.5, "pullback_pct": 0.3, "rsi_buy_max": 50, "rsi_sell_min": 50},
    {"label": "EMA21_50_RR3",   "ema_fast": 21, "ema_slow": 50, "rr_ratio": 3.0, "sl_atr": 1.5, "pullback_pct": 0.3, "rsi_buy_max": 45, "rsi_sell_min": 55},
    # Tight EMA (sensitive)
    {"label": "EMA10_21_RR2",   "ema_fast": 10, "ema_slow": 21, "rr_ratio": 2.0, "sl_atr": 1.2, "pullback_pct": 0.4, "rsi_buy_max": 50, "rsi_sell_min": 50},
    {"label": "EMA10_21_RR1.5", "ema_fast": 10, "ema_slow": 21, "rr_ratio": 1.5, "sl_atr": 1.2, "pullback_pct": 0.4, "rsi_buy_max": 55, "rsi_sell_min": 45},
    # Wide EMA (conservative)
    {"label": "EMA21_100_RR2",  "ema_fast": 21, "ema_slow": 100, "rr_ratio": 2.0, "sl_atr": 2.0, "pullback_pct": 0.2, "rsi_buy_max": 45, "rsi_sell_min": 55},
    {"label": "EMA21_100_RR3",  "ema_fast": 21, "ema_slow": 100, "rr_ratio": 3.0, "sl_atr": 2.0, "pullback_pct": 0.2, "rsi_buy_max": 45, "rsi_sell_min": 55},
    # Wide SL
    {"label": "EMA21_50_WS2",   "ema_fast": 21, "ema_slow": 50, "rr_ratio": 2.0, "sl_atr": 2.0, "pullback_pct": 0.3, "rsi_buy_max": 50, "rsi_sell_min": 50},
]


# ════════════════════════════════════════════════════════════════════
# Indicator Functions
# ════════════════════════════════════════════════════════════════════

def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift(1)).abs()
    low_close = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()

def is_bullish_candle(row) -> bool:
    """Bullish: close > open, or hammer (small body + long lower wick)."""
    body = abs(row["close"] - row["open"])
    total = row["high"] - row["low"]
    if total == 0:
        return False
    lower_wick = min(row["open"], row["close"]) - row["low"]
    if row["close"] > row["open"]:
        return True
    # Hammer: lower wick > 2x body
    if lower_wick > body * 2 and body / total < 0.3:
        return True
    return False

def is_bearish_candle(row) -> bool:
    """Bearish: close < open, or shooting star (small body + long upper wick)."""
    body = abs(row["close"] - row["open"])
    total = row["high"] - row["low"]
    if total == 0:
        return False
    upper_wick = row["high"] - max(row["open"], row["close"])
    if row["close"] < row["open"]:
        return True
    # Shooting star: upper wick > 2x body
    if upper_wick > body * 2 and body / total < 0.3:
        return True
    return False


# ════════════════════════════════════════════════════════════════════
# Pullback-Rejection Signal Generator
# ════════════════════════════════════════════════════════════════════

def generate_signal(df: pd.DataFrame, i: int, params: dict) -> dict | None:
    """
    Generate BUY/SELL signal based on pullback/rejection logic.

    Returns:
        {"action": "BUY"/"SELL", "sl": float, "tp": float, "reason": str}
        or None if no signal
    """
    if i < max(params["ema_slow"], 50) + 5:
        return None

    row = df.iloc[i]
    prev = df.iloc[i - 1]
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

    # ── Trend Strength Check ──
    trend_gap = abs(ema_fast - ema_slow)
    if trend_gap < atr * 0.1:
        return None  # No clear trend

    # ═══════════════════════════════
    # BUY PULLBACK (Uptrend)
    # ═══════════════════════════════
    if ema_fast > ema_slow:
        # Price pulled back to EMA zone
        near_ema = low <= ema_fast + pullback_zone and close > ema_fast - pullback_zone
        # RSI not overbought (room to go up)
        rsi_ok = 25 <= rsi <= params["rsi_buy_max"]
        # Bullish candle confirmation
        candle_ok = is_bullish_candle(row)
        # Price above EMA50 (still in uptrend)
        above_slow = close > ema_slow

        if near_ema and rsi_ok and candle_ok and above_slow:
            sl = close - sl_dist
            tp = close + sl_dist * rr

            # Extra: swing low SL (use lowest low of last 5 bars)
            recent_lows = df["low"].iloc[max(0, i-5):i+1]
            swing_low = recent_lows.min()
            sl = min(sl, swing_low - atr * 0.2)

            return {
                "action": "BUY",
                "sl": round(sl, 5),
                "tp": round(tp, 5),
                "reason": f"Pullback BUY @ EMA{params['ema_fast']} | RSI={rsi:.0f} | ATR={atr:.2f}",
            }

    # ═══════════════════════════════
    # SELL REJECTION (Downtrend)
    # ═══════════════════════════════
    elif ema_fast < ema_slow:
        # Price rose to EMA zone
        near_ema = high >= ema_fast - pullback_zone and close < ema_fast + pullback_zone
        # RSI not oversold (room to go down)
        rsi_ok = params["rsi_sell_min"] <= rsi <= 75
        # Bearish candle confirmation
        candle_ok = is_bearish_candle(row)
        # Price below EMA50 (still in downtrend)
        below_slow = close < ema_slow

        if near_ema and rsi_ok and candle_ok and below_slow:
            sl = close + sl_dist
            tp = close - sl_dist * rr

            # Extra: swing high SL
            recent_highs = df["high"].iloc[max(0, i-5):i+1]
            swing_high = recent_highs.max()
            sl = max(sl, swing_high + atr * 0.2)

            return {
                "action": "SELL",
                "sl": round(sl, 5),
                "tp": round(tp, 5),
                "reason": f"Rejection SELL @ EMA{params['ema_fast']} | RSI={rsi:.0f} | ATR={atr:.2f}",
            }

    return None


# ════════════════════════════════════════════════════════════════════
# Backtester
# ════════════════════════════════════════════════════════════════════

def run_backtest(df: pd.DataFrame, symbol: str, config: dict, params: dict, equity_start: float = 10000.0) -> dict:
    """Run a single pullback-rejection backtest."""

    contract_size = config["contract_size"]
    point = config["point"]

    # Pre-compute indicators
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
    risk_pct = 0.01  # 1% risk per trade

    total_bars = len(df)
    warmup = max(params["ema_slow"], 50) + 10

    for i in range(warmup, total_bars):
        bar = df.iloc[i]
        bar_high = bar["high"]
        bar_low = bar["low"]
        bar_close = bar["close"]
        bar_time = str(bar.get("time", i))

        # ── Manage open trade ──
        if open_trade is not None:
            open_trade["bars"] += 1
            exit_happened = False
            exit_price = 0
            exit_reason = ""

            if open_trade["action"] == "BUY":
                if bar_low <= open_trade["sl"]:
                    exit_price = open_trade["sl"]
                    exit_reason = "SL"
                    exit_happened = True
                elif bar_high >= open_trade["tp"]:
                    exit_price = open_trade["tp"]
                    exit_reason = "TP"
                    exit_happened = True
                elif open_trade["bars"] >= 60:  # 5 hour timeout
                    exit_price = bar_close
                    exit_reason = "TIMEOUT"
                    exit_happened = True
            else:  # SELL
                if bar_high >= open_trade["sl"]:
                    exit_price = open_trade["sl"]
                    exit_reason = "SL"
                    exit_happened = True
                elif bar_low <= open_trade["tp"]:
                    exit_price = open_trade["tp"]
                    exit_reason = "TP"
                    exit_happened = True
                elif open_trade["bars"] >= 60:
                    exit_price = bar_close
                    exit_reason = "TIMEOUT"
                    exit_happened = True

            # Break-even at +1R
            if not exit_happened and open_trade["bars"] >= 3:
                if open_trade["action"] == "BUY":
                    sl_dist = open_trade["entry"] - open_trade["sl"]
                    if sl_dist > 0 and bar_close >= open_trade["entry"] + sl_dist:
                        open_trade["sl"] = open_trade["entry"] + point * 2  # BE + 2 points
                else:
                    sl_dist = open_trade["sl"] - open_trade["entry"]
                    if sl_dist > 0 and bar_close <= open_trade["entry"] - sl_dist:
                        open_trade["sl"] = open_trade["entry"] - point * 2

            if exit_happened:
                if open_trade["action"] == "BUY":
                    pnl = (exit_price - open_trade["entry"]) * open_trade["lot"] * contract_size
                else:
                    pnl = (open_trade["entry"] - exit_price) * open_trade["lot"] * contract_size

                equity += pnl
                trades.append({
                    "id": open_trade["id"],
                    "action": open_trade["action"],
                    "entry": open_trade["entry"],
                    "exit": exit_price,
                    "pnl": round(pnl, 2),
                    "reason": exit_reason,
                    "bars": open_trade["bars"],
                    "signal_reason": open_trade["signal_reason"],
                    "entry_time": open_trade["entry_time"],
                    "exit_time": bar_time,
                })
                open_trade = None

        # ── Track drawdown ──
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
        if dd > max_dd_pct:
            max_dd_pct = dd

        # ── Signal check (every 3 bars to avoid over-trading) ──
        if i % 3 != 0:
            continue
        if open_trade is not None:
            continue

        signal = generate_signal(df, i, params)
        if signal is None:
            continue

        # ── Position sizing ──
        sl_distance = abs(bar_close - signal["sl"])
        if sl_distance <= 0:
            continue

        risk_amount = equity * risk_pct
        raw_lot = risk_amount / (sl_distance * contract_size)
        lot = max(vol_min, round(raw_lot / vol_step) * vol_step)

        trade_id += 1
        open_trade = {
            "id": trade_id,
            "action": signal["action"],
            "entry": bar_close,
            "sl": signal["sl"],
            "tp": signal["tp"],
            "lot": lot,
            "bars": 0,
            "signal_reason": signal["reason"],
            "entry_time": bar_time,
        }

    # Close at end
    if open_trade is not None:
        bar_close = df.iloc[-1]["close"]
        if open_trade["action"] == "BUY":
            pnl = (bar_close - open_trade["entry"]) * open_trade["lot"] * contract_size
        else:
            pnl = (open_trade["entry"] - bar_close) * open_trade["lot"] * contract_size
        equity += pnl
        trades.append({
            "id": open_trade["id"], "action": open_trade["action"],
            "entry": open_trade["entry"], "exit": bar_close,
            "pnl": round(pnl, 2), "reason": "END", "bars": open_trade["bars"],
            "signal_reason": open_trade["signal_reason"],
            "entry_time": open_trade["entry_time"], "exit_time": str(df.iloc[-1]["time"]),
        })

    # ── Compute stats ──
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total = len(trades)
    wr = len(wins) / total * 100 if total > 0 else 0
    total_profit = sum(t["pnl"] for t in wins) if wins else 0
    total_loss = abs(sum(t["pnl"] for t in losses)) if losses else 0
    pf = total_profit / total_loss if total_loss > 0 else (10.0 if total_profit > 0 else 0)
    net_pnl = sum(t["pnl"] for t in trades)
    expectancy = net_pnl / total if total > 0 else 0
    avg_win = total_profit / len(wins) if wins else 0
    avg_loss_val = total_loss / len(losses) if losses else 0

    buys = [t for t in trades if t["action"] == "BUY"]
    sells = [t for t in trades if t["action"] == "SELL"]
    buy_wr = len([t for t in buys if t["pnl"] > 0]) / len(buys) * 100 if buys else 0
    sell_wr = len([t for t in sells if t["pnl"] > 0]) / len(sells) * 100 if sells else 0

    return {
        "trades": trades,
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "wr": round(wr, 1),
        "pf": round(pf, 2),
        "net_pnl": round(net_pnl, 2),
        "max_dd": round(max_dd_pct, 2),
        "expectancy": round(expectancy, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss_val, 2),
        "final_equity": round(equity, 2),
        "buy_count": len(buys),
        "buy_wr": round(buy_wr, 1),
        "sell_count": len(sells),
        "sell_wr": round(sell_wr, 1),
    }


# ════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Pullback-Rejection Backtest")
    parser.add_argument("--symbol", default="XAUUSDc", choices=list(SYMBOL_CONFIGS.keys()))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--equity", type=float, default=18000.0)
    args = parser.parse_args()

    symbol = args.symbol
    days = args.days
    config = SYMBOL_CONFIGS[symbol]

    print("=" * 110)
    print(f"🎯 SMART PULLBACK-REJECTION BACKTEST — {config['name']} ({symbol})")
    print(f"   Days: {days} | TF: M5 | Initial Equity: ${args.equity:,.0f} | Configs: {len(PARAM_GRID)}")
    print("=" * 110)

    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return

    # ── Fetch M5 data ──
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    print(f"📊 Fetching M5 data ({days} days)...", end=" ", flush=True)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        print(f"❌ No M5 data for {symbol}")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"✅ {len(df)} bars ({df['time'].iloc[0]} → {df['time'].iloc[-1]})")
    print()

    # ── Run Grid ──
    results = []
    for idx, params in enumerate(PARAM_GRID, 1):
        label = params.get("label", f"SET_{idx}")
        print(f"  [{idx}/{len(PARAM_GRID)}] {label}...", end=" ", flush=True)
        t0 = time.monotonic()
        result = run_backtest(df, symbol, config, params, equity_start=args.equity)
        elapsed = time.monotonic() - t0

        # Live-ready check: WR≥50%, PF≥1.3, DD≤6%
        is_live = result["wr"] >= 50 and result["pf"] >= 1.3 and result["max_dd"] <= 6 and result["total"] >= 10
        icon = "🟢" if is_live else ("⭐" if result["wr"] >= 50 else "")
        print(f"N={result['total']:>3} WR={result['wr']:>5.1f}% PF={result['pf']:>5.2f} "
              f"P&L=${result['net_pnl']:>+8.2f} DD={result['max_dd']:>4.1f}% ({elapsed:.1f}s) {icon}")
        results.append((params, result))

    # ── Summary Table ──
    print()
    print("=" * 130)
    print(f"  {'Config':<22s}│{'N':>4}│{'W':>4}│{'L':>4}│{'WR%':>6}│{'PF':>5}│{'Net P&L':>11}│{'DD%':>5}│"
          f"{'Exp':>7}│{'BUY':>6}│{'B_WR':>5}│{'SELL':>5}│{'S_WR':>5}│ Status")
    print("─" * 130)

    sorted_results = sorted(results, key=lambda x: (x[1]["pf"] * x[1]["wr"], x[1]["net_pnl"]), reverse=True)

    best = None
    for params, r in sorted_results:
        label = params.get("label", "?")
        is_live = r["wr"] >= 50 and r["pf"] >= 1.3 and r["max_dd"] <= 6 and r["total"] >= 10
        status = "🟢 LIVE" if is_live else ""
        if is_live and best is None:
            best = (params, r)
            prefix = ">>> "
        else:
            prefix = "    "

        print(f"{prefix}{label:<18s}│{r['total']:>4}│{r['wins']:>4}│{r['losses']:>4}│"
              f"{r['wr']:>5.1f}%│{r['pf']:>5.2f}│${r['net_pnl']:>+9.2f}│{r['max_dd']:>4.1f}%│"
              f"${r['expectancy']:>+5.2f}│{r['buy_count']:>5}B│{r['buy_wr']:>4.0f}%│"
              f"{r['sell_count']:>4}S│{r['sell_wr']:>4.0f}%│ {status}")

    print("=" * 130)

    # ── Winner Details ──
    if best:
        bp, br = best
        print()
        print("🏆 BEST LIVE-READY CONFIGURATION")
        print(f"   Config: {bp.get('label', '?')}")
        print(f"   Win Rate: {br['wr']}% | Profit Factor: {br['pf']}")
        print(f"   Net P&L: ${br['net_pnl']:+.2f} | Max DD: {br['max_dd']}%")
        print(f"   Trades: {br['total']} ({br['wins']}W / {br['losses']}L)")
        print(f"   BUY: {br['buy_count']} trades (WR={br['buy_wr']:.0f}%) | SELL: {br['sell_count']} trades (WR={br['sell_wr']:.0f}%)")
        print(f"   Avg Win: ${br['avg_win']:.2f} | Avg Loss: ${br['avg_loss']:.2f}")
        print(f"   Expectancy: ${br['expectancy']:+.2f}/trade")
        print()
        print("   Parameters:")
        for k, v in bp.items():
            if k == "label":
                continue
            print(f"     {k} = {v}")

        # Show last 10 trades
        last_trades = br["trades"][-10:]
        if last_trades:
            print()
            print("   Last 10 Trades:")
            for t in last_trades:
                icon = "✅" if t["pnl"] > 0 else "❌"
                print(f"     {icon} {t['action']} @ {t['entry']:.2f} → {t['exit']:.2f} "
                      f"| P&L=${t['pnl']:+.2f} | {t['reason']} | {t['bars']}bars | {t['signal_reason']}")
    else:
        best_wr = max(results, key=lambda x: x[1]["wr"])
        best_pnl = max(results, key=lambda x: x[1]["net_pnl"])
        print()
        print("⚠️  No config met live-ready criteria (WR≥50%, PF≥1.3, DD≤6%, N≥10)")
        print(f"   Highest WR:  {best_wr[0].get('label','?')} — {best_wr[1]['wr']}% WR, PF={best_wr[1]['pf']}, ${best_wr[1]['net_pnl']:+.2f}")
        print(f"   Highest P&L: {best_pnl[0].get('label','?')} — {best_pnl[1]['wr']}% WR, PF={best_pnl[1]['pf']}, ${best_pnl[1]['net_pnl']:+.2f}")

    mt5.shutdown()
    print()
    print(f"✅ Pullback-Rejection Backtest Complete — {symbol}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⛔ Cancelled by user.")
    except Exception:
        import traceback
        traceback.print_exc()
