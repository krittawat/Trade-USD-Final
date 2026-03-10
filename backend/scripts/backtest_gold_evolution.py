"""
Gold Evolution Strategy Backtest & Parameter Tuning
=====================================================
Backtest the GoldEvolutionStrategy over 200 days on M5 with H1 trend filter.
Grid search across TP/SL/score configurations targeting WR>70%.

Usage:
    cd d:\\VibeCode\\Trade\\backend
    python scripts/backtest_gold_evolution.py
"""
print("🐉 Gold Evolution Backtest — Starting...", flush=True)

import sys
import os
import time
import random
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from copy import deepcopy

logging.disable(logging.CRITICAL)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

from app.core.config import get_settings
from app.execution.backtester import Backtester, BacktestTrade, BacktestResult
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile, AccountState
from app.brain.regime import classify_regime
from app.risk.sizing import calculate_lot_size
from app.risk.trailing import TrailingManager

settings = get_settings()

def _get_sl_tp(decision):
    sl = getattr(decision, 'sl', None) or getattr(decision, 'stop_loss', None) or 0.0
    tp = getattr(decision, 'tp', None) or getattr(decision, 'take_profit', None) or 0.0
    return sl, tp

class MockMT5Client:
    def modify_position(self, ticket, sl, tp):
        return True

SYMBOL = "XAUUSDc"
CONTRACT_SIZE = 100.0
POINT = 0.01
DIGITS = 2
DAYS = 30


# ─── Parameter Configurations v2 (expanded grid) ───
PARAM_GRID = [
    # ── Lower thresholds to increase trade count ──
    {"label": "T50_TP1.0",  "tp_atr_mult": 1.0, "sl_atr_mult": 1.5, "min_score_trade": 50},
    {"label": "T50_TP1.2",  "tp_atr_mult": 1.2, "sl_atr_mult": 1.5, "min_score_trade": 50},
    {"label": "T50_TP1.5",  "tp_atr_mult": 1.5, "sl_atr_mult": 1.5, "min_score_trade": 50},
    {"label": "T55_TP1.0",  "tp_atr_mult": 1.0, "sl_atr_mult": 1.5, "min_score_trade": 55},
    {"label": "T55_TP1.2",  "tp_atr_mult": 1.2, "sl_atr_mult": 1.5, "min_score_trade": 55},
    {"label": "T55_TP1.5",  "tp_atr_mult": 1.5, "sl_atr_mult": 1.5, "min_score_trade": 55},
    {"label": "T60_TP1.0",  "tp_atr_mult": 1.0, "sl_atr_mult": 1.5, "min_score_trade": 60},
    {"label": "T60_TP1.2",  "tp_atr_mult": 1.2, "sl_atr_mult": 1.5, "min_score_trade": 60},
    {"label": "T60_TP1.5",  "tp_atr_mult": 1.5, "sl_atr_mult": 1.5, "min_score_trade": 60},
    # ── Wide SL variants ──
    {"label": "T50_WS2.0",  "tp_atr_mult": 1.0, "sl_atr_mult": 2.0, "min_score_trade": 50},
    {"label": "T55_WS2.0",  "tp_atr_mult": 1.2, "sl_atr_mult": 2.0, "min_score_trade": 55},
    {"label": "T60_WS2.0",  "tp_atr_mult": 1.2, "sl_atr_mult": 2.0, "min_score_trade": 60},
    # ── Best from v1 ──
    {"label": "T65_TP1.2",  "tp_atr_mult": 1.2, "sl_atr_mult": 1.5, "min_score_trade": 65},
    {"label": "T70_TP1.2",  "tp_atr_mult": 1.2, "sl_atr_mult": 1.5, "min_score_trade": 70},
]


class EvolutionBacktester(Backtester):
    """Enhanced backtester that passes H1 candles to strategy."""

    def __init__(self, strategy, initial_equity=10000.0, h1_candles=None):
        super().__init__(strategy, initial_equity=initial_equity)
        self.h1_candles = h1_candles
        self.peak_equity = initial_equity
        self.metrics = {
            "current_loss_streak": 0,
            "current_win_streak": 0,
            "max_drawdown_pct": 0.0,
            "risk_modifier": 1.0,
        }

    def _update_metrics(self, pnl, equity):
        if pnl > 0:
            self.metrics["current_win_streak"] += 1
            self.metrics["current_loss_streak"] = 0
        elif pnl < 0:
            self.metrics["current_loss_streak"] += 1
            self.metrics["current_win_streak"] = 0

    def run(self, candles: pd.DataFrame, symbol, contract_size=100.0, point=0.01, digits=2) -> BacktestResult:
        start_time = time.monotonic()
        total_bars = len(candles)

        # State
        equity = self.initial_equity
        self.peak_equity = equity
        max_dd_usd = 0.0
        max_dd_pct = 0.0
        open_trade = None
        closed_trades: list[BacktestTrade] = []
        equity_curve: list[dict] = []
        trade_counter = 0

        is_cent = str(symbol).upper().endswith("C")
        vol_min = 0.0001 if is_cent else 0.01
        vol_max = 200.0 if is_cent else 100.0
        vol_step = 0.0001 if is_cent else 0.01

        profile = SymbolProfile(
            symbol=symbol, contract_size=contract_size, point=point,
            digits=digits, volume_min=vol_min, volume_max=vol_max, volume_step=vol_step,
        )

        for i in range(self.warmup_bars, total_bars):
            bar = candles.iloc[i]
            bar_time = str(bar.get("time", i))
            bar_high = bar["high"]
            bar_low = bar["low"]
            bar_close = bar["close"]

            # ── Manage Open Trade ──
            if open_trade is not None:
                open_trade.bars_held += 1
                exit_happened = False

                if open_trade.action == "BUY":
                    if open_trade.sl > 0 and bar_low <= open_trade.sl:
                        open_trade.exit_price = open_trade.sl
                        open_trade.exit_reason = "SL"
                        exit_happened = True
                    elif open_trade.tp > 0 and bar_high >= open_trade.tp:
                        open_trade.exit_price = open_trade.tp
                        open_trade.exit_reason = "TP"
                        exit_happened = True
                else:
                    if open_trade.sl > 0 and bar_high >= open_trade.sl:
                        open_trade.exit_price = open_trade.sl
                        open_trade.exit_reason = "SL"
                        exit_happened = True
                    elif open_trade.tp > 0 and bar_low <= open_trade.tp:
                        open_trade.exit_price = open_trade.tp
                        open_trade.exit_reason = "TP"
                        exit_happened = True

                if exit_happened:
                    open_trade.exit_time = bar_time
                    open_trade = self._close_trade(open_trade, contract_size)
                    equity += open_trade.profit_usd
                    closed_trades.append(open_trade)
                    self._update_metrics(open_trade.profit_usd, equity)
                    open_trade = None

            # ── Signal (every 5 bars) ──
            if i % 5 != 0:
                continue

            window_start = max(0, i - 299)
            history = candles.iloc[window_start:i + 1]
            if len(history) < 50:
                continue

            regime = classify_regime(history)
            if not regime.actionable:
                continue

            # Build H1 slice: find H1 candles up to current time
            h1_slice = None
            if self.h1_candles is not None and len(self.h1_candles) > 0:
                current_time = bar.get("time")
                if current_time is not None:
                    h1_slice = self.h1_candles[self.h1_candles["time"] <= current_time]
                    if len(h1_slice) < 220:
                        h1_slice = None

            try:
                decision = self.strategy.analyze(
                    history, profile, regime.regime,
                    h1_candles=h1_slice,
                )
            except TypeError:
                decision = self.strategy.analyze(history, profile, regime.regime)

            if decision.action in (Action.BUY, Action.SELL):
                # Reverse logic
                if open_trade and open_trade.action != decision.action.value:
                    open_trade.exit_price = bar_close
                    open_trade.exit_time = bar_time
                    open_trade.exit_reason = "REVERSE"
                    open_trade = self._close_trade(open_trade, contract_size)
                    equity += open_trade.profit_usd
                    closed_trades.append(open_trade)
                    self._update_metrics(open_trade.profit_usd, equity)
                    open_trade = None

                _sl, _tp = _get_sl_tp(decision)
                if open_trade is None and _sl and _sl > 0:
                    r_pct = getattr(decision, "risk_pct", None)
                    if r_pct is not None and r_pct < 1.0:
                        r_pct *= 100.0

                    sizing_decision = Decision(
                        symbol=symbol, action=decision.action,
                        confidence=min(max(decision.confidence, 0.0), 1.0),
                        reason=decision.reason, stop_loss=_sl, take_profit=_tp,
                        risk_pct=r_pct, strategy_name=self.strategy.name, timeframe="M5",
                    )
                    account = AccountState(balance=equity, equity=equity, margin=0, margin_free=equity)
                    plan = calculate_lot_size(
                        decision=sizing_decision, profile=profile, account=account,
                        settings=settings, entry_price=bar_close, stop_loss=_sl,
                        performance_metrics=self.metrics,
                    )
                    if hasattr(plan, "lot_size"):
                        trade_counter += 1
                        open_trade = BacktestTrade(
                            trade_id=trade_counter, symbol=symbol,
                            strategy=self.strategy.name, action=decision.action.value,
                            entry_price=bar_close, entry_time=bar_time,
                            sl=_sl, tp=_tp, lot_size=plan.lot_size,
                            regime=regime.regime.value,
                        )

            # Track equity
            if i % 20 == 0:
                unrealized = 0.0
                if open_trade:
                    if open_trade.action == "BUY":
                        unrealized = (bar_close - open_trade.entry_price) * open_trade.lot_size * contract_size
                    else:
                        unrealized = (open_trade.entry_price - bar_close) * open_trade.lot_size * contract_size
                curr_eq = equity + unrealized
                equity_curve.append({"bar": i, "time": bar_time, "equity": round(curr_eq, 2)})
                if curr_eq > self.peak_equity:
                    self.peak_equity = curr_eq
                dd = self.peak_equity - curr_eq
                dd_pct = (dd / self.peak_equity * 100) if self.peak_equity > 0 else 0
                if dd_pct > max_dd_pct: max_dd_pct = dd_pct
                if dd > max_dd_usd: max_dd_usd = dd
                self.metrics["max_drawdown_pct"] = max_dd_pct

        # Close at end
        if open_trade:
            open_trade.exit_price = candles.iloc[-1]["close"]
            open_trade.exit_time = str(candles.iloc[-1]["time"])
            open_trade.exit_reason = "END"
            open_trade = self._close_trade(open_trade, contract_size)
            equity += open_trade.profit_usd
            closed_trades.append(open_trade)

        duration = time.monotonic() - start_time
        return self._compute_stats(
            closed_trades, equity_curve, symbol, self.strategy.name,
            str(candles.iloc[0]["time"]), str(candles.iloc[-1]["time"]),
            total_bars, self.initial_equity, equity, max_dd_usd, max_dd_pct, duration,
        )


def run_backtest(m5_candles, h1_candles, params, initial_equity=10000.0):
    """Run a single backtest with given parameters."""
    random.seed(42)
    np.random.seed(42)

    from app.strategy.templates.gold_evolution import GoldEvolutionStrategy
    strategy = GoldEvolutionStrategy()

    # Override parameters
    for key, val in params.items():
        if key == "label":
            continue
        if key in strategy.p:
            strategy.p[key] = val

    bt = EvolutionBacktester(strategy, initial_equity=initial_equity, h1_candles=h1_candles)
    result = bt.run(m5_candles, SYMBOL, contract_size=CONTRACT_SIZE, point=POINT, digits=DIGITS)
    return result


def main():
    print("=" * 100)
    print("🐉 GOLD EVOLUTION STRATEGY — BACKTEST & PARAMETER TUNING")
    print(f"   Symbol: {SYMBOL} | Days: {DAYS} | TF: M5 + H1 filter | Configs: {len(PARAM_GRID)}")
    print("=" * 100)

    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return

    # ── Fetch M5 data ──
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS)

    print(f"📊 Fetching M5 data ({DAYS} days)...", end=" ", flush=True)
    rates_m5 = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M5, utc_from, utc_to)
    if rates_m5 is None or len(rates_m5) == 0:
        print(f"❌ No M5 data for {SYMBOL}")
        mt5.shutdown()
        return
    df_m5 = pd.DataFrame(rates_m5)
    df_m5["time"] = pd.to_datetime(df_m5["time"], unit="s")
    print(f"✅ {len(df_m5)} bars ({df_m5['time'].iloc[0]} → {df_m5['time'].iloc[-1]})")

    # ── Fetch H1 data ──
    print(f"📊 Fetching H1 data ({DAYS} days)...", end=" ", flush=True)
    rates_h1 = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_H1, utc_from, utc_to)
    if rates_h1 is None or len(rates_h1) == 0:
        print(f"⚠️ No H1 data — will use M5 fallback")
        df_h1 = None
    else:
        df_h1 = pd.DataFrame(rates_h1)
        df_h1["time"] = pd.to_datetime(df_h1["time"], unit="s")
        print(f"✅ {len(df_h1)} bars")

    print()

    # ── Run Grid ──
    results = []
    for idx, params in enumerate(PARAM_GRID, 1):
        label = params.get("label", f"SET_{idx}")
        print(f"[{idx}/{len(PARAM_GRID)}] {label}...", end=" ", flush=True)
        t0 = time.monotonic()
        result = run_backtest(df_m5, df_h1, params)
        elapsed = time.monotonic() - t0
        results.append((params, result))
        wr_icon = "🏆" if result.win_rate >= 70 else ("⭐" if result.win_rate >= 60 else "")
        print(f"WR={result.win_rate:.1f}% PF={result.profit_factor:.2f} N={result.total_trades} ({elapsed:.1f}s) {wr_icon}")

    # ── Summary Table ──
    print()
    print("=" * 120)
    print(f"{'Config':<22s} | {'Trades':>6} | {'Wins':>5} | {'Loss':>5} | {'WR%':>6} | {'PF':>5} | {'P&L':>11} | {'DD%':>5} | {'Exp':>7} | Status")
    print("-" * 120)

    sorted_results = sorted(results, key=lambda x: (x[1].win_rate, x[1].total_profit_usd), reverse=True)

    best = None
    live_ready = []

    for params, r in sorted_results:
        label = params.get("label", "?")
        pnl_icon = "✅" if r.total_profit_usd >= 0 else "❌"
        wr_icon = "🏆" if r.win_rate >= 70 else ("⭐" if r.win_rate >= 60 else "")
        exp = round(r.total_profit_usd / r.total_trades, 2) if r.total_trades > 0 else 0

        # Live-ready check: WR≥50%, PF≥1.3, DD≤6%
        is_live = r.win_rate >= 50 and r.profit_factor >= 1.3 and r.max_drawdown_pct <= 6 and r.total_trades >= 10
        live_icon = "🟢 LIVE-READY" if is_live else ""

        if r.win_rate >= 70 and is_live:
            prefix = ">>> "
            if best is None:
                best = (params, r)
        else:
            prefix = "    "

        if is_live:
            live_ready.append((params, r))

        print(f"{prefix}{label:<18s} | {r.total_trades:>6} | {r.winning_trades:>5} | {r.losing_trades:>5} | {r.win_rate:>5.1f}% | {r.profit_factor:>5.2f} | ${r.total_profit_usd:>+9.2f} | {r.max_drawdown_pct:>4.1f}% | ${exp:>+5.2f} | {pnl_icon} {wr_icon} {live_icon}")

    print("=" * 120)

    # ── Winner Analysis ──
    if best:
        bp, br = best
        print()
        print("🏆🏆🏆 WR>70% CONFIGURATION FOUND! 🏆🏆🏆")
        print(f"   Config: {bp.get('label', '?')}")
        print(f"   Win Rate: {br.win_rate}% ({'✅ TARGET MET' if br.win_rate >= 70 else ''})")
        print(f"   Profit Factor: {br.profit_factor}")
        print(f"   Total P&L: ${br.total_profit_usd:.2f}")
        print(f"   Max DD: {br.max_drawdown_pct}%")
        print(f"   Trades: {br.total_trades} ({br.winning_trades}W / {br.losing_trades}L)")
        print()
        print("   Parameters to apply:")
        for k, v in bp.items():
            if k == "label": continue
            print(f"     {k} = {v}")

        if br.per_regime:
            print()
            print("   Per-Regime Breakdown:")
            for regime, stats in br.per_regime.items():
                total_r = stats.get('wins', 0) + stats.get('losses', 0)
                wr_r = round(stats['wins'] / total_r * 100, 1) if total_r > 0 else 0
                print(f"     {regime:<20s}: WR={wr_r:>5.1f}% | N={total_r:>3} | P&L=${stats.get('pnl', 0):>+.2f}")

    elif live_ready:
        bp, br = live_ready[0]
        print()
        print("⭐ No WR>70% config found, but LIVE-READY configs exist:")
        print(f"   Best: {bp.get('label', '?')} — WR={br.win_rate}%, PF={br.profit_factor}, DD={br.max_drawdown_pct}%")
    else:
        best_wr = max(results, key=lambda x: x[1].win_rate)
        best_pnl = max(results, key=lambda x: x[1].total_profit_usd)
        print()
        print("⚠️  No config met live-ready criteria.")
        print(f"   Highest WR:  {best_wr[0].get('label','?')} — {best_wr[1].win_rate}% WR, ${best_wr[1].total_profit_usd:.2f}")
        print(f"   Highest P&L: {best_pnl[0].get('label','?')} — {best_pnl[1].win_rate}% WR, ${best_pnl[1].total_profit_usd:.2f}")

    mt5.shutdown()
    print()
    print("✅ Gold Evolution Backtest Complete!")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
