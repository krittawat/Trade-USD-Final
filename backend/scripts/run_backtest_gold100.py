"""
Fast Gold 100-Day Backtest — หา strategy ที่เทรดได้ทุกสภาวะตลาด.

Optimization:
    - ข้าม M1 strategy สำหรับ Gold (too noisy)
    - analyze() ทุก N bars แทน ทุก bar (N=5 for M5, N=3 for M15, N=1 for H1)
    - SL/TP ยังจำลองทุก bar (accuracy เท่า bar-by-bar)
    - Per-regime analysis: หา strategy ที่ทำกำไรใน EVERY regime

Usage:
    cd d:\\VibeCode\\Trade\\backend
    python scripts/run_backtest_gold100.py
"""

import sys
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd

# pandas_ta compat
_orig_setattr = pd.Series.__setattr__
def _safe_setattr(self, name, value):
    if name == "category" and isinstance(value, str):
        try: _orig_setattr(self, name, value)
        except (AttributeError, ValueError): pass
    else: _orig_setattr(self, name, value)
pd.Series.__setattr__ = _safe_setattr

from app.core.logging import get_logger
from app.execution.backtester import BacktestTrade, BacktestResult
from app.strategy.factory import TEMPLATE_REGISTRY
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.brain.regime import classify_regime

logger = get_logger("BacktestGold100")

import logging

def suppress_loggers():
    for name, log in logging.Logger.manager.loggerDict.items():
        if isinstance(log, logging.Logger):
            if any(p in name for p in ["app.strategy", "app.brain", "Backtester", "scalping", "sniper", "trend_rider"]):
                log.setLevel(logging.CRITICAL)
    logging.getLogger().setLevel(logging.CRITICAL)
    logging.getLogger("BacktestGold100").setLevel(logging.INFO)


def load_strategy(name: str):
    if name not in TEMPLATE_REGISTRY:
        return None
    class_name, key, timeframe, regimes, bridge = TEMPLATE_REGISTRY[name]
    try:
        mod = __import__(f"app.strategy.templates.{name}", fromlist=[class_name])
        cls = getattr(mod, class_name, None)
        if cls is None: return None
        return cls(), timeframe, regimes
    except:
        return None


# =============================================
# Fast Backtester — analyze every N bars, SL/TP check every bar
# =============================================

def fast_backtest(
    strategy,
    candles: pd.DataFrame,
    symbol: str = "XAUUSDc",
    contract_size: float = 100.0,
    point: float = 0.01,
    initial_equity: float = 100.0,
    risk_per_trade: float = 0.02,
    warmup_bars: int = 50,
    analyze_interval: int = 5,  # Call analyze() every N bars
) -> BacktestResult:
    """
    Fast backtest engine:
      - SL/TP checked EVERY bar (accuracy preserved)
      - analyze() called every N bars (speed optimization)
      - Regime classified every 50 bars
    """
    start_time = time.monotonic()
    total_bars = len(candles)

    # State
    equity = initial_equity
    peak_equity = equity
    max_dd_usd = 0.0
    max_dd_pct = 0.0
    open_trade: Optional[BacktestTrade] = None
    closed_trades: list[BacktestTrade] = []
    equity_curve: list[dict] = []
    trade_counter = 0
    current_regime = RegimeType.UNKNOWN

    profile = SymbolProfile(
        symbol=symbol, contract_size=contract_size, point=point,
        digits=2, volume_min=0.01, volume_max=100.0, volume_step=0.01,
    )

    for i in range(warmup_bars, total_bars):
        bar = candles.iloc[i]
        bar_time = str(bar.get("time", i))
        bar_high = bar["high"]
        bar_low = bar["low"]
        bar_close = bar["close"]

        # ─── Step 1: Check SL/TP every bar ───
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
                if open_trade.action == "BUY":
                    pnl = (open_trade.exit_price - open_trade.entry_price) * open_trade.lot_size * contract_size
                else:
                    pnl = (open_trade.entry_price - open_trade.exit_price) * open_trade.lot_size * contract_size
                open_trade.profit_usd = round(pnl, 2)
                sl_dist = abs(open_trade.entry_price - open_trade.sl) if open_trade.sl > 0 else 1.0
                risk_usd = sl_dist * open_trade.lot_size * contract_size
                open_trade.risk_reward = round(pnl / risk_usd, 2) if risk_usd > 0 else 0.0
                equity += pnl
                closed_trades.append(open_trade)
                open_trade = None

        # ─── Step 2: Analyze ONLY every N bars ───
        if (i - warmup_bars) % analyze_interval != 0:
            # Still track equity curve
            if i % 20 == 0:
                unrealized = 0.0
                if open_trade:
                    if open_trade.action == "BUY":
                        unrealized = (bar_close - open_trade.entry_price) * open_trade.lot_size * contract_size
                    else:
                        unrealized = (open_trade.entry_price - bar_close) * open_trade.lot_size * contract_size
                current_equity = equity + unrealized
                equity_curve.append({"bar": i, "time": bar_time, "equity": round(current_equity, 2)})
                if current_equity > peak_equity: peak_equity = current_equity
                dd_usd = peak_equity - current_equity
                dd_pct = (dd_usd / peak_equity * 100) if peak_equity > 0 else 0
                if dd_usd > max_dd_usd: max_dd_usd = dd_usd
                if dd_pct > max_dd_pct: max_dd_pct = dd_pct
            continue

        # Window for analyze()
        window_start = max(0, i - 299)
        history = candles.iloc[window_start:i + 1]
        if len(history) < 50:
            continue

        # Classify regime every 50 analyze calls
        if (i - warmup_bars) % (analyze_interval * 50) == 0:
            try:
                current_regime = classify_regime(history)
            except:
                current_regime = RegimeType.UNKNOWN

        try:
            decision = strategy.analyze(history, profile, current_regime)
        except BaseException as e:
            if isinstance(e, (SystemExit, KeyboardInterrupt)):
                import traceback
                if "pandas_ta" not in traceback.format_exc():
                    raise
            continue

        if decision.action in (Action.BUY, Action.SELL):
            action_str = decision.action.value

            # Close opposing trade
            if open_trade is not None and open_trade.action != action_str:
                open_trade.exit_price = bar_close
                open_trade.exit_time = bar_time
                open_trade.exit_reason = "SIGNAL_REVERSE"
                if open_trade.action == "BUY":
                    pnl = (bar_close - open_trade.entry_price) * open_trade.lot_size * contract_size
                else:
                    pnl = (open_trade.entry_price - bar_close) * open_trade.lot_size * contract_size
                open_trade.profit_usd = round(pnl, 2)
                equity += pnl
                closed_trades.append(open_trade)
                open_trade = None

            # Open new trade
            if open_trade is None and decision.stop_loss and decision.stop_loss > 0:
                trade_counter += 1
                sl_dist = abs(bar_close - decision.stop_loss)
                risk_usd = equity * risk_per_trade
                lot = risk_usd / (sl_dist * contract_size) if sl_dist > 0 else 0.01
                lot = max(0.01, min(lot, 10.0))
                lot = round(lot, 2)

                open_trade = BacktestTrade(
                    trade_id=trade_counter, symbol=symbol,
                    strategy=getattr(strategy, 'name', 'unknown'),
                    action=action_str,
                    entry_price=bar_close,
                    entry_time=bar_time,
                    sl=decision.stop_loss,
                    tp=decision.take_profit or 0.0,
                    lot_size=lot,
                    regime=current_regime.value if hasattr(current_regime, 'value') else str(current_regime),
                )

        # Track equity curve
        if i % 20 == 0:
            unrealized = 0.0
            if open_trade:
                if open_trade.action == "BUY":
                    unrealized = (bar_close - open_trade.entry_price) * open_trade.lot_size * contract_size
                else:
                    unrealized = (open_trade.entry_price - bar_close) * open_trade.lot_size * contract_size
            current_equity = equity + unrealized
            equity_curve.append({"bar": i, "time": bar_time, "equity": round(current_equity, 2)})
            if current_equity > peak_equity: peak_equity = current_equity
            dd_usd = peak_equity - current_equity
            dd_pct = (dd_usd / peak_equity * 100) if peak_equity > 0 else 0
            if dd_usd > max_dd_usd: max_dd_usd = dd_usd
            if dd_pct > max_dd_pct: max_dd_pct = dd_pct

    # Close open trade at end
    if open_trade is not None:
        open_trade.exit_price = candles.iloc[-1]["close"]
        open_trade.exit_time = str(candles.iloc[-1].get("time", total_bars))
        open_trade.exit_reason = "END_OF_DATA"
        if open_trade.action == "BUY":
            pnl = (open_trade.exit_price - open_trade.entry_price) * open_trade.lot_size * contract_size
        else:
            pnl = (open_trade.entry_price - open_trade.exit_price) * open_trade.lot_size * contract_size
        open_trade.profit_usd = round(pnl, 2)
        equity += pnl
        closed_trades.append(open_trade)

    duration = time.monotonic() - start_time
    return _compute_stats(closed_trades, equity_curve, symbol, getattr(strategy, 'name', 'unknown'),
                          str(candles.iloc[0].get("time", "")), str(candles.iloc[-1].get("time", "")),
                          total_bars, initial_equity, equity, max_dd_usd, max_dd_pct, duration)


def _compute_stats(trades, equity_curve, symbol, strategy_name, start_date, end_date,
                   total_bars, initial_equity, final_equity, max_dd_usd, max_dd_pct, duration):
    total = len(trades)
    if total == 0:
        return BacktestResult(symbol=symbol, strategy=strategy_name, start_date=start_date, end_date=end_date,
                              total_bars=total_bars, total_trades=0, winning_trades=0, losing_trades=0,
                              win_rate=0, profit_factor=0, total_profit_usd=0, max_drawdown_pct=0, max_drawdown_usd=0,
                              expectancy=0, avg_rr=0, sharpe_ratio=0, avg_bars_held=0,
                              initial_equity=initial_equity, final_equity=final_equity,
                              equity_curve=equity_curve, duration_seconds=duration)

    wins = [t for t in trades if t.profit_usd > 0]
    losses = [t for t in trades if t.profit_usd <= 0]
    gross_profit = sum(t.profit_usd for t in wins)
    gross_loss = abs(sum(t.profit_usd for t in losses))
    total_profit = sum(t.profit_usd for t in trades)
    win_rate = (len(wins) / total * 100) if total > 0 else 0
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (10.0 if gross_profit > 0 else 0)
    expectancy = total_profit / total if total > 0 else 0
    avg_rr = sum(t.risk_reward for t in trades) / total if total > 0 else 0
    avg_bars = sum(t.bars_held for t in trades) / total if total > 0 else 0

    returns = [t.profit_usd / initial_equity for t in trades]
    if len(returns) > 1:
        import statistics
        mean_ret = statistics.mean(returns)
        std_ret = statistics.stdev(returns)
        sharpe = (mean_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0
    else:
        sharpe = 0

    # Per-regime
    per_regime: dict[str, dict] = {}
    for t in trades:
        r = t.regime
        if r not in per_regime:
            per_regime[r] = {"trades": 0, "wins": 0, "pnl": 0.0}
        per_regime[r]["trades"] += 1
        per_regime[r]["pnl"] += t.profit_usd
        if t.profit_usd > 0:
            per_regime[r]["wins"] += 1
    for r in per_regime:
        cnt = per_regime[r]["trades"]
        per_regime[r]["win_rate"] = round(per_regime[r]["wins"] / cnt * 100, 1) if cnt > 0 else 0
        per_regime[r]["pnl"] = round(per_regime[r]["pnl"], 2)

    return BacktestResult(
        symbol=symbol, strategy=strategy_name, start_date=start_date, end_date=end_date,
        total_bars=total_bars, total_trades=total, winning_trades=len(wins), losing_trades=len(losses),
        win_rate=round(win_rate, 1), profit_factor=round(pf, 2), total_profit_usd=round(total_profit, 2),
        max_drawdown_pct=round(max_dd_pct, 1), max_drawdown_usd=round(max_dd_usd, 2),
        expectancy=round(expectancy, 2), avg_rr=round(avg_rr, 2), sharpe_ratio=round(sharpe, 2),
        avg_bars_held=round(avg_bars, 1), initial_equity=initial_equity, final_equity=round(final_equity, 2),
        equity_curve=equity_curve, per_regime=per_regime, duration_seconds=round(duration, 2),
    )


def compute_score(result: BacktestResult) -> float:
    if result.total_trades < 5:
        return -999.0
    pf_score = min(result.profit_factor, 5.0) * 20.0
    wr_score = result.win_rate
    sharpe_score = min(max(result.sharpe_ratio, -2), 5) * 20
    dd_penalty = min(result.max_drawdown_pct, 50) * 2
    return round((pf_score * 0.4) + (wr_score * 0.2) + (sharpe_score * 0.2) - (dd_penalty * 0.2), 2)


# =============================================
# Main
# =============================================

def main():
    SYMBOL = "XAUUSDc"
    DAYS = 100
    RISK_PCT = 0.02
    
    # Analyze interval per timeframe (reduce analyze() calls by this factor)
    ANALYZE_INTERVALS = {"M5": 5, "M15": 3, "H1": 1}

    print(f"\n{'='*70}")
    print(f"  🏆 GOLD 100-DAY BACKTEST — หา Strategy ที่เทรดได้ทุกสภาวะตลาด")
    print(f"  Symbol: {SYMBOL}  |  Period: {DAYS} days")
    print(f"  Optimization: analyze every N bars (M5:5, M15:3, H1:1)")
    print(f"{'='*70}\n")

    if not mt5.initialize():
        print(f"[ERROR] MT5 not connected: {mt5.last_error()}")
        return

    acct = mt5.account_info()
    equity = acct.equity if acct else 1000.0
    print(f"  Account: {acct.login if acct else 'N/A'} | Equity: ${equity:.2f}")

    sym_info = mt5.symbol_info(SYMBOL)
    contract_size = sym_info.trade_contract_size if sym_info else 100.0
    point_val = sym_info.point if sym_info else 0.01
    print(f"  Contract: {contract_size} | Point: {point_val}")

    # Load strategies
    strategies = {}
    for name in TEMPLATE_REGISTRY:
        loaded = load_strategy(name)
        if loaded:
            inst, tf, regs = loaded
            if tf == "M1": continue  # Skip M1 for gold
            if hasattr(inst, 'analyze'):
                strategies[name] = loaded
    
    print(f"  Loaded: {len(strategies)} strategies\n")
    suppress_loggers()

    # Pre-fetch data
    print(f"  📥 Fetching OHLCV data...", flush=True)
    TIMEFRAME_MAP = {
        "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
    }
    candle_cache: dict[str, pd.DataFrame] = {}
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS)
    for tf in set(loaded[1] for loaded in strategies.values()):
        mt5_tf = TIMEFRAME_MAP.get(tf, mt5.TIMEFRAME_M5)
        rates = mt5.copy_rates_range(SYMBOL, mt5_tf, utc_from, utc_to)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            candle_cache[tf] = df
            print(f"     {tf}: {len(df)} bars", flush=True)

    # Run backtests
    print(f"\n{'─'*70}")
    print(f"  Testing all strategies...")
    print(f"{'─'*70}\n")

    results: list[tuple[str, BacktestResult, float]] = []
    n = 0
    total = len(strategies)

    for strat_name, (strat_instance, timeframe, regimes) in strategies.items():
        n += 1
        candles = candle_cache.get(timeframe)
        if candles is None or len(candles) < 100:
            print(f"  [{n:2d}/{total}] {strat_name:30s} ⏭️ SKIP (no data)", flush=True)
            continue

        interval = ANALYZE_INTERVALS.get(timeframe, 5)
        effective_calls = (len(candles) - 50) // interval
        print(f"  [{n:2d}/{total}] {strat_name:30s} ({timeframe}/{interval}) ", end="", flush=True)

        try:
            result = fast_backtest(
                strategy=strat_instance, candles=candles, symbol=SYMBOL,
                contract_size=contract_size, point=point_val,
                initial_equity=equity, risk_per_trade=RISK_PCT,
                warmup_bars=50, analyze_interval=interval,
            )

            score = compute_score(result)
            results.append((strat_name, result, score))

            status = "✅" if result.profit_factor > 1.0 and result.win_rate > 40 else "❌"
            regime_info = result.per_regime
            profitable_regimes = sum(1 for v in regime_info.values() if v.get("pnl", 0) > 0)
            total_regimes = len(regime_info)

            print(f"{status} "
                  f"T:{result.total_trades:4d} "
                  f"WR:{result.win_rate:5.1f}% "
                  f"PF:{result.profit_factor:5.2f} "
                  f"DD:{result.max_drawdown_pct:5.1f}% "
                  f"P&L:${result.total_profit_usd:8.2f} "
                  f"R:{profitable_regimes}/{total_regimes} "
                  f"{result.duration_seconds:.0f}s", flush=True)

        except BaseException as e:
            if isinstance(e, (SystemExit, KeyboardInterrupt)):
                raise
            print(f"❌ {type(e).__name__}: {str(e)[:50]}", flush=True)

    mt5.shutdown()

    if not results:
        print("\n  ⚠️ No results!")
        return

    # =============================================
    # Rankings
    # =============================================

    # 1. Overall Score
    print(f"\n\n{'='*70}")
    print(f"  📊 TOP 10 — Best Overall Score")
    print(f"{'='*70}\n")
    results.sort(key=lambda x: x[2], reverse=True)
    for rank, (name, r, score) in enumerate(results[:10], 1):
        pr = sum(1 for v in r.per_regime.values() if v.get("pnl", 0) > 0)
        tr = len(r.per_regime)
        label = "🏆" if rank == 1 else f"#{rank:2d}"
        print(f"  {label} {name:30s} Score:{score:7.1f} | "
              f"T:{r.total_trades:4d} WR:{r.win_rate:5.1f}% PF:{r.profit_factor:5.2f} "
              f"DD:{r.max_drawdown_pct:5.1f}% P&L:${r.total_profit_usd:8.2f} | R:{pr}/{tr}")

    # 2. All-Regime Champion
    def all_regime_score(item):
        name, r, score = item
        if r.total_trades < 5: return -999
        regime_info = r.per_regime
        if not regime_info: return score
        profitable = sum(1 for v in regime_info.values() if v.get("pnl", 0) > 0)
        total = len(regime_info)
        coverage = profitable / total if total > 0 else 0
        penalty = (total - profitable) * 15
        return score + coverage * 25 - penalty

    print(f"\n\n{'='*70}")
    print(f"  🏆 ALL-REGIME CHAMPION — เทรดได้ทุกสภาวะตลาด")
    print(f"{'='*70}\n")
    results.sort(key=all_regime_score, reverse=True)
    for rank, (name, r, score) in enumerate(results[:10], 1):
        ar = all_regime_score((name, r, score))
        pr = sum(1 for v in r.per_regime.values() if v.get("pnl", 0) > 0)
        tr = len(r.per_regime)
        label = "🏆" if rank == 1 else f"#{rank:2d}"
        print(f"  {label} {name:30s} AR:{ar:7.1f} Score:{score:7.1f} | "
              f"T:{r.total_trades:4d} WR:{r.win_rate:5.1f}% PF:{r.profit_factor:5.2f} "
              f"DD:{r.max_drawdown_pct:5.1f}% P&L:${r.total_profit_usd:8.2f} | R:{pr}/{tr}")

    # 3. Per-regime breakdown of top 3
    print(f"\n\n{'='*70}")
    print(f"  📈 Per-Regime Breakdown (Top 3)")
    print(f"{'='*70}")
    for rank, (name, r, score) in enumerate(results[:3], 1):
        label = "🏆" if rank == 1 else f"#{rank}"
        print(f"\n  {label} {name}")
        print(f"     Overall: T={r.total_trades} WR={r.win_rate:.1f}% PF={r.profit_factor:.2f} "
              f"DD={r.max_drawdown_pct:.1f}% P&L=${r.total_profit_usd:.2f}")
        for rname, stats in sorted(r.per_regime.items()):
            pnl = stats.get("pnl", 0)
            wr = stats.get("win_rate", 0)
            trades = stats.get("trades", 0)
            icon = "✅" if pnl > 0 else "❌"
            print(f"     {icon} {rname:20s} T:{trades:4d} WR:{wr:5.1f}% P&L:${pnl:8.2f}")

    # 4. Universal winners
    winners = [(n, r, s) for n, r, s in results
               if sum(1 for v in r.per_regime.values() if v.get("pnl", 0) > 0) == len(r.per_regime)
               and len(r.per_regime) >= 2 and r.total_trades >= 10]
    
    print(f"\n\n{'='*70}")
    if winners:
        print(f"  ✨ STRATEGIES PROFITABLE IN ALL REGIMES ({len(winners)} found)")
        print(f"{'='*70}\n")
        for n, r, s in winners:
            pr = len(r.per_regime)
            print(f"  ✨ {n:30s} T:{r.total_trades:4d} WR:{r.win_rate:5.1f}% "
                  f"PF:{r.profit_factor:5.2f} DD:{r.max_drawdown_pct:5.1f}% "
                  f"P&L:${r.total_profit_usd:.2f} | {pr}/{pr} regimes ✅")
    else:
        print(f"  ⚠️ No strategy profits in ALL regimes")
        print(f"{'='*70}")

    # Summary
    total_tests = len(results)
    profitable = sum(1 for _, r, _ in results if r.profit_factor > 1.0)
    print(f"\n{'='*70}")
    print(f"  SUMMARY: {total_tests} tested, {profitable} profitable ({profitable/max(total_tests,1)*100:.0f}%)")
    print(f"{'='*70}\n")

    # =============================================
    # Auto-Seed Backtest Routing Table
    # =============================================
    print(f"  📦 Seeding Backtest Routing Table...")
    try:
        from app.strategy.backtest_router import BacktestRouter

        # Determine db path
        db_path = str(Path(__file__).resolve().parent.parent / "data" / "sqlite" / "trading.db")

        # Ensure backtest_routing table exists
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_routing (
                symbol TEXT NOT NULL,
                regime TEXT NOT NULL,
                strategy TEXT NOT NULL,
                profit_factor REAL DEFAULT 0,
                win_rate REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                score REAL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, regime)
            )
        """)
        conn.commit()
        conn.close()

        # Build regime_results from all strategies with trades
        regime_results = []
        for strat_name, result, score in results:
            if result.total_trades < 2:
                continue
            for regime_name, stats in result.per_regime.items():
                regime_results.append({
                    "strategy": strat_name,
                    "regime": regime_name,
                    "profit_factor": result.profit_factor,
                    "win_rate": stats.get("win_rate", 0),
                    "total_trades": stats.get("trades", 0),
                    "total_pnl": stats.get("pnl", 0),
                    "score": score if stats.get("pnl", 0) > 0 else score - 50,
                })

        router = BacktestRouter(db_path=db_path)
        routes_saved = router.seed_from_backtest(SYMBOL, regime_results)
        routing = router.get_routing_table(SYMBOL)

        print(f"  ✅ Saved {routes_saved} routes to backtest_routing table")
        print(f"  📋 Routing Table for {SYMBOL}:")
        for regime, strategy in routing.get("routes", {}).items():
            metrics = routing.get("metrics", {}).get(regime, {})
            pf = metrics.get("profit_factor", 0)
            wr = metrics.get("win_rate", 0)
            pnl = metrics.get("total_pnl", 0)
            print(f"     {regime:20s} → {strategy:30s} (PF:{pf:.2f} WR:{wr:.1f}% P&L:${pnl:.2f})")
        print()

    except Exception as e:
        import traceback
        print(f"  ⚠️ Routing seed error: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
