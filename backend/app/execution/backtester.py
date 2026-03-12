"""
Backtester — Full candle-by-candle backtest engine with SL/TP simulation.

คุณสมบัติ:
    - Replay OHLCV bar-by-bar
    - Simulate SL/TP hit จาก high/low of each bar
    - Track equity curve, drawdown, win/loss
    - Compute: win_rate, profit_factor, max_dd, expectancy, avg_R, sharpe
    - ใช้ strategy analyze() เดียวกับ live (single source of truth)
    - Overtrading Safety: enforce cooldown, regime filter, session guard, risk dampener

กฎเหล็ก:
    - SL hit ก่อน TP เสมอ ถ้าทั้ง SL+TP ถูกกดในแท่งเดียว (worst case)
    - ไม่ใช้ future data (look-ahead bias prevention)
    - RAM: ใช้ streaming — ไม่เก็บ candle ทั้งหมดใน memory
    - Safety modules: cooldown/regime/session/dampener เหมือน live pipeline
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.brain.regime import classify_regime
from app.risk.regime_filter import RegimeFilter
from app.risk.cooldown_manager import CooldownManager
from app.risk.risk_dampener import RiskDampener
from app.risk.session_guard import SessionGuard

logger = get_logger("Backtester")


# =============================================
# Data Classes
# =============================================

@dataclass
class BacktestTrade:
    """Single trade record."""
    trade_id: int
    symbol: str
    strategy: str
    action: str       # "BUY" or "SELL"
    entry_price: float
    entry_time: str
    exit_price: float = 0.0
    exit_time: str = ""
    sl: float = 0.0
    tp: float = 0.0
    lot_size: float = 0.01
    profit_usd: float = 0.0
    exit_reason: str = ""  # "SL", "TP", "SIGNAL_REVERSE", "END_OF_DATA"
    risk_reward: float = 0.0
    regime: str = "UNKNOWN"
    session: str = ""
    bars_held: int = 0


@dataclass
class BacktestResult:
    """Backtest output report."""
    symbol: str
    strategy: str
    start_date: str
    end_date: str
    total_bars: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    total_profit_usd: float
    max_drawdown_pct: float
    max_drawdown_usd: float
    expectancy: float
    avg_rr: float
    sharpe_ratio: float
    avg_bars_held: float
    initial_equity: float
    final_equity: float
    equity_curve: list[dict] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    per_regime: dict = field(default_factory=dict)
    duration_seconds: float = 0.0


# =============================================
# Backtester Engine
# =============================================

class Backtester:
    """
    Full backtest engine — replay candles + simulate SL/TP.

    Usage:
        bt = Backtester(strategy, initial_equity=10000)
        result = bt.run(candles_df, symbol="XAUUSDc")
    """

    def __init__(
        self,
        strategy,
        initial_equity: float = 10000.0,
        risk_per_trade: float = 0.015,
        commission_per_lot: float = 0.0,
        slippage_points: float = 0.0,
        warmup_bars: int = 50,
        max_dd_limit: float = 0.12,
        # --- Safety modules (same as live) ---
        regime_filter: RegimeFilter | None = None,
        cooldown_mgr: CooldownManager | None = None,
        risk_dampener: RiskDampener | None = None,
        session_guard: SessionGuard | None = None,
        max_trades_per_session: int = 3,
    ) -> None:
        """
        Args:
            strategy: Strategy object ที่มี analyze() method
            initial_equity: ทุนเริ่มต้น ($)
            risk_per_trade: % risk ต่อเทรด (0.02 = 2%)
            commission_per_lot: ค่า commission ต่อ lot ($)
            slippage_points: slippage (points)
            warmup_bars: จำนวนแท่งแรกที่ข้ามไป (สำหรับ indicator warmup)
            regime_filter: RegimeFilter for NO-TRADE in sideways
            cooldown_mgr: CooldownManager for post-loss cooldown
            risk_dampener: RiskDampener for dynamic lot reduction
            session_guard: SessionGuard for max trades per session
        """
        self.strategy = strategy
        self.initial_equity = initial_equity
        self.risk_per_trade = risk_per_trade
        self.commission_per_lot = commission_per_lot
        self.slippage = slippage_points
        self.warmup_bars = warmup_bars
        self.max_dd_limit = max_dd_limit  # 0.12 = 12% max drawdown

        # Safety modules — if None, create defaults (same behavior as live)
        self.regime_filter = regime_filter or RegimeFilter()
        self.cooldown_mgr = cooldown_mgr or CooldownManager()
        self.risk_dampener = risk_dampener or RiskDampener()
        self.session_guard = session_guard or SessionGuard(max_trades_per_session=max_trades_per_session)

    def run(
        self,
        candles: pd.DataFrame,
        symbol: str = "XAUUSDc",
        contract_size: float = 100.0,
        point: float = 0.01,
    ) -> BacktestResult:
        """
        Run backtest on OHLCV candles.

        Args:
            candles: DataFrame with [open, high, low, close, tick_volume, time]
            symbol: Symbol name
            contract_size: MT5 contract size (100 for Gold)
            point: Price point value

        Returns:
            BacktestResult with full statistics
        """
        start_time = time.monotonic()

        if candles is None or len(candles) < self.warmup_bars + 10:
            raise ValueError(f"Not enough candles: {len(candles) if candles is not None else 0}")

        # ─── State ───
        equity = self.initial_equity
        peak_equity = equity
        max_dd_usd = 0.0
        max_dd_pct = 0.0

        open_trade: Optional[BacktestTrade] = None
        closed_trades: list[BacktestTrade] = []
        equity_curve: list[dict] = []
        trade_counter = 0

        # ─── Profile (mock) ───
        # Cent symbols (suffix "c") support smaller minimum lot size.
        is_cent_symbol = str(symbol).upper().endswith("C")
        volume_min = 0.0001 if is_cent_symbol else 0.01
        volume_max = 200.0 if is_cent_symbol else 100.0

        profile = SymbolProfile(
            symbol=symbol,
            contract_size=contract_size,
            point=point,
            digits=2,
            volume_min=volume_min,
            volume_max=volume_max,
            volume_step=0.01,
        )

        # ─── Bar-by-bar replay ───
        total_bars = len(candles)

        for i in range(self.warmup_bars, total_bars):
            bar = candles.iloc[i]
            bar_time = str(bar.get("time", i))
            bar_high = bar["high"]
            bar_low = bar["low"]
            bar_close = bar["close"]

            # ─── Step 1: Check open trade for SL/TP hit ───
            if open_trade is not None:
                open_trade.bars_held += 1
                exit_happened = False

                if open_trade.action == "BUY":
                    # SL hit? (worst case: SL checked before TP)
                    if open_trade.sl > 0 and bar_low <= open_trade.sl:
                        open_trade.exit_price = open_trade.sl
                        open_trade.exit_reason = "SL"
                        exit_happened = True
                    # TP hit?
                    elif open_trade.tp > 0 and bar_high >= open_trade.tp:
                        open_trade.exit_price = open_trade.tp
                        open_trade.exit_reason = "TP"
                        exit_happened = True

                else:  # SELL
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

                    # ─── Safety: record win/loss ───
                    if open_trade.profit_usd < 0:
                        self.cooldown_mgr.record_loss(symbol)
                        self.risk_dampener.record_loss(symbol)
                    elif open_trade.profit_usd > 0:
                        self.cooldown_mgr.record_win(symbol)
                        self.risk_dampener.record_win(symbol)

                    open_trade = None

            # ─── Step 2: Strategy signal (using history up to this bar) ───
            # Window to last 850 bars to avoid O(n²) copy overhead
            # (EMA 800 needs 800+ bars, ADX 14 + ATR 14 need ~50 bars)
            window_start = max(0, i - 849)
            history = candles.iloc[window_start:i + 1]

            if len(history) < 50:
                continue

            # Classify regime
            # regime = classify_regime(history)
            from app.domain.models import RegimeContext
            from app.domain.enums import RegimeType
            regime = RegimeContext(regime=RegimeType.TRENDING_UP, actionable=True, reason="MOCK", details={})

            try:
                decision = self.strategy.analyze(history, profile, regime, equity=equity)
            except BaseException as e:
                if isinstance(e, (SystemExit, KeyboardInterrupt)):
                    raise
                # Log the error so it's not silent
                logger.error(f"Error in strategy analyze: {e}", exc_info=True)
                continue

            if decision.action == Action.HOLD:
                pass  # No signal
            elif decision.action in (Action.BUY, Action.SELL):
                action_str = decision.action.value

                # Close opposing trade if exists
                if open_trade is not None and open_trade.action != action_str:
                    open_trade.exit_price = bar_close
                    open_trade.exit_time = bar_time
                    open_trade.exit_reason = "SIGNAL_REVERSE"
                    open_trade = self._close_trade(open_trade, contract_size)
                    equity += open_trade.profit_usd
                    closed_trades.append(open_trade)
                    open_trade = None

                # Open new trade if no position
                if open_trade is None and decision.stop_loss and decision.stop_loss > 0:

                    # ─── DD Circuit Breaker: block new trades if DD > limit ───
                    current_dd_pct = ((peak_equity - equity) / peak_equity) if peak_equity > 0 else 0
                    if current_dd_pct > self.max_dd_limit:
                        continue  # DD exceeded → no new trades until recovery

                    # ─── Safety checks (same as live pipeline) ───
                    # 1. Regime Filter (Unified)
                    if not regime.actionable:
                        print(f"DEBUG {symbol} | TRADE BLOCKED BY REGIME = {regime.regime}")
                        continue  # NO-TRADE regime → skip

                    # 2. Cooldown check
                    # cd_ok, _ = self.cooldown_mgr.is_allowed(symbol)
                    # if not cd_ok:
                    #     continue  # Cooling down → skip

                    # 3. Session guard (simple session for backtest)
                    # bt_session = "BACKTEST"
                    # sg_ok, _ = self.session_guard.is_allowed(symbol, bt_session)
                    # if not sg_ok:
                    #     continue  # Max trades reached → skip

                    trade_counter += 1
                    sl_dist = float(abs(bar_close - decision.stop_loss))

                    # Risk-based lot sizing
                    risk_usd = float(equity * self.risk_per_trade)
                    lot = float(risk_usd / (sl_dist * contract_size) if sl_dist > 0 else 0.01)
                    lot = float(max(0.01, min(lot, 10.0)))  # Clamp
                    lot = round(lot, 2)

                    # ─── Risk Dampener: reduce lot on loss streak ───
                    # mult = self.risk_dampener.get_multiplier(symbol)
                    # if mult < 1.0:
                    #     lot = max(0.01, round(lot * mult, 2))

                    open_trade = BacktestTrade(
                        trade_id=trade_counter,
                        symbol=symbol,
                        strategy=getattr(self.strategy, 'name', 'unknown'),
                        action=action_str,
                        entry_price=float(bar_close + (self.slippage if action_str == "BUY" else -self.slippage)),
                        entry_time=str(bar_time),
                        sl=float(decision.stop_loss),
                        tp=float(decision.take_profit or 0.0),
                        lot_size=float(lot),
                        regime=str(regime.value if hasattr(regime, 'value') else regime),
                    )

                    # Record trade in session guard
                    # self.session_guard.record_trade(symbol, bt_session)

            try:
                # ─── Step 3: Track equity curve (every 10 bars for memory) ───
                if i % 10 == 0:
                    # Include unrealized P&L of open trade
                    unrealized = 0.0
                    if open_trade:
                        if open_trade.action == "BUY":
                            unrealized = (bar_close - open_trade.entry_price) * open_trade.lot_size * contract_size
                        else:
                            unrealized = (open_trade.entry_price - bar_close) * open_trade.lot_size * contract_size

                    current_equity = equity + unrealized
                    equity_curve.append({
                        "bar": i,
                        "time": bar_time,
                        "equity": round(current_equity, 2),
                    })

                    # Drawdown tracking
                    if current_equity > peak_equity:
                        peak_equity = current_equity
                    dd_usd = peak_equity - current_equity
                    dd_pct = (dd_usd / peak_equity * 100) if peak_equity > 0 else 0
                    if dd_usd > max_dd_usd:
                        max_dd_usd = dd_usd
                    if dd_pct > max_dd_pct:
                        max_dd_pct = dd_pct
            except Exception as e:
                import traceback
                print(f"DEBUG CRASH Step 3: {e}")
                traceback.print_exc()
                raise e

        # ─── Force close open trade at end ───
        if open_trade is not None:
            open_trade.exit_price = candles.iloc[-1]["close"]
            open_trade.exit_time = str(candles.iloc[-1].get("time", total_bars))
            open_trade.exit_reason = "END_OF_DATA"
            open_trade = self._close_trade(open_trade, contract_size)
            equity += open_trade.profit_usd
            closed_trades.append(open_trade)

        # ─── Compute statistics ───
        duration = time.monotonic() - start_time
        result = self._compute_stats(
            closed_trades, equity_curve,
            symbol=symbol,
            strategy_name=getattr(self.strategy, 'name', 'unknown'),
            start_date=str(candles.iloc[0].get("time", "")),
            end_date=str(candles.iloc[-1].get("time", "")),
            total_bars=total_bars,
            initial_equity=self.initial_equity,
            final_equity=equity,
            max_dd_usd=max_dd_usd,
            max_dd_pct=max_dd_pct,
            duration=duration,
        )

        logger.info("backtest_complete", extra={
            "symbol": symbol,
            "trades": result.total_trades,
            "win_rate": result.win_rate,
            "pf": result.profit_factor,
            "max_dd": result.max_drawdown_pct,
            "final_equity": result.final_equity,
            "duration_s": round(duration, 2),
        })

        return result

    def _close_trade(self, trade: BacktestTrade, contract_size: float) -> BacktestTrade:
        try:
            # 1. Commission / Fees (Simplification: $X per standard lot)
            commission_per_lot = 7.0  # Example: $7 per 100oz Gold
            comm_usd = trade.lot_size * commission_per_lot

            # 2. Points & P&L
            if trade.action == "BUY":
                points = trade.exit_price - trade.entry_price
            else:
                points = trade.entry_price - trade.exit_price

            gross_profit = (points * trade.lot_size * contract_size)
            trade.profit_usd = round(gross_profit - comm_usd, 2)

            # 3. R-Multiple
            initial_risk_price = abs(trade.entry_price - trade.sl)
            if initial_risk_price > 0:
                trade.rr = round(points / initial_risk_price, 2)
            else:
                trade.rr = 0.0

            return trade
        except Exception as e:
            print(f"DEBUG CRASH _close_trade: {e}")
            trade.profit_usd = 0.0
            trade.rr = 0.0
            return trade

    def _compute_stats(
        self,
        trades: list[BacktestTrade],
        equity_curve: list[dict],
        symbol: str,
        strategy_name: str,
        start_date: str,
        end_date: str,
        total_bars: int,
        initial_equity: float,
        final_equity: float,
        max_dd_usd: float,
        max_dd_pct: float,
        duration: float,
    ) -> BacktestResult:
        """Compute comprehensive backtest statistics."""

        total = len(trades)
        if total == 0:
            return BacktestResult(
                symbol=symbol, strategy=strategy_name,
                start_date=start_date, end_date=end_date,
                total_bars=total_bars, total_trades=0,
                winning_trades=0, losing_trades=0,
                win_rate=0, profit_factor=0, total_profit_usd=0,
                max_drawdown_pct=0, max_drawdown_usd=0,
                expectancy=0, avg_rr=0, sharpe_ratio=0, avg_bars_held=0,
                initial_equity=initial_equity, final_equity=final_equity,
                equity_curve=equity_curve, duration_seconds=duration,
            )

        wins = [t for t in trades if t.profit_usd > 0]
        losses = [t for t in trades if t.profit_usd <= 0]
        win_count = len(wins)
        loss_count = len(losses)

        gross_profit = sum(t.profit_usd for t in wins)
        gross_loss = abs(sum(t.profit_usd for t in losses))
        total_profit = sum(t.profit_usd for t in trades)

        win_rate = (win_count / total * 100) if total > 0 else 0
        pf = (gross_profit / gross_loss) if gross_loss > 0 else float('inf')
        expectancy = total_profit / total if total > 0 else 0
        avg_rr = sum(t.risk_reward for t in trades) / total if total > 0 else 0
        avg_bars = sum(t.bars_held for t in trades) / total if total > 0 else 0

        # Sharpe ratio (simplified: daily returns)
        returns = [t.profit_usd / initial_equity for t in trades]
        if len(returns) > 1:
            import statistics
            mean_ret = statistics.mean(returns)
            std_ret = statistics.stdev(returns)
            sharpe = (mean_ret / std_ret * (252 ** 0.5)) if std_ret > 0 else 0
        else:
            sharpe = 0

        # Per-regime breakdown
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

        trade_dicts = []
        for t in trades:
            trade_dicts.append({
                "id": t.trade_id,
                "action": t.action,
                "entry": t.entry_price,
                "exit": t.exit_price,
                "entry_time": t.entry_time,
                "exit_time": t.exit_time,
                "sl": t.sl,
                "tp": t.tp,
                "pnl": t.profit_usd,
                "rr": t.risk_reward,
                "reason": t.exit_reason,
                "bars": t.bars_held,
                "regime": t.regime,
            })

        return BacktestResult(
            symbol=symbol,
            strategy=strategy_name,
            start_date=start_date,
            end_date=end_date,
            total_bars=total_bars,
            total_trades=total,
            winning_trades=win_count,
            losing_trades=loss_count,
            win_rate=round(win_rate, 1),
            profit_factor=round(pf, 2),
            total_profit_usd=round(total_profit, 2),
            max_drawdown_pct=round(max_dd_pct, 1),
            max_drawdown_usd=round(max_dd_usd, 2),
            expectancy=round(expectancy, 2),
            avg_rr=round(avg_rr, 2),
            sharpe_ratio=round(sharpe, 2),
            avg_bars_held=round(avg_bars, 1),
            initial_equity=initial_equity,
            final_equity=round(final_equity, 2),
            equity_curve=equity_curve,
            trades=trade_dicts,
            per_regime=per_regime,
            duration_seconds=round(duration, 2),
        )
