"""
GBPUSDc Backtest Optimization — Grid search for best WR + Profitability.
========================================================================
Strategy: GbpSessionBreakoutStrategy (Asia Range Breakout in London/NY)
Target:   Win Rate > 50%, Profit Factor > 1.0, Net Profit > 0

Grid Parameters:
    - SL_ATR_BUFFER:   SL buffer beyond Asia range (ATR multiplier)
    - TP_RANGE_MULT:   TP as multiple of Asia range distance
    - ADX_MIN:         Min ADX for confirmed breakout
    - MIN_CONFIDENCE:  Minimum confidence threshold

Usage:
    python backend/scripts/backtest_gbpusd.py
"""

print("🇬🇧 GBPUSDc Optimization — Starting...", flush=True)

import sys
import os
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.disable(logging.CRITICAL)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

from app.core.config import get_settings
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile

settings = get_settings()

# ─── Symbol Profile ───
SYMBOL = "GBPUSDc"
CONTRACT_SIZE = 100_000.0
POINT = 0.00001
DIGITS = 5
DAYS = 200
INITIAL_EQUITY = 70.0
STRATEGY_NAME = "gbp_session_breakout"


# ─── Curated Parameter Grid (~30 configs) ───
PARAM_GRID = [
    # --- Conservative (Tight TP = High WR) ---
    {"label": "CONSERVATIVE_1",   "SL_ATR_BUFFER": 0.5, "TP_RANGE_MULT": 1.0, "ADX_MIN": 18, "MIN_CONFIDENCE": 0.50},
    {"label": "CONSERVATIVE_2",   "SL_ATR_BUFFER": 0.5, "TP_RANGE_MULT": 1.0, "ADX_MIN": 15, "MIN_CONFIDENCE": 0.45},
    {"label": "CONSERVATIVE_3",   "SL_ATR_BUFFER": 0.8, "TP_RANGE_MULT": 1.0, "ADX_MIN": 18, "MIN_CONFIDENCE": 0.50},
    {"label": "CONSERVATIVE_4",   "SL_ATR_BUFFER": 1.0, "TP_RANGE_MULT": 1.0, "ADX_MIN": 15, "MIN_CONFIDENCE": 0.45},

    # --- Balanced (TP=1.25-1.5× range) ---
    {"label": "BALANCED_1",       "SL_ATR_BUFFER": 0.5, "TP_RANGE_MULT": 1.25, "ADX_MIN": 18, "MIN_CONFIDENCE": 0.50},
    {"label": "BALANCED_2",       "SL_ATR_BUFFER": 0.5, "TP_RANGE_MULT": 1.5,  "ADX_MIN": 18, "MIN_CONFIDENCE": 0.55},
    {"label": "BALANCED_3",       "SL_ATR_BUFFER": 0.8, "TP_RANGE_MULT": 1.25, "ADX_MIN": 15, "MIN_CONFIDENCE": 0.50},
    {"label": "BALANCED_4",       "SL_ATR_BUFFER": 0.8, "TP_RANGE_MULT": 1.5,  "ADX_MIN": 18, "MIN_CONFIDENCE": 0.50},
    {"label": "BALANCED_5",       "SL_ATR_BUFFER": 0.5, "TP_RANGE_MULT": 1.5,  "ADX_MIN": 15, "MIN_CONFIDENCE": 0.45},
    {"label": "BALANCED_6",       "SL_ATR_BUFFER": 0.3, "TP_RANGE_MULT": 1.25, "ADX_MIN": 18, "MIN_CONFIDENCE": 0.50},

    # --- Aggressive (Wider TP for bigger wins) ---
    {"label": "AGGRESSIVE_1",     "SL_ATR_BUFFER": 0.5, "TP_RANGE_MULT": 2.0,  "ADX_MIN": 22, "MIN_CONFIDENCE": 0.55},
    {"label": "AGGRESSIVE_2",     "SL_ATR_BUFFER": 0.8, "TP_RANGE_MULT": 2.0,  "ADX_MIN": 18, "MIN_CONFIDENCE": 0.50},
    {"label": "AGGRESSIVE_3",     "SL_ATR_BUFFER": 0.5, "TP_RANGE_MULT": 2.5,  "ADX_MIN": 22, "MIN_CONFIDENCE": 0.55},
    {"label": "AGGRESSIVE_4",     "SL_ATR_BUFFER": 1.0, "TP_RANGE_MULT": 2.0,  "ADX_MIN": 18, "MIN_CONFIDENCE": 0.50},

    # --- Loose Filter (More trades, lower confidence) ---
    {"label": "LOOSE_1",          "SL_ATR_BUFFER": 0.5, "TP_RANGE_MULT": 1.25, "ADX_MIN": 15, "MIN_CONFIDENCE": 0.45},
    {"label": "LOOSE_2",          "SL_ATR_BUFFER": 0.3, "TP_RANGE_MULT": 1.0,  "ADX_MIN": 15, "MIN_CONFIDENCE": 0.45},
    {"label": "LOOSE_3",          "SL_ATR_BUFFER": 0.8, "TP_RANGE_MULT": 1.5,  "ADX_MIN": 15, "MIN_CONFIDENCE": 0.45},
    {"label": "LOOSE_4",          "SL_ATR_BUFFER": 0.5, "TP_RANGE_MULT": 1.5,  "ADX_MIN": 15, "MIN_CONFIDENCE": 0.40},

    # --- Strict Filter (Fewer but higher quality trades) ---
    {"label": "STRICT_1",         "SL_ATR_BUFFER": 0.5, "TP_RANGE_MULT": 1.25, "ADX_MIN": 25, "MIN_CONFIDENCE": 0.60},
    {"label": "STRICT_2",         "SL_ATR_BUFFER": 0.8, "TP_RANGE_MULT": 1.5,  "ADX_MIN": 25, "MIN_CONFIDENCE": 0.60},
    {"label": "STRICT_3",         "SL_ATR_BUFFER": 0.5, "TP_RANGE_MULT": 2.0,  "ADX_MIN": 25, "MIN_CONFIDENCE": 0.55},
    {"label": "STRICT_4",         "SL_ATR_BUFFER": 1.0, "TP_RANGE_MULT": 1.5,  "ADX_MIN": 22, "MIN_CONFIDENCE": 0.55},

    # --- Wide SL Protection ---
    {"label": "WIDE_SL_1",        "SL_ATR_BUFFER": 1.0, "TP_RANGE_MULT": 1.25, "ADX_MIN": 18, "MIN_CONFIDENCE": 0.50},
    {"label": "WIDE_SL_2",        "SL_ATR_BUFFER": 1.0, "TP_RANGE_MULT": 1.5,  "ADX_MIN": 15, "MIN_CONFIDENCE": 0.50},
    {"label": "WIDE_SL_3",        "SL_ATR_BUFFER": 1.0, "TP_RANGE_MULT": 2.0,  "ADX_MIN": 15, "MIN_CONFIDENCE": 0.45},

    # --- Tight SL ---
    {"label": "TIGHT_SL_1",       "SL_ATR_BUFFER": 0.3, "TP_RANGE_MULT": 1.5,  "ADX_MIN": 18, "MIN_CONFIDENCE": 0.55},
    {"label": "TIGHT_SL_2",       "SL_ATR_BUFFER": 0.3, "TP_RANGE_MULT": 1.0,  "ADX_MIN": 22, "MIN_CONFIDENCE": 0.55},
    {"label": "TIGHT_SL_3",       "SL_ATR_BUFFER": 0.3, "TP_RANGE_MULT": 2.0,  "ADX_MIN": 15, "MIN_CONFIDENCE": 0.45},

    # --- Original Default ---
    {"label": "ORIGINAL_DEFAULT", "SL_ATR_BUFFER": 0.5, "TP_RANGE_MULT": 1.5,  "ADX_MIN": 18, "MIN_CONFIDENCE": 0.55},
]


def apply_params(params: dict):
    """Apply grid-search params to strategy module-level constants."""
    import app.strategy.templates.gbp_session_breakout as mod
    for key, val in params.items():
        if key == "label":
            continue
        if hasattr(mod, key):
            setattr(mod, key, val)


def run_direct_backtest(df: pd.DataFrame, params: dict) -> dict:
    """
    Lightweight direct backtest — NO regime filter, NO cooldown, NO session guard.
    
    Calls strategy.analyze() every 5 bars (M5 = 25min signal cadence).
    Uses simple SL/TP hit detection. Same risk sizing as live (1% per trade).
    
    This is ~20-50× faster than the full Backtester class because:
    - No classify_regime() per bar (saves ~6 indicator calculations per bar)
    - No CooldownManager/SessionGuard/RiskDampener overhead
    - Analyzes every 5th bar instead of every bar
    """
    from app.strategy.templates.gbp_session_breakout import GbpSessionBreakoutStrategy

    apply_params(params)
    strategy = GbpSessionBreakoutStrategy()

    profile = SymbolProfile(
        symbol=SYMBOL,
        contract_size=CONTRACT_SIZE,
        point=POINT,
        digits=DIGITS,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
    )

    equity = INITIAL_EQUITY
    peak_equity = equity
    max_dd_pct = 0.0

    trades = []
    open_trade = None
    trade_counter = 0

    warmup = 250  # Need enough bars for EMA/ADX/ATR warmup
    total_len = len(df)

    for i in range(warmup, total_len):
        bar = df.iloc[i]
        bar_time = bar["time"]
        close = bar["close"]
        high = bar["high"]
        low = bar["low"]

        # ─── Check Exit ───
        if open_trade is not None:
            sl_hit = False
            tp_hit = False
            exit_price = 0.0

            if open_trade["action"] == "BUY":
                if low <= open_trade["sl"]:
                    sl_hit = True
                    exit_price = open_trade["sl"]
                elif high >= open_trade["tp"]:
                    tp_hit = True
                    exit_price = open_trade["tp"]
            else:  # SELL
                if high >= open_trade["sl"]:
                    sl_hit = True
                    exit_price = open_trade["sl"]
                elif low <= open_trade["tp"]:
                    tp_hit = True
                    exit_price = open_trade["tp"]

            if sl_hit or tp_hit:
                if open_trade["action"] == "BUY":
                    profit = (exit_price - open_trade["entry"]) * open_trade["lot"] * CONTRACT_SIZE
                else:
                    profit = (open_trade["entry"] - exit_price) * open_trade["lot"] * CONTRACT_SIZE

                open_trade["exit_price"] = exit_price
                open_trade["exit_time"] = bar_time
                open_trade["exit_reason"] = "SL" if sl_hit else "TP"
                open_trade["profit"] = round(profit, 2)

                equity += profit
                trades.append(open_trade)
                open_trade = None

                # Track DD
                if equity > peak_equity:
                    peak_equity = equity
                dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
                if dd > max_dd_pct:
                    max_dd_pct = dd

                continue  # Don't re-enter same bar

        # ─── Check Entry (every 5 bars for speed) ───
        if open_trade is None and i % 5 == 0:
            window = df.iloc[max(0, i - 300):i + 1]
            if len(window) < 200:
                continue

            try:
                decision = strategy.analyze(window, profile, RegimeType.UNKNOWN)
            except Exception:
                continue

            if decision.action in (Action.BUY, Action.SELL) and decision.stop_loss and decision.stop_loss > 0:
                entry_price = close
                sl = decision.stop_loss
                tp = decision.take_profit or 0.0

                # Risk-based lot sizing (1% risk)
                sl_dist = abs(entry_price - sl)
                if sl_dist <= 0:
                    continue

                risk_usd = equity * 0.01
                lot = risk_usd / (sl_dist * CONTRACT_SIZE)
                lot = max(0.01, min(round(lot, 2), 10.0))

                trade_counter += 1
                open_trade = {
                    "id": trade_counter,
                    "action": decision.action.value,
                    "entry": entry_price,
                    "entry_time": bar_time,
                    "sl": sl,
                    "tp": tp,
                    "lot": lot,
                }

    # Close any open trade at end
    if open_trade is not None:
        exit_price = df.iloc[-1]["close"]
        if open_trade["action"] == "BUY":
            profit = (exit_price - open_trade["entry"]) * open_trade["lot"] * CONTRACT_SIZE
        else:
            profit = (open_trade["entry"] - exit_price) * open_trade["lot"] * CONTRACT_SIZE

        open_trade["exit_price"] = exit_price
        open_trade["exit_time"] = df.iloc[-1]["time"]
        open_trade["exit_reason"] = "END"
        open_trade["profit"] = round(profit, 2)
        equity += profit
        trades.append(open_trade)

    # ─── Compute Stats ───
    total = len(trades)
    wins = [t for t in trades if t["profit"] > 0]
    losses = [t for t in trades if t["profit"] <= 0]

    gross_profit = sum(t["profit"] for t in wins)
    gross_loss = abs(sum(t["profit"] for t in losses))

    wr = (len(wins) / total * 100) if total > 0 else 0
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0)
    net = equity - INITIAL_EQUITY
    exp = net / total if total > 0 else 0

    return {
        "trades": total,
        "winning": len(wins),
        "losing": len(losses),
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "net_profit": round(net, 2),
        "max_drawdown": round(max_dd_pct, 1),
        "expectancy": round(exp, 2),
        "params": params.copy(),
        "trade_list": trades,
    }


def main():
    """Main optimization loop."""
    print("=" * 110)
    print("🇬🇧 GBPUSDc — SESSION BREAKOUT OPTIMIZATION (All Regimes)")
    print(f"   Symbol: {SYMBOL} | Days: {DAYS} | Configs: {len(PARAM_GRID)} | Equity: ${INITIAL_EQUITY}")
    print("=" * 110)

    # ─── 1. Initialize MT5 & Fetch Data ───
    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS)
    rates = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, utc_from, utc_to)

    if rates is None or len(rates) == 0:
        print(f"❌ No data for {SYMBOL}")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"✅ Data: {len(df)} bars ({df['time'].iloc[0]} → {df['time'].iloc[-1]})\n")

    mt5.shutdown()

    # ─── 2. Run Grid Search ───
    results = []
    for idx, params in enumerate(PARAM_GRID, 1):
        label = params.get("label", f"SET_{idx}")
        print(f"[{idx}/{len(PARAM_GRID)}] {label:25s}...", end=" ", flush=True)

        t0 = time.monotonic()
        try:
            res = run_direct_backtest(df, params)
            elapsed = time.monotonic() - t0
            results.append(res)
            print(
                f"Done ({elapsed:.1f}s) | "
                f"Trades={res['trades']:>3} | "
                f"WR={res['win_rate']:>5.1f}% | "
                f"PF={res['profit_factor']:>5.2f} | "
                f"P&L=${res['net_profit']:>+8.2f}"
            )
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"ERROR ({elapsed:.1f}s): {e}")
            import traceback; traceback.print_exc()
            continue

    # ─── 3. Summary Table ───
    print("\n" + "=" * 115)
    print(f"    {'Config':<25s} | {'Trades':>6} | {'W':>4} | {'L':>4} | {'WR%':>6} | {'PF':>5} | {'P&L':>11} | {'DD%':>5} | {'Exp$/T':>7} |")
    print("-" * 115)

    # Sort: prioritize WR >= 50% AND PF > 1.0 AND profit > 0, then by PnL
    sorted_results = sorted(results, key=lambda x: (
        1 if x["win_rate"] >= 50 and x["profit_factor"] > 1.0 and x["net_profit"] > 0 and x["trades"] >= 5 else 0,
        x["net_profit"],
    ), reverse=True)

    best = None
    for r in sorted_results:
        label = r["params"].get("label", "?")
        meets = r["win_rate"] >= 50 and r["profit_factor"] > 1.0 and r["net_profit"] > 0 and r["trades"] >= 5

        if meets and r["profit_factor"] >= 1.3:
            marker = "🏆"
        elif meets:
            marker = "✅"
        elif r["net_profit"] > 0:
            marker = "⭐"
        else:
            marker = "❌"

        prefix = ">>> " if meets and best is None else "    "
        if meets and best is None:
            best = r

        print(
            f"{prefix}{label:<21s} | {r['trades']:>6} | {r['winning']:>4} | "
            f"{r['losing']:>4} | {r['win_rate']:>5.1f}% | {r['profit_factor']:>5.2f} | "
            f"${r['net_profit']:>+9.2f} | {r['max_drawdown']:>4.1f}% | "
            f"${r['expectancy']:>+5.02f} | {marker}"
        )

    print("=" * 115)

    # ─── 4. Best Config Detail ───
    if best:
        bp = best["params"]
        print(f"\n🏆 BEST CONFIG: {bp.get('label', '?')}")
        print(f"   WR={best['win_rate']}% | PF={best['profit_factor']} | P&L=${best['net_profit']:.2f} | DD={best['max_drawdown']}% | {best['trades']} trades")
        print(f"\n   Production Parameters (for gbp_session_breakout.py):")
        for k, v in bp.items():
            if k == "label":
                continue
            print(f"     {k} = {v}")
    else:
        print("\n⚠️ No config met criteria (WR >= 50%, PF > 1.0, Profit > 0, Trades >= 5)")
        if sorted_results:
            best = sorted_results[0]
            bp = best["params"]
            print(f"   Best available: {bp.get('label', '?')} — WR={best['win_rate']}%, PF={best['profit_factor']}, P&L=${best['net_profit']:.2f}")

    # ─── 5. Save to SQLite DB ───
    print("\n💾 Saving results to SQLite DB...")
    try:
        from app.db.sqlite import SQLiteStore

        db = SQLiteStore(get_settings())
        db.connect()

        # Save ALL results as audit trail
        for r in results:
            p = r["params"]
            db.save_backtest_result(
                symbol=SYMBOL,
                strategy_name=STRATEGY_NAME,
                label=p.get("label", ""),
                params={k: v for k, v in p.items() if k != "label"},
                win_rate=r["win_rate"],
                profit_factor=r["profit_factor"],
                total_pnl=r["net_profit"],
                max_drawdown_pct=r["max_drawdown"],
                total_trades=r["trades"],
                winning_trades=r["winning"],
                losing_trades=r["losing"],
                backtest_days=DAYS,
                expectancy=r["expectancy"],
            )
        print(f"   ✅ Saved {len(results)} backtest results to audit trail")

        # Save BEST config as active strategy params
        if best:
            bp = best["params"]
            clean_params = {k: v for k, v in bp.items() if k != "label"}
            db.save_strategy_params(
                symbol=SYMBOL,
                strategy_name=STRATEGY_NAME,
                params=clean_params,
                win_rate=best["win_rate"],
                profit_factor=best["profit_factor"],
                total_pnl=best["net_profit"],
                max_drawdown_pct=best["max_drawdown"],
                total_trades=best["trades"],
                backtest_days=DAYS,
                label=bp.get("label", ""),
            )
            print(f"   ✅ Saved winning config '{bp.get('label', '')}' to strategy_params")
            print(f"      → Use: db.get_strategy_params('{SYMBOL}', '{STRATEGY_NAME}')")

        db.disconnect()
    except Exception as e:
        import traceback
        print(f"   ⚠️ DB save failed: {e}")
        traceback.print_exc()

    print("\n✅ GBPUSDc Optimization Complete!")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
