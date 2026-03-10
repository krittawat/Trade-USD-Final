"""
Multi-Symbol Backtest — Factory & Persistence Enabled
Bot automatically switches strategies based on market regime per bar using StrategyFactory.
"""
print("DEBUG: Multi-Symbol Backtest Start", flush=True)

import sys
import os
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

# Suppress ALL library logging noise
logging.disable(logging.CRITICAL)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import MetaTrader5 as mt5
import pandas as pd

from app.core.config import get_settings
from app.execution.backtester import Backtester, BacktestTrade, BacktestResult
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile, AccountState
from app.brain.regime import classify_regime
from app.risk.sizing import calculate_lot_size
from app.strategy.factory import StrategyFactory
from app.brain.memory_store import MemoryStore
from app.brain.memory_store import MemoryStore

# ─── Inline Helper Functions (to avoid import issues) ───

def _calc_pnl(trade: BacktestTrade, contract_size: float) -> float:
    """Calculate P&L for a trade."""
    if trade.action == "BUY":
        pips = trade.exit_price - trade.entry_price
    else:
        pips = trade.entry_price - trade.exit_price
    # Pips * Lot * Contract
    return round(pips * trade.lot_size * contract_size, 2)

def _get_sl_tp(decision: Decision):
    """Extract SL/TP from decision."""
    sl = decision.stop_loss
    tp = decision.take_profit
    return sl, tp

settings = get_settings()

# ─── Symbol Config ───
SYMBOL_CONFIG = {
    # Gold: Uses app/strategy/pairs/XAUUSD or fallback
    "XAUUSDc":  {"contract_size": 100.0, "point": 0.01, "digits": 2},
    
    # Stable Forex
    "EURUSDc":  {"contract_size": 100000.0, "point": 0.00001, "digits": 5},
    "USDCADc":  {"contract_size": 100000.0, "point": 0.00001, "digits": 5},
    
    # Volatile Forex
    "GBPUSDc":  {"contract_size": 100000.0, "point": 0.00001, "digits": 5},
    "AUDUSDc":  {"contract_size": 100000.0, "point": 0.00001, "digits": 5},
    "NZDUSDc":  {"contract_size": 100000.0, "point": 0.00001, "digits": 5},
    
    # JPY
    "USDJPYc":  {"contract_size": 100000.0, "point": 0.001, "digits": 3},
}

def run_symbol_backtest(symbol: str, config: dict, factory: StrategyFactory, memory: MemoryStore, days: int = 100):
    """Run backtest for a single symbol using Factory strategy selection."""
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, utc_from, utc_to)

    if rates is None or len(rates) == 0:
        print(f"  ⚠️ No data for {symbol} — SKIPPED")
        return None, ""

    candles = pd.DataFrame(rates)
    candles["time"] = pd.to_datetime(candles["time"], unit="s")

    # Initialize Backtester context
    initial_equity = 10000.0
    equity = initial_equity
    peak_equity = equity
    max_dd_pct = 0.0
    closed_trades = []
    open_trade = None
    trade_counter = 0
    per_regime = {}
    strategy_usage = {}
    
    metrics = {"total_trades": 0, "wins": 0, "losses": 0, "consecutive_losses": 0}

    profile = SymbolProfile(
        symbol=symbol, digits=config["digits"], point=config["point"],
        contract_size=config["contract_size"], volume_min=0.01,
        volume_max=100.0, volume_step=0.01, 
        spread_avg=10, trade_mode=0, currency_profit="USD"
    )

    print(f"DEBUG: Processing {len(candles)} bars for {symbol}...")

    # Main Loop
    for i in range(300, len(candles)):
        bar_close = candles["close"].iloc[i]
        bar_high = candles["high"].iloc[i]
        bar_low = candles["low"].iloc[i]
        bar_time = candles["time"].iloc[i]

        if i % 5000 == 0:
            print(f"  > Bar {i}/{len(candles)}")

        # 1. Manage Open Trade
        if open_trade:
            hit_sl = (open_trade.action == "BUY" and bar_low <= open_trade.sl) or \
                     (open_trade.action == "SELL" and bar_high >= open_trade.sl)
            hit_tp = open_trade.tp and (
                (open_trade.action == "BUY" and bar_high >= open_trade.tp) or
                (open_trade.action == "SELL" and bar_low <= open_trade.tp))

            if hit_sl:
                open_trade.exit_price = open_trade.sl
                open_trade.exit_time = bar_time
                open_trade.exit_reason = "SL_HIT"
                pnl = _calc_pnl(open_trade, config["contract_size"])
                open_trade.profit_usd = pnl
                equity += pnl
                closed_trades.append(open_trade)
                _update_regime_stats(per_regime, open_trade)
                if pnl >= 0: metrics["wins"] += 1
                else: metrics["losses"] += 1
                metrics["total_trades"] += 1
                open_trade = None
            elif hit_tp:
                open_trade.exit_price = open_trade.tp
                open_trade.exit_time = bar_time
                open_trade.exit_reason = "TP_HIT"
                pnl = _calc_pnl(open_trade, config["contract_size"])
                open_trade.profit_usd = pnl
                equity += pnl
                closed_trades.append(open_trade)
                _update_regime_stats(per_regime, open_trade)
                metrics["wins"] += 1
                metrics["total_trades"] += 1
                open_trade = None

        # Track DD
        if equity > peak_equity: peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
        if dd > max_dd_pct: max_dd_pct = dd

        # 2. Get Strategy Signal
        window_start = max(0, i - 299)
        history = candles.iloc[window_start:i + 1]
        
        # Determine Regime
        regime = classify_regime(history)
        if not regime.actionable: continue
        
        # Select Strategy via Factory (Priority: Per-Symbol > Template)
        # Note: Factory auto-selects app/strategy/pairs/<SYMBOL>/strategy.py if registered
        strategy = factory.select_strategy(symbol, regime.regime)
        if not strategy: continue
        
        strat_name = strategy.name
        strategy_usage[strat_name] = strategy_usage.get(strat_name, 0) + 1

        try:
            decision = strategy.analyze(history, profile, regime.regime)
        except Exception:
            continue

        if decision.action not in (Action.BUY, Action.SELL):
            continue

        # 3. Entry Logic
        _sl, _tp = _get_sl_tp(decision)
        
        # New Entry
        if open_trade is None and _sl and _sl > 0:
            lot_plan = calculate_lot_size(
                decision=decision, profile=profile,
                account=AccountState(balance=equity, equity=equity, margin=0, margin_free=equity),
                settings=settings, entry_price=bar_close, stop_loss=_sl,
                performance_metrics=metrics
            )
            
            if hasattr(lot_plan, 'lot_size') and lot_plan.lot_size > 0:
                trade_counter += 1
                open_trade = BacktestTrade(
                    trade_id=trade_counter, symbol=symbol, strategy=strat_name,
                    action=decision.action.value, entry_price=bar_close, entry_time=bar_time,
                    sl=_sl, tp=_tp, lot_size=lot_plan.lot_size, regime=regime.regime.value
                )

        # Reverse Trade
        elif open_trade and open_trade.action != decision.action.value:
            # Close existing
            open_trade.exit_price = bar_close
            open_trade.exit_time = bar_time
            open_trade.exit_reason = "REVERSE"
            pnl = _calc_pnl(open_trade, config["contract_size"])
            open_trade.profit_usd = pnl
            equity += pnl
            closed_trades.append(open_trade)
            _update_regime_stats(per_regime, open_trade)
            if pnl >= 0: metrics["wins"] += 1
            else: metrics["losses"] += 1
            metrics["total_trades"] += 1
            
            # Open new
            lot_plan = calculate_lot_size(
                decision=decision, profile=profile,
                account=AccountState(balance=equity, equity=equity, margin=0, margin_free=equity),
                settings=settings, entry_price=bar_close, stop_loss=_sl,
                performance_metrics=metrics
            )
            if hasattr(lot_plan, 'lot_size') and lot_plan.lot_size > 0:
                trade_counter += 1
                open_trade = BacktestTrade(
                    trade_id=trade_counter, symbol=symbol, strategy=strat_name,
                    action=decision.action.value, entry_price=bar_close, entry_time=bar_time,
                    sl=_sl, tp=_tp, lot_size=lot_plan.lot_size, regime=regime.regime.value
                )

    # End: Close open trade
    if open_trade:
        open_trade.exit_price = candles["close"].iloc[-1]
        open_trade.exit_time = candles["time"].iloc[-1]
        open_trade.exit_reason = "END"
        pnl = _calc_pnl(open_trade, config["contract_size"])
        open_trade.profit_usd = pnl
        equity += pnl
        closed_trades.append(open_trade)
        _update_regime_stats(per_regime, open_trade)

    # 4. Results & Persistence
    wins = sum(1 for t in closed_trades if t.profit_usd >= 0)
    total = len(closed_trades)
    wr = round(wins / total * 100, 1) if total > 0 else 0
    gross_profit = sum(t.profit_usd for t in closed_trades if t.profit_usd > 0)
    gross_loss = abs(sum(t.profit_usd for t in closed_trades if t.profit_usd < 0))
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 99.99
    
    result_data = {
        "win_rate": wr,
        "profit_factor": pf,
        "total_trades": total,
        "total_pnl": round(equity - initial_equity, 2),
        "max_dd": round(max_dd_pct, 1)
    }

    # Save to SQLite
    top_strategy = max(strategy_usage, key=strategy_usage.get) if strategy_usage else "unknown"
    memory.save_backtest_run(
        strategy_name=top_strategy,
        symbol=symbol,
        timeframe="M5",
        config=config,
        result=result_data
    )

    strat_info = ", ".join(f"{k}:{v}" for k, v in strategy_usage.items())
    
    return BacktestResult(
        symbol=symbol, strategy=top_strategy,
        start_date=str(candles["time"].iloc[300]), end_date=str(candles["time"].iloc[-1]),
        total_bars=len(candles), total_trades=total,
        winning_trades=wins, losing_trades=total-wins,
        win_rate=wr, profit_factor=pf,
        total_profit_usd=result_data["total_pnl"],
        max_drawdown_pct=max_dd_pct,
        max_drawdown_usd=0, expectancy=0, avg_rr=0, sharpe_ratio=0, avg_bars_held=0,
        initial_equity=initial_equity, final_equity=equity,
        trades=closed_trades, per_regime=per_regime
    ), strat_info

def _update_regime_stats(per_regime: dict, trade: BacktestTrade):
    r = trade.regime
    if r not in per_regime: per_regime[r] = {"wins": 0, "losses": 0, "pnl": 0.0}
    if trade.profit_usd >= 0: per_regime[r]["wins"] += 1
    else: per_regime[r]["losses"] += 1
    per_regime[r]["pnl"] = round(per_regime[r]["pnl"] + trade.profit_usd, 2)
    t = per_regime[r]["wins"] + per_regime[r]["losses"]
    per_regime[r]["win_rate"] = round(per_regime[r]["wins"] / t * 100, 1) if t > 0 else 0

def main():
    print("=" * 60)
    print("🚀 MULTI-SYMBOL BACKTEST (Factory & Persistence)")
    print("=" * 60)

    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return

    # Init Factory & Memory
    factory = StrategyFactory()
    factory.auto_register()
    
    memory = MemoryStore()
    memory.connect()

    DAYS = 100
    results = {}
    strat_infos = {}

    for symbol, config in SYMBOL_CONFIG.items():
        print(f"\n{'─'*40}")
        print(f"📊 {symbol}")
        print(f"{'─'*40}")

        result, strat_info = run_symbol_backtest(symbol, config, factory, memory, DAYS)
        strat_infos[symbol] = strat_info
        if result:
            results[symbol] = result
            print(f"  Strategy: {strat_info}")
            print(f"  Trades: {result.total_trades} | WR: {result.win_rate}% | PF: {result.profit_factor} | P&L: ${result.total_profit_usd:.2f} | DD: {result.max_drawdown_pct}%")
        else:
            print(f"  ⚠️ No result")

    # ─── Summary Table ───
    print("\n" + "=" * 80)
    print("📋 SUMMARY: ALL SYMBOLS (Factory Selected)")
    print("=" * 80)
    print(f"{'Symbol':<12} {'Strategy':<30} {'Trades':>6} {'WR%':>6} {'PF':>6} {'P&L':>10} {'DD%':>6}")
    print("-" * 80)

    total_pnl = 0.0
    for symbol, result in results.items():
        strat = strat_infos.get(symbol, "unknown")
        strat_short = strat[:28] if len(strat) > 28 else strat
        pnl = result.total_profit_usd
        total_pnl += pnl
        marker = "✅" if pnl >= 0 else "❌"
        print(f"{symbol:<12} {strat_short:<30} {result.total_trades:>6} {result.win_rate:>5}% {result.profit_factor:>5} {pnl:>+10.2f} {result.max_drawdown_pct:>5}% {marker}")

    print("-" * 80)
    print(f"{'TOTAL P&L':<60} {total_pnl:>+10.2f}")
    print("=" * 80)

    mt5.shutdown()
    print("\n✅ Multi-Symbol Backtest Complete!")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
print("DEBUG: Multi-Symbol Backtest Start", flush=True)

import sys
import os
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

# Suppress ALL library logging noise
logging.disable(logging.CRITICAL)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import MetaTrader5 as mt5
import pandas as pd

from app.core.config import get_settings
from app.execution.backtester import Backtester, BacktestTrade, BacktestResult
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile, AccountState
from app.brain.regime import classify_regime
from app.risk.sizing import calculate_lot_size
from app.risk.trailing import TrailingManager
from scripts.backtest_full import FullFeatureBacktester, _get_sl_tp

settings = get_settings()

# ─── Symbol Config ───
SYMBOL_CONFIG = {
    # Gold: optimized (fixed strategy)
    "XAUUSDc":  {"strategy": "gold_scalp_pro",   "contract_size": 100.0, "point": 0.01, "digits": 2},
    
    # Stable Forex: Tight SL (2.0), High RR (3.0)
    "EURUSDc":  {"strategy": "regime_adaptive", "sl_mult": 2.0, "rr": 2.0, "contract_size": 100000.0, "point": 0.00001, "digits": 5},
    "USDCADc":  {"strategy": "regime_adaptive", "sl_mult": 2.0, "rr": 3.0, "contract_size": 100000.0, "point": 0.00001, "digits": 5},
    
    # Volatile Forex: Wide SL (3.0), Moderate RR (2.0) — prevents sweeps
    "GBPUSDc":  {"strategy": "regime_adaptive", "sl_mult": 3.0, "rr": 2.0, "contract_size": 100000.0, "point": 0.00001, "digits": 5},
    "AUDUSDc":  {"strategy": "regime_adaptive", "sl_mult": 3.0, "rr": 2.0, "contract_size": 100000.0, "point": 0.00001, "digits": 5},
    "NZDUSDc":  {"strategy": "regime_adaptive", "sl_mult": 3.0, "rr": 2.0, "contract_size": 100000.0, "point": 0.00001, "digits": 5},
    
    # JPY: Special handling (Point=0.001)
    "USDJPYc":  {"strategy": "regime_adaptive", "sl_mult": 2.0, "rr": 3.0, "contract_size": 100000.0, "point": 0.001, "digits": 3},
}


# ─── Regime-Adaptive Backtester ───
class RegimeAdaptiveBacktester:
    """
    Backtester that dynamically switches strategies based on market regime.
    TRENDING → forex_precision
    RANGING  → ranging_sniper
    """

    def __init__(self, initial_equity: float = 10000.0):
        from app.strategy.templates.forex_precision import ForexPrecisionStrategy
        from app.strategy.templates.ranging_sniper import RangingSniperStrategy
        from app.strategy.templates.trend_rider import TrendRiderStrategy

        self.trending_strategy = ForexPrecisionStrategy()
        self.ranging_strategy = RangingSniperStrategy()
        self.trend_rider_strategy = TrendRiderStrategy()
        self.initial_equity = initial_equity

    def _select_strategy(self, regime_type: RegimeType):
        """Select best strategy for current regime."""
        if regime_type in (RegimeType.RANGING, RegimeType.LOW_VOLATILITY):
            return self.ranging_strategy, "ranging_sniper"
        elif regime_type == RegimeType.TRENDING_UP or regime_type == RegimeType.TRENDING_DOWN:
            return self.trending_strategy, "forex_precision"
        else:
            return self.trending_strategy, "forex_precision"

    def run(self, candles: pd.DataFrame, symbol: str,
            contract_size: float = 100000.0, point: float = 0.00001,
            digits: int = 5, sl_mult: float = 2.0, rr: float = 3.0) -> BacktestResult:
        """Run regime-adaptive backtest."""
        equity = self.initial_equity
        peak_equity = equity
        max_dd_pct = 0.0
        closed_trades = []
        open_trade = None
        trade_counter = 0
        per_regime = {}
        strategy_usage = {}

        profile = SymbolProfile(
            symbol=symbol, digits=digits, point=point,
            contract_size=contract_size, volume_min=0.01,
            volume_max=100.0, volume_step=0.01,
            spread_avg=0.0, trade_mode=0, currency_profit="USD"
        )
        
        metrics = {"total_trades": 0, "wins": 0, "losses": 0, "consecutive_losses": 0}

        print(f"DEBUG: Processing {len(candles)} bars (Regime-Adaptive)...")

        for i in range(300, len(candles)):
            # ... (time/price extraction same as before)
            bar_close = candles["close"].iloc[i]
            bar_high = candles["high"].iloc[i]
            bar_low = candles["low"].iloc[i]
            bar_time = candles["time"].iloc[i]

            if i % 1000 == 0:
                print(f"DEBUG: Bar {i}/{len(candles)}")

            # ─── SL/TP Check on open trade ───
            if open_trade:
                hit_sl = (open_trade.action == "BUY" and bar_low <= open_trade.sl) or \
                         (open_trade.action == "SELL" and bar_high >= open_trade.sl)
                hit_tp = open_trade.tp and (
                    (open_trade.action == "BUY" and bar_high >= open_trade.tp) or
                    (open_trade.action == "SELL" and bar_low <= open_trade.tp))

                if hit_sl:
                    open_trade.exit_price = open_trade.sl
                    open_trade.exit_time = bar_time
                    open_trade.exit_reason = "SL_HIT"
                    pnl = _calc_pnl(open_trade, contract_size)
                    open_trade.profit_usd = pnl
                    equity += pnl
                    closed_trades.append(open_trade)
                    _update_regime_stats(per_regime, open_trade)
                    if pnl >= 0: metrics["wins"] += 1
                    else: metrics["losses"] += 1; metrics["consecutive_losses"] += 1
                    metrics["total_trades"] += 1
                    open_trade = None
                elif hit_tp:
                    open_trade.exit_price = open_trade.tp
                    open_trade.exit_time = bar_time
                    open_trade.exit_reason = "TP_HIT"
                    pnl = _calc_pnl(open_trade, contract_size)
                    open_trade.profit_usd = pnl
                    equity += pnl
                    closed_trades.append(open_trade)
                    _update_regime_stats(per_regime, open_trade)
                    metrics["wins"] += 1; metrics["consecutive_losses"] = 0
                    metrics["total_trades"] += 1
                    open_trade = None

            # Track DD
            if equity > peak_equity:
                peak_equity = equity
            dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
            if dd > max_dd_pct:
                max_dd_pct = dd

            # ─── Regime Detection ───
            window_start = max(0, i - 299)
            history = candles.iloc[window_start:i + 1]
            if len(history) < 50:
                continue

            regime = classify_regime(history)
            if not regime.actionable:
                continue

            # ─── Forex Regime Safety Filter ───
            if regime.regime in (RegimeType.HIGH_VOLATILITY, RegimeType.UNKNOWN):
                continue
            if regime.regime == RegimeType.NEWS_SPIKE:
                continue

            # ─── Dynamic Strategy Selection ───
            strategy, strat_name = self._select_strategy(regime.regime)
            strategy_usage[strat_name] = strategy_usage.get(strat_name, 0) + 1

            # ─── Get Signal ───
            try:
                # Pass SL/RR overrides if supported
                if strat_name == "forex_precision":
                     decision = strategy.analyze(history, profile, regime.regime, sl_atr_mult=sl_mult, rr_target=rr)
                else:
                     decision = strategy.analyze(history, profile, regime.regime)
            except Exception:
                continue


            if decision.action not in (Action.BUY, Action.SELL):
                continue

            # ─── Entry (only if no open trade) ───
            _sl, _tp = _get_sl_tp(decision)
            if open_trade is None and _sl and _sl > 0:
                # Lot sizing
                r_pct = getattr(decision, 'risk_pct', None)
                if r_pct is not None and r_pct < 1.0:
                    r_pct *= 100.0

                sizing_decision = Decision(
                    symbol=symbol, action=decision.action,
                    confidence=decision.confidence,
                    reason=getattr(decision, 'reason', ''),
                    stop_loss=_sl, take_profit=_tp,
                    risk_pct=r_pct,
                    strategy_name=strat_name, timeframe="M5"
                )
                account_state = AccountState(
                    balance=equity, equity=equity, margin=0, margin_free=equity
                )
                try:
                    plan = calculate_lot_size(
                        decision=sizing_decision, profile=profile,
                        account=account_state, settings=settings,
                        entry_price=bar_close, stop_loss=_sl,
                        performance_metrics=metrics
                    )
                except Exception:
                    continue

                if hasattr(plan, 'lot_size'):
                    trade_counter += 1
                    open_trade = BacktestTrade(
                        trade_id=trade_counter, symbol=symbol,
                        strategy=strat_name,
                        action=decision.action.value,
                        entry_price=bar_close, entry_time=bar_time,
                        sl=_sl, tp=_tp,
                        lot_size=plan.lot_size,
                        regime=regime.regime.value
                    )

            # ─── Reverse Logic ───
            elif open_trade and open_trade.action != decision.action.value:
                open_trade.exit_price = bar_close
                open_trade.exit_time = bar_time
                open_trade.exit_reason = "REVERSE"
                pnl = _calc_pnl(open_trade, contract_size)
                open_trade.profit_usd = pnl
                equity += pnl
                closed_trades.append(open_trade)
                _update_regime_stats(per_regime, open_trade)
                if pnl >= 0: metrics["wins"] += 1; metrics["consecutive_losses"] = 0
                else: metrics["losses"] += 1; metrics["consecutive_losses"] += 1
                metrics["total_trades"] += 1
                open_trade = None

        # Close any remaining trade
        if open_trade:
            open_trade.exit_price = candles["close"].iloc[-1]
            open_trade.exit_time = candles["time"].iloc[-1]
            open_trade.exit_reason = "END"
            pnl = _calc_pnl(open_trade, contract_size)
            open_trade.profit_usd = pnl
            equity += pnl
            closed_trades.append(open_trade)
            _update_regime_stats(per_regime, open_trade)

        # ─── Build Result ───
        wins = sum(1 for t in closed_trades if t.profit_usd >= 0)
        losses = sum(1 for t in closed_trades if t.profit_usd < 0)
        total = len(closed_trades)
        wr = round(wins / total * 100, 1) if total > 0 else 0
        gross_profit = sum(t.profit_usd for t in closed_trades if t.profit_usd > 0)
        gross_loss = abs(sum(t.profit_usd for t in closed_trades if t.profit_usd < 0))
        pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else float('inf') if gross_profit > 0 else 0

        # Strategy usage stats
        strat_info = ", ".join(f"{k}:{v}" for k, v in strategy_usage.items())

        return BacktestResult(
            symbol=symbol, strategy="regime_adaptive",
            start_date=str(candles["time"].iloc[300]),
            end_date=str(candles["time"].iloc[-1]),
            total_bars=len(candles),
            total_trades=total,
            winning_trades=wins, losing_trades=losses,
            win_rate=wr, profit_factor=pf,
            total_profit_usd=round(equity - self.initial_equity, 2),
            max_drawdown_pct=round(max_dd_pct, 1),
            max_drawdown_usd=round(peak_equity * max_dd_pct / 100, 2),
            expectancy=round((equity - self.initial_equity) / total, 2) if total > 0 else 0,
            avg_rr=0.0, sharpe_ratio=0.0, avg_bars_held=0.0,
            initial_equity=self.initial_equity,
            final_equity=round(equity, 2),
            trades=closed_trades, per_regime=per_regime,
        ), strat_info


def _calc_pnl(trade: BacktestTrade, contract_size: float) -> float:
    """Calculate P&L for a trade."""
    if trade.action == "BUY":
        pips = trade.exit_price - trade.entry_price
    else:
        pips = trade.entry_price - trade.exit_price
    return round(pips * trade.lot_size * contract_size, 2)


def _update_regime_stats(per_regime: dict, trade: BacktestTrade):
    """Track regime-level performance."""
    r = trade.regime
    if r not in per_regime:
        per_regime[r] = {"wins": 0, "losses": 0, "pnl": 0.0}
    if trade.profit_usd >= 0:
        per_regime[r]["wins"] += 1
    else:
        per_regime[r]["losses"] += 1
    per_regime[r]["pnl"] = round(per_regime[r]["pnl"] + trade.profit_usd, 2)
    total = per_regime[r]["wins"] + per_regime[r]["losses"]
    per_regime[r]["win_rate"] = round(per_regime[r]["wins"] / total * 100, 1) if total > 0 else 0


def load_strategy(name: str):
    """Load strategy instance by name."""
    if name == "gold_scalp_pro":
        from app.strategy.templates.gold_scalp_pro import GoldScalpProStrategy
        return GoldScalpProStrategy()
    elif name == "forex_precision":
        from app.strategy.templates.forex_precision import ForexPrecisionStrategy
        return ForexPrecisionStrategy()
    else:
        raise ValueError(f"Unknown strategy: {name}")


def run_symbol_backtest(symbol: str, config: dict, days: int = 100):
    """Run backtest for a single symbol."""
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, utc_from, utc_to)

    if rates is None or len(rates) == 0:
        print(f"  ⚠️ No data for {symbol} — SKIPPED")
        return None, ""

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")

    strategy_name = config["strategy"]

    if strategy_name == "regime_adaptive":
        # New: Get overrides or defaults
        sl_mult = config.get("sl_mult", 2.0)
        rr = config.get("rr", 3.0)

        # Use regime-adaptive backtester for Forex
        bt = RegimeAdaptiveBacktester(initial_equity=10000.0)
        result, strat_info = bt.run(
            df, symbol,
            contract_size=config["contract_size"],
            point=config["point"],
            digits=config.get("digits", 5),
            sl_mult=sl_mult,
            rr=rr
        )
        return result, f"regime_adaptive ({strat_info})"
    else:
        # Use original backtester for Gold
        strategy = load_strategy(strategy_name)
        bt = FullFeatureBacktester(strategy, initial_equity=10000.0)
        result = bt.run(df, symbol,
                        contract_size=config["contract_size"],
                        point=config["point"])
        return result, strategy_name


def main():
    print("=" * 60)
    print("🚀 MULTI-SYMBOL BACKTEST (Regime-Adaptive)")
    print("=" * 60)

    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return

    DAYS = 100
    results = {}
    strat_infos = {}

    for symbol, config in SYMBOL_CONFIG.items():
        print(f"\n{'─'*40}")
        print(f"📊 {symbol}")
        print(f"{'─'*40}")

        result, strat_info = run_symbol_backtest(symbol, config, DAYS)
        strat_infos[symbol] = strat_info
        if result:
            results[symbol] = result
            print(f"  Strategy: {strat_info}")
            print(f"  Trades: {result.total_trades} | WR: {result.win_rate}% | PF: {result.profit_factor} | P&L: ${result.total_profit_usd:.2f} | DD: {result.max_drawdown_pct}%")
        else:
            print(f"  ⚠️ No result")

    # ─── Summary Table ───
    print("\n" + "=" * 80)
    print("📋 SUMMARY: ALL SYMBOLS (Regime-Adaptive)")
    print("=" * 80)
    print(f"{'Symbol':<12} {'Strategy':<30} {'Trades':>6} {'WR%':>6} {'PF':>6} {'P&L':>10} {'DD%':>6}")
    print("-" * 80)

    total_pnl = 0.0
    total_trades = 0

    for symbol, result in results.items():
        strat = strat_infos.get(symbol, "unknown")
        # Truncate strategy info for display
        strat_short = strat[:28] if len(strat) > 28 else strat
        pnl = result.total_profit_usd
        total_pnl += pnl
        total_trades += result.total_trades
        marker = "✅" if pnl >= 0 else "❌"
        print(f"{symbol:<12} {strat_short:<30} {result.total_trades:>6} {result.win_rate:>5}% {result.profit_factor:>5} {pnl:>+10.2f} {result.max_drawdown_pct:>5}% {marker}")

    print("-" * 80)
    print(f"{'TOTAL':<42} {total_trades:>6} {'':>6} {'':>6} {total_pnl:>+10.2f}")
    print("=" * 80)

    # Per-regime breakdown for Gold
    if "XAUUSDc" in results:
        print("\n📊 XAUUSDc Regime Breakdown:")
        for r, stats in results["XAUUSDc"].per_regime.items():
            print(f"  {r:15s}: {stats['win_rate']}% WR | ${stats['pnl']} P&L")

    # Per-regime breakdown for Forex
    for symbol in ["EURUSDc", "GBPUSDc", "USDJPYc", "AUDUSDc", "USDCADc", "NZDUSDc"]:
        if symbol in results and results[symbol].per_regime:
            print(f"\n📊 {symbol} Regime Breakdown:")
            for r, stats in results[symbol].per_regime.items():
                print(f"  {r:15s}: {stats['win_rate']}% WR | ${stats['pnl']} P&L")

    mt5.shutdown()
    print("\n✅ Multi-Symbol Backtest Complete!")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
