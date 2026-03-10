"""
Trade History Learner — learns from MT5 deal history.

Runs periodically (or on-demand) to:
1. Pull all closed trades from MT5
2. Analyze win/loss patterns per symbol, session, lot size, hour
3. Save insights to SQLite for strategy factory to query
4. Dynamically adjust strategy parameters based on what actually works

Key Learnings Tracked:
- Best performing hours (UTC) per symbol
- Best lot sizes
- Win rate by direction (BUY vs SELL)
- Average win vs average loss (for R:R tuning)
- Profitable vs unprofitable strategies
"""

import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import Optional

import MetaTrader5 as mt5

from app.core.logging import get_logger

logger = get_logger(__name__)


class TradeInsight:
    """Container for trade insights per symbol."""
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.total_trades = 0
        self.wins = 0
        self.losses = 0
        self.win_rate = 0.0
        self.profit_factor = 0.0
        self.avg_win = 0.0
        self.avg_loss = 0.0
        self.net_pnl = 0.0
        self.best_hours: list[int] = []       # UTC hours with WR > 60%
        self.worst_hours: list[int] = []      # UTC hours with WR < 40%
        self.best_direction: str = "BOTH"     # BUY / SELL / BOTH
        self.optimal_lot_range: tuple = (0.01, 0.1)
        self.avg_rr_actual: float = 1.0


class TradeHistoryLearner:
    """
    Learns from MT5 deal history and produces actionable insights.
    """

    def __init__(self, db=None):
        self.db = db
        self._last_analysis_time: float = 0.0
        self._insights: dict[str, TradeInsight] = {}
        self._analysis_interval = 1800  # 30 minutes

    def should_analyze(self) -> bool:
        """Check if enough time has passed since last analysis."""
        return time.time() - self._last_analysis_time > self._analysis_interval

    def analyze(self, days_back: int = 5) -> dict[str, TradeInsight]:
        """
        Pull trades from MT5 and analyze patterns.
        
        Returns dict of symbol -> TradeInsight
        """
        try:
            if not mt5.terminal_info():
                logger.debug("history_learner_mt5_not_ready")
                return self._insights

            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(days=days_back)

            deals = mt5.history_deals_get(start_time, end_time)
            if not deals:
                logger.info("history_learner_no_deals")
                return self._insights

            # Filter exits only (entry=1)
            exits = [d for d in deals if d.entry == 1]
            if not exits:
                return self._insights

            # Group by symbol
            by_symbol = defaultdict(list)
            for d in exits:
                by_symbol[d.symbol].append(d)

            insights = {}
            for symbol, trades in by_symbol.items():
                insight = self._analyze_symbol(symbol, trades)
                insights[symbol] = insight

            self._insights = insights
            self._last_analysis_time = time.time()

            # Persist to SQLite
            if self.db:
                self._persist_insights(insights)

            # Log summary
            for sym, ins in insights.items():
                logger.info("history_insight", extra={
                    "symbol": sym,
                    "total": ins.total_trades,
                    "wr": round(ins.win_rate, 1),
                    "pf": round(ins.profit_factor, 2),
                    "net": round(ins.net_pnl, 0),
                    "best_hours": ins.best_hours,
                    "worst_hours": ins.worst_hours,
                    "best_dir": ins.best_direction,
                    "avg_rr": round(ins.avg_rr_actual, 2),
                })

            return insights

        except Exception as e:
            logger.error("history_learner_error", extra={"error": str(e)})
            return self._insights

    def _analyze_symbol(self, symbol: str, trades: list) -> TradeInsight:
        """Deep analysis of trades for one symbol."""
        ins = TradeInsight(symbol)
        ins.total_trades = len(trades)

        wins = [d for d in trades if d.profit > 0]
        losses = [d for d in trades if d.profit <= 0]
        ins.wins = len(wins)
        ins.losses = len(losses)
        ins.win_rate = ins.wins / ins.total_trades * 100 if ins.total_trades > 0 else 0

        total_win_pnl = sum(d.profit for d in wins)
        total_loss_pnl = sum(d.profit for d in losses)
        ins.net_pnl = total_win_pnl + total_loss_pnl
        ins.avg_win = total_win_pnl / ins.wins if ins.wins > 0 else 0
        ins.avg_loss = total_loss_pnl / ins.losses if ins.losses > 0 else 0
        ins.profit_factor = abs(total_win_pnl / total_loss_pnl) if total_loss_pnl != 0 else 999
        ins.avg_rr_actual = abs(ins.avg_win / ins.avg_loss) if ins.avg_loss != 0 else 1.0

        # Hour analysis
        hour_stats = defaultdict(lambda: {"w": 0, "l": 0})
        for d in trades:
            h = datetime.fromtimestamp(d.time, tz=timezone.utc).hour
            if d.profit > 0:
                hour_stats[h]["w"] += 1
            else:
                hour_stats[h]["l"] += 1

        for h, s in hour_stats.items():
            total = s["w"] + s["l"]
            if total >= 3:  # Need at least 3 trades to judge
                wr = s["w"] / total * 100
                if wr >= 60:
                    ins.best_hours.append(h)
                elif wr <= 35:
                    ins.worst_hours.append(h)

        # Direction analysis
        buy_trades = [d for d in trades if d.type == 0]  # BUY
        sell_trades = [d for d in trades if d.type == 1]  # SELL
        buy_wr = len([d for d in buy_trades if d.profit > 0]) / len(buy_trades) * 100 if buy_trades else 0
        sell_wr = len([d for d in sell_trades if d.profit > 0]) / len(sell_trades) * 100 if sell_trades else 0

        if buy_wr >= 60 and sell_wr < 50:
            ins.best_direction = "BUY"
        elif sell_wr >= 60 and buy_wr < 50:
            ins.best_direction = "SELL"
        else:
            ins.best_direction = "BOTH"

        # Lot size analysis
        small_wins = [d for d in trades if d.volume <= 0.1 and d.profit > 0]
        small_total = [d for d in trades if d.volume <= 0.1]
        large_wins = [d for d in trades if d.volume > 0.5 and d.profit > 0]
        large_total = [d for d in trades if d.volume > 0.5]

        small_wr = len(small_wins) / len(small_total) * 100 if small_total else 0
        large_wr = len(large_wins) / len(large_total) * 100 if large_total else 0

        if small_wr > large_wr + 10:
            ins.optimal_lot_range = (0.01, 0.1)
        elif large_wr > small_wr + 10:
            ins.optimal_lot_range = (0.1, 0.5)

        return ins

    def _persist_insights(self, insights: dict[str, TradeInsight]) -> None:
        """Save insights to SQLite for strategy factory to query."""
        if not self.db:
            return
        try:
            # Safely get connection — handle both SQLiteStore._conn and other DB wrappers
            conn = getattr(self.db, '_conn', None)
            if conn is None:
                logger.warning("trade_insights_no_conn", extra={"db_type": type(self.db).__name__})
                return
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_insights (
                    symbol TEXT PRIMARY KEY,
                    total_trades INTEGER,
                    win_rate REAL,
                    profit_factor REAL,
                    avg_win REAL,
                    avg_loss REAL,
                    net_pnl REAL,
                    avg_rr REAL,
                    best_hours TEXT,
                    worst_hours TEXT,
                    best_direction TEXT,
                    updated_at TEXT
                )
            """)
            for sym, ins in insights.items():
                conn.execute("""
                    INSERT OR REPLACE INTO trade_insights
                    (symbol, total_trades, win_rate, profit_factor, avg_win, avg_loss,
                     net_pnl, avg_rr, best_hours, worst_hours, best_direction, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    sym, ins.total_trades, round(ins.win_rate, 2),
                    round(ins.profit_factor, 2), round(ins.avg_win, 2),
                    round(ins.avg_loss, 2), round(ins.net_pnl, 2),
                    round(ins.avg_rr_actual, 2),
                    ",".join(str(h) for h in ins.best_hours),
                    ",".join(str(h) for h in ins.worst_hours),
                    ins.best_direction,
                    datetime.now(timezone.utc).isoformat(),
                ))
            conn.commit()
            logger.info("trade_insights_persisted", extra={"symbols": list(insights.keys())})
        except Exception as e:
            logger.error("trade_insights_persist_error", extra={"error": str(e)})

    def get_insight(self, symbol: str) -> Optional[TradeInsight]:
        """Get cached insight for a symbol."""
        return self._insights.get(symbol)

    def should_trade_now(self, symbol: str) -> tuple[bool, str]:
        """
        Check if current hour is good for trading this symbol.
        Returns (should_trade, reason)
        """
        ins = self._insights.get(symbol)
        if not ins or ins.total_trades < 10:
            return True, "insufficient_data"

        current_hour = datetime.now(timezone.utc).hour

        if current_hour in ins.worst_hours:
            return False, f"worst_hour_{current_hour}_wr_below_35pct"

        if ins.win_rate < 40:
            return False, f"overall_wr_{ins.win_rate:.0f}pct_too_low"

        return True, "ok"

    def get_direction_bias(self, symbol: str) -> Optional[str]:
        """
        Get preferred direction for a symbol based on history.
        Returns "BUY", "SELL", or None (both ok)
        """
        ins = self._insights.get(symbol)
        if not ins or ins.total_trades < 10:
            return None
        if ins.best_direction == "BOTH":
            return None
        return ins.best_direction
