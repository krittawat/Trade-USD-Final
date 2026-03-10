"""
Outcome Analyzer — Trade Outcome Learning Loop.

Records full trade context → analyzes outcomes → feeds back to strategy selection.

This is what makes the bot learn from EVERY trade:
    - Records: strategy, regime, session, confidence, mtf_score, entry_quality, patterns
    - On close: records PnL, hold duration, exit type, R-multiple
    - Builds statistical model: which (strategy, regime, session) combos have REAL edge
    - Uses edge_score = (win_rate - 0.5) × sqrt(sample_size) for statistical significance

Rules:
    - Advisory only — never overrides Risk Engine
    - Min 20 samples before edge_score is reported
    - Data stored in SQLite (low RAM, persistent)
    - Auto-prune data older than 90 days

Usage:
    analyzer = OutcomeAnalyzer(db=sqlite_store)
    analyzer.record_trade_open(ticket=123, decision=..., mtf_score=82, ...)
    analyzer.record_trade_close(ticket=123, pnl=15.0, exit_type="TP")
    edges = analyzer.get_strategy_edges("XAUUSDc", "STRONG_TREND", "NY")
"""

import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# Min sample size for edge score
MIN_SAMPLES = 20
# Data retention (days)
RETENTION_DAYS = 90

# ====================================================================
# Data Containers
# ====================================================================

@dataclass
class TradeContext:
    """Full context recorded at trade open."""
    ticket: int = 0
    symbol: str = ""
    strategy_name: str = ""
    regime: str = ""
    session: str = ""
    confidence: float = 0.0
    mtf_score: float = 0.0
    entry_quality_grade: str = ""
    entry_quality_score: float = 0.0
    ml_win_prob: float = 0.5
    dl_win_prob: float = 0.5
    sentiment_score: float = 0.0
    patterns_aligned: list = field(default_factory=list)
    patterns_conflicting: list = field(default_factory=list)
    direction: str = ""  # BUY / SELL
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    opened_at: str = ""


@dataclass
class TradeOutcome:
    """Outcome recorded at trade close."""
    ticket: int = 0
    pnl: float = 0.0
    pnl_r: float = 0.0       # P/L in R-multiples
    hold_bars: int = 0
    hold_seconds: float = 0.0
    exit_type: str = ""       # SL / TP / BE / TRAIL / MANUAL / TIME


@dataclass
class StrategyEdge:
    """Statistical edge for a (strategy, regime, session) combination."""
    strategy_name: str = ""
    symbol: str = ""
    regime: str = ""
    session: str = ""
    win_rate: float = 0.0
    avg_r: float = 0.0
    total_trades: int = 0
    edge_score: float = 0.0       # (win_rate - 0.5) × sqrt(N)
    profit_factor: float = 0.0
    avg_hold_bars: float = 0.0
    best_mtf_bucket: str = ""     # "high"/"medium"/"low"
    best_entry_grade: str = ""    # "A"/"B"/"C"


# ====================================================================
# Tables SQL
# ====================================================================

CREATE_TRADE_CONTEXT_SQL = """
CREATE TABLE IF NOT EXISTS trade_context (
    ticket INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    regime TEXT DEFAULT '',
    session TEXT DEFAULT '',
    confidence REAL DEFAULT 0,
    mtf_score REAL DEFAULT 0,
    entry_quality_grade TEXT DEFAULT '',
    entry_quality_score REAL DEFAULT 0,
    ml_win_prob REAL DEFAULT 0.5,
    dl_win_prob REAL DEFAULT 0.5,
    sentiment_score REAL DEFAULT 0,
    patterns_aligned TEXT DEFAULT '[]',
    patterns_conflicting TEXT DEFAULT '[]',
    direction TEXT DEFAULT '',
    entry_price REAL DEFAULT 0,
    stop_loss REAL DEFAULT 0,
    take_profit REAL DEFAULT 0,
    opened_at TEXT DEFAULT '',
    created_ts REAL DEFAULT 0
)
"""

CREATE_TRADE_OUTCOME_SQL = """
CREATE TABLE IF NOT EXISTS trade_outcome (
    ticket INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    strategy_name TEXT NOT NULL,
    regime TEXT DEFAULT '',
    session TEXT DEFAULT '',
    pnl REAL DEFAULT 0,
    pnl_r REAL DEFAULT 0,
    hold_bars INTEGER DEFAULT 0,
    hold_seconds REAL DEFAULT 0,
    exit_type TEXT DEFAULT '',
    mtf_score REAL DEFAULT 0,
    entry_quality_grade TEXT DEFAULT '',
    confidence REAL DEFAULT 0,
    direction TEXT DEFAULT '',
    closed_at TEXT DEFAULT '',
    created_ts REAL DEFAULT 0
)
"""


# ====================================================================
# Outcome Analyzer
# ====================================================================

class OutcomeAnalyzer:
    """
    Trade Outcome Learning Loop.

    Records every trade's full context, then on close analyzes the outcome
    and builds a statistical model of which strategies have real edge
    in which conditions.

    This makes the bot learn from mistakes — something humans struggle with.
    """

    def __init__(self, db=None):
        """
        Args:
            db: SQLiteStore instance for persistence
        """
        self.db = db
        self._initialized = False
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        """Create tables if they don't exist."""
        if not self.db or self._initialized:
            return
        try:
            conn = self.db._conn
            conn.execute(CREATE_TRADE_CONTEXT_SQL)
            conn.execute(CREATE_TRADE_OUTCOME_SQL)
            conn.commit()
            self._check_schema_updates(conn)
            self._initialized = True
            logger.info("outcome_analyzer_tables_ready")
        except Exception as e:
            logger.error("outcome_analyzer_init_error", extra={"error": str(e)})

    def _check_schema_updates(self, conn) -> None:
        """Migrate schema dynamically (add dl_win_prob if missing)."""
        try:
            # Check if dl_win_prob exists
            cursor = conn.execute("PRAGMA table_info(trade_context)")
            columns = [info[1] for info in cursor.fetchall()]
            if "dl_win_prob" not in columns:
                logger.info("migrating_schema_add_dl_win_prob")
                conn.execute("ALTER TABLE trade_context ADD COLUMN dl_win_prob REAL DEFAULT 0.5")
                conn.commit()
        except Exception as e:
            logger.warning("schema_migration_error", extra={"error": str(e)})

    # ────────────────────────────────────────────────────────────────
    # Record Trade Open
    # ────────────────────────────────────────────────────────────────

    def record_trade_open(self, ctx: TradeContext) -> None:
        """Record full trade context at open."""
        if not self.db:
            return
        self._ensure_tables()

        try:
            conn = self.db._conn
            conn.execute(
                """INSERT OR REPLACE INTO trade_context
                   (ticket, symbol, strategy_name, regime, session,
                    confidence, mtf_score, entry_quality_grade, entry_quality_score,
                    ml_win_prob, dl_win_prob, sentiment_score,
                    patterns_aligned, patterns_conflicting,
                    direction, entry_price, stop_loss, take_profit,
                    opened_at, created_ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ctx.ticket, ctx.symbol, ctx.strategy_name,
                    ctx.regime, ctx.session,
                    ctx.confidence, ctx.mtf_score,
                    ctx.entry_quality_grade, ctx.entry_quality_score,
                    ctx.ml_win_prob, ctx.dl_win_prob, ctx.sentiment_score,
                    json.dumps(ctx.patterns_aligned),
                    json.dumps(ctx.patterns_conflicting),
                    ctx.direction, ctx.entry_price, ctx.stop_loss, ctx.take_profit,
                    ctx.opened_at or datetime.now(timezone.utc).isoformat(),
                    time.time(),
                ),
            )
            conn.commit()
            logger.debug("trade_context_recorded", extra={
                "ticket": ctx.ticket, "symbol": ctx.symbol,
                "strategy": ctx.strategy_name,
            })
        except Exception as e:
            logger.error("record_trade_open_error", extra={
                "ticket": ctx.ticket, "error": str(e),
            })

    # ────────────────────────────────────────────────────────────────
    # Record Trade Close
    # ────────────────────────────────────────────────────────────────

    def record_trade_close(
        self,
        ticket: int,
        pnl: float,
        hold_bars: int = 0,
        hold_seconds: float = 0.0,
        exit_type: str = "",
    ) -> None:
        """
        Record trade outcome and compute R-multiple.

        Args:
            ticket: Trade ticket number
            pnl: Profit/loss in account currency
            hold_bars: Number of M5 bars held
            hold_seconds: Hold duration in seconds
            exit_type: SL/TP/BE/TRAIL/MANUAL/TIME
        """
        if not self.db:
            return
        self._ensure_tables()

        try:
            conn = self.db._conn

            # Fetch context
            row = conn.execute(
                "SELECT * FROM trade_context WHERE ticket = ?",
                (ticket,),
            ).fetchone()

            if not row:
                logger.debug("trade_context_not_found", extra={"ticket": ticket})
                return

            # Compute R-multiple
            entry = row["entry_price"] if isinstance(row, dict) else row[14]  # entry_price
            sl = row["stop_loss"] if isinstance(row, dict) else row[15]  # stop_loss
            risk = abs(entry - sl) if sl > 0 and entry > 0 else 1.0
            pnl_r = pnl / risk if risk > 0 else 0.0

            symbol = row["symbol"] if isinstance(row, dict) else row[1]
            strategy = row["strategy_name"] if isinstance(row, dict) else row[2]
            regime = row["regime"] if isinstance(row, dict) else row[3]
            session = row["session"] if isinstance(row, dict) else row[4]
            mtf_score = row["mtf_score"] if isinstance(row, dict) else row[6]
            entry_grade = row["entry_quality_grade"] if isinstance(row, dict) else row[7]
            confidence = row["confidence"] if isinstance(row, dict) else row[5]
            direction = row["direction"] if isinstance(row, dict) else row[13]

            conn.execute(
                """INSERT OR REPLACE INTO trade_outcome
                   (ticket, symbol, strategy_name, regime, session,
                    pnl, pnl_r, hold_bars, hold_seconds, exit_type,
                    mtf_score, entry_quality_grade, confidence, direction,
                    closed_at, created_ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ticket, symbol, strategy, regime, session,
                    pnl, round(pnl_r, 3), hold_bars, hold_seconds, exit_type,
                    mtf_score, entry_grade, confidence, direction,
                    datetime.now(timezone.utc).isoformat(),
                    time.time(),
                ),
            )
            conn.commit()

            logger.info("trade_outcome_recorded", extra={
                "ticket": ticket, "symbol": symbol,
                "strategy": strategy, "pnl": round(pnl, 2),
                "pnl_r": round(pnl_r, 2), "exit_type": exit_type,
            })

        except Exception as e:
            logger.error("record_trade_close_error", extra={
                "ticket": ticket, "error": str(e),
            })

    # ────────────────────────────────────────────────────────────────
    # Get Strategy Edges
    # ────────────────────────────────────────────────────────────────

    def get_strategy_edges(
        self,
        symbol: str,
        regime: str = "",
        session: str = "",
    ) -> list[StrategyEdge]:
        """
        Get statistically significant strategy edges for given conditions.

        Returns list of StrategyEdge sorted by edge_score DESC.
        Only strategies with >= MIN_SAMPLES trades are included.
        """
        if not self.db:
            return []
        self._ensure_tables()

        try:
            conn = self.db._conn

            # Build query dynamically
            where_clauses = ["symbol = ?"]
            params = [symbol]

            if regime:
                where_clauses.append("regime = ?")
                params.append(regime)
            if session:
                where_clauses.append("session = ?")
                params.append(session)

            where_sql = " AND ".join(where_clauses)

            rows = conn.execute(f"""
                SELECT strategy_name, regime, session,
                       COUNT(*) as total,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                       AVG(pnl_r) as avg_r,
                       SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) as gross_profit,
                       SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END) as gross_loss,
                       AVG(hold_bars) as avg_hold
                FROM trade_outcome
                WHERE {where_sql}
                GROUP BY strategy_name, regime, session
                HAVING total >= ?
                ORDER BY total DESC
            """, (*params, MIN_SAMPLES)).fetchall()

            edges = []
            for row in rows:
                total = row[3] if not isinstance(row, dict) else row["total"]
                wins = row[4] if not isinstance(row, dict) else row["wins"]
                avg_r = row[5] if not isinstance(row, dict) else row["avg_r"]
                gross_profit = row[6] if not isinstance(row, dict) else row["gross_profit"]
                gross_loss = row[7] if not isinstance(row, dict) else row["gross_loss"]
                avg_hold = row[8] if not isinstance(row, dict) else row["avg_hold"]

                win_rate = wins / total if total > 0 else 0.0
                pf = gross_profit / gross_loss if gross_loss > 0 else (
                    99.0 if gross_profit > 0 else 0.0
                )
                edge_score = (win_rate - 0.5) * math.sqrt(total)

                strat_name = row[0] if not isinstance(row, dict) else row["strategy_name"]
                r = row[1] if not isinstance(row, dict) else row["regime"]
                s = row[2] if not isinstance(row, dict) else row["session"]

                edges.append(StrategyEdge(
                    strategy_name=strat_name,
                    symbol=symbol,
                    regime=r,
                    session=s,
                    win_rate=round(win_rate, 4),
                    avg_r=round(avg_r or 0, 3),
                    total_trades=total,
                    edge_score=round(edge_score, 3),
                    profit_factor=round(pf, 2),
                    avg_hold_bars=round(avg_hold or 0, 1),
                ))

            # Sort by edge_score descending
            edges.sort(key=lambda e: e.edge_score, reverse=True)
            return edges

        except Exception as e:
            logger.error("get_strategy_edges_error", extra={
                "symbol": symbol, "error": str(e),
            })
            return []

    # ────────────────────────────────────────────────────────────────
    # Get Best Strategy (shorthand)
    # ────────────────────────────────────────────────────────────────

    def get_best_strategy(
        self,
        symbol: str,
        regime: str = "",
        session: str = "",
    ) -> Optional[str]:
        """
        Get the strategy with the highest edge score.

        Returns strategy_name or None if no edges found.
        """
        edges = self.get_strategy_edges(symbol, regime, session)
        if edges and edges[0].edge_score > 0:
            return edges[0].strategy_name
        return None

    # ────────────────────────────────────────────────────────────────
    # Get Edge Confidence Boost
    # ────────────────────────────────────────────────────────────────

    def get_edge_boost(
        self,
        strategy_name: str,
        symbol: str,
        regime: str = "",
        session: str = "",
    ) -> float:
        """
        Get confidence boost for a specific strategy based on historical edge.

        Returns:
            -0.10 to +0.10 based on edge strength
        """
        edges = self.get_strategy_edges(symbol, regime, session)
        for edge in edges:
            if edge.strategy_name == strategy_name:
                if edge.edge_score > 2.0:
                    return 0.10   # Strong statistical edge
                elif edge.edge_score > 1.0:
                    return 0.05   # Moderate edge
                elif edge.edge_score > 0:
                    return 0.02   # Weak edge
                elif edge.edge_score < -1.0:
                    return -0.10  # Negative edge!
                elif edge.edge_score < 0:
                    return -0.05  # Slight negative edge
                return 0.0
        return 0.0  # No data

    # ────────────────────────────────────────────────────────────────
    # Cleanup
    # ────────────────────────────────────────────────────────────────

    def prune_old_data(self, keep_days: int = RETENTION_DAYS) -> int:
        """Delete outcome and context data older than keep_days."""
        if not self.db:
            return 0
        try:
            conn = self.db._conn
            cutoff = time.time() - (keep_days * 86400)
            c1 = conn.execute(
                "DELETE FROM trade_outcome WHERE created_ts < ?", (cutoff,)
            ).rowcount
            c2 = conn.execute(
                "DELETE FROM trade_context WHERE created_ts < ?", (cutoff,)
            ).rowcount
            conn.commit()
            total = (c1 or 0) + (c2 or 0)
            if total > 0:
                logger.info("outcome_data_pruned", extra={
                    "deleted_outcomes": c1, "deleted_contexts": c2,
                })
            return total
        except Exception as e:
            logger.error("prune_old_data_error", extra={"error": str(e)})
            return 0

    # ────────────────────────────────────────────────────────────────
    # Status
    # ────────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Get analyzer status for dashboard."""
        if not self.db:
            return {"initialized": False, "total_contexts": 0, "total_outcomes": 0}
        try:
            conn = self.db._conn
            ctx_count = conn.execute("SELECT COUNT(*) FROM trade_context").fetchone()[0]
            out_count = conn.execute("SELECT COUNT(*) FROM trade_outcome").fetchone()[0]
            return {
                "initialized": self._initialized,
                "total_contexts": ctx_count,
                "total_outcomes": out_count,
            }
        except Exception:
            return {"initialized": self._initialized, "total_contexts": 0, "total_outcomes": 0}
