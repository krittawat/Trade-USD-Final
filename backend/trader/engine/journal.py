from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.trader.config.paths import OPUS_DB_PATH

from .models import EngineResult, MarketSnapshot


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TradingJournal:
    def __init__(self, db_path: str | Path = OPUS_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10.0)
        self._configure()
        self._create_schema()

    def _configure(self) -> None:
        cursor = self.conn.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout = 10000")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA synchronous = NORMAL")
        finally:
            cursor.close()

    def _create_schema(self) -> None:
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_engine_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                mode TEXT NOT NULL,
                symbol TEXT NOT NULL,
                standard_symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                model TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT DEFAULT '',
                confidence REAL DEFAULT 0.0,
                expectancy_r REAL DEFAULT 0.0,
                rr REAL DEFAULT 0.0,
                risk_pct REAL DEFAULT 0.0,
                risk_usd REAL DEFAULT 0.0,
                lot_size REAL DEFAULT 0.0,
                entry_price REAL DEFAULT 0.0,
                stop_loss REAL DEFAULT 0.0,
                take_profit REAL DEFAULT 0.0,
                spread_points REAL DEFAULT 0.0,
                slippage_points REAL DEFAULT 0.0,
                session TEXT DEFAULT '',
                blocked_reasons TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}'
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_engine_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                mode TEXT NOT NULL,
                symbol TEXT NOT NULL,
                model TEXT NOT NULL,
                equity REAL DEFAULT 0.0,
                balance REAL DEFAULT 0.0,
                daily_pnl REAL DEFAULT 0.0,
                floating_pnl REAL DEFAULT 0.0,
                combined_pnl REAL DEFAULT 0.0,
                drawdown_pct REAL DEFAULT 0.0,
                peak_equity REAL DEFAULT 0.0,
                open_risk_usd REAL DEFAULT 0.0,
                win_rate REAL DEFAULT 0.0,
                profit_factor REAL DEFAULT 0.0,
                expectancy_r REAL DEFAULT 0.0,
                metadata TEXT DEFAULT '{}'
            )
            """
        )
        self.conn.commit()

    def record_result(self, mode: str, market: MarketSnapshot, result: EngineResult) -> None:
        signal = result.signal
        if signal is None:
            return

        plan = result.plan
        performance = result.performance
        execution = result.execution

        metadata = {
            "rationale": list(signal.rationale),
            "execution": execution.raw if execution else {},
        }
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO trade_engine_journal (
                timestamp, mode, symbol, standard_symbol, timeframe, model, side, status,
                reason, confidence, expectancy_r, rr, risk_pct, risk_usd, lot_size,
                entry_price, stop_loss, take_profit, spread_points, slippage_points,
                session, blocked_reasons, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _utc_iso(),
                str(mode).lower(),
                signal.symbol,
                signal.standard_symbol,
                signal.timeframe,
                signal.model,
                signal.side,
                result.status.upper(),
                result.reason,
                float(signal.confidence or 0.0),
                float(performance.combined_expectancy_r if performance else 0.0),
                float(plan.rr if plan else 0.0),
                float(plan.risk_pct if plan else 0.0),
                float(plan.risk_usd if plan else 0.0),
                float(plan.lot_size if plan else 0.0),
                float(plan.entry_price if plan else signal.entry_price),
                float(plan.stop_loss if plan else 0.0),
                float(plan.take_profit if plan else 0.0),
                float(plan.spread_points if plan else market.spread_points),
                float(
                    execution.slippage_points
                    if execution is not None
                    else (plan.estimated_slippage_points if plan else 0.0)
                ),
                market.session,
                json.dumps(result.blocked_reasons, separators=(",", ":"), ensure_ascii=True),
                json.dumps(metadata, separators=(",", ":"), ensure_ascii=True, default=str),
            ),
        )
        self.conn.commit()

    def record_metrics(self, mode: str, market: MarketSnapshot, result: EngineResult) -> None:
        if result.portfolio is None or result.performance is None or result.signal is None:
            return

        portfolio = result.portfolio
        performance = result.performance
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO trade_engine_metrics (
                timestamp, mode, symbol, model, equity, balance, daily_pnl, floating_pnl,
                combined_pnl, drawdown_pct, peak_equity, open_risk_usd, win_rate,
                profit_factor, expectancy_r, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _utc_iso(),
                str(mode).lower(),
                result.signal.standard_symbol,
                result.signal.model,
                float(portfolio.equity),
                float(portfolio.balance),
                float(portfolio.daily_pnl),
                float(portfolio.floating_pnl),
                float(portfolio.combined_pnl),
                float(portfolio.drawdown_pct),
                float(portfolio.peak_equity),
                float(portfolio.open_risk_usd),
                float(performance.win_rate),
                float(performance.profit_factor),
                float(performance.combined_expectancy_r),
                json.dumps(
                    {
                        "session": market.session,
                        "status": result.status,
                    },
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
            ),
        )
        self.conn.commit()
