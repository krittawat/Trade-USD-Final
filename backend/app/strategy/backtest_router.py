"""
Backtest-Powered Strategy Router — เลือก strategy จากข้อมูล backtest จริง.

แทนที่จะเลือกแบบ "ใครมาก่อนได้ก่อน" ระบบนี้ใช้ผล backtest
เป็น lookup table: (symbol, regime) → best proven strategy

Priority order:
    1. AI Brain recommendation
    2. Backtest Router pick (data-driven)
    3. Regime-matched strategies (factory)
    4. Default (sniper — all-regime champion)

Usage:
    router = BacktestRouter(db_path="data/sqlite/trading.db")
    best = router.get_best_strategy("XAUUSDc", "TRENDING_UP")
    # → "gold_session_breakout"
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class BacktestRouter:
    """
    Data-driven strategy selector — ใช้ผล backtest เลือก strategy ที่ดีที่สุด.

    Routing table structure:
        { "XAUUSDc": { "TRENDING_UP": "gold_session_breakout", ... } }
    """

    def __init__(self, db_path: str = "data/sqlite/trading.db"):
        self._db_path = db_path
        # { symbol: { regime: strategy_name } }
        self._routing_table: dict[str, dict[str, str]] = {}
        # { symbol: { regime: { pf, wr, trades, pnl, score } } }
        self._routing_metrics: dict[str, dict[str, dict]] = {}
        # Default fallback strategy (all-regime champion from backtest)
        self._default_strategy: str = "sniper"
        # Load from DB on init
        self._load_from_db()

    # ────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────

    def get_best_strategy(
        self,
        symbol: str,
        regime: str,
    ) -> Optional[str]:
        """
        Return best strategy name for symbol + regime.

        Args:
            symbol: e.g. "XAUUSDc"
            regime: e.g. "TRENDING_UP", "RANGING"

        Returns:
            Strategy name or None if no data
        """
        sym_table = self._routing_table.get(symbol)
        if not sym_table:
            logger.debug("router_no_data_for_symbol", extra={
                "symbol": symbol, "regime": regime,
                "fallback": self._default_strategy,
            })
            return self._default_strategy

        strategy = sym_table.get(regime)
        if strategy:
            metrics = self._routing_metrics.get(symbol, {}).get(regime, {})
            logger.info("router_pick", extra={
                "symbol": symbol, "regime": regime,
                "strategy": strategy,
                "pf": metrics.get("profit_factor", 0),
                "wr": metrics.get("win_rate", 0),
                "score": metrics.get("score", 0),
            })
            return strategy

        # Regime not in table → use default
        logger.debug("router_regime_not_found", extra={
            "symbol": symbol, "regime": regime,
            "available": list(sym_table.keys()),
            "fallback": self._default_strategy,
        })
        return self._default_strategy

    def get_routing_table(self, symbol: Optional[str] = None) -> dict:
        """Return full routing table (for dashboard/API)."""
        if symbol:
            return {
                "routes": self._routing_table.get(symbol, {}),
                "metrics": self._routing_metrics.get(symbol, {}),
                "default": self._default_strategy,
            }
        return {
            "routes": self._routing_table,
            "metrics": self._routing_metrics,
            "default": self._default_strategy,
        }

    # ────────────────────────────────────────────────────────────────
    # Seeding — populate from backtest results
    # ────────────────────────────────────────────────────────────────

    def seed_from_backtest(
        self,
        symbol: str,
        regime_results: list[dict],
    ) -> int:
        """
        Populate routing table from backtest results.

        Args:
            symbol: e.g. "XAUUSDc"
            regime_results: list of dicts with:
                {
                    "strategy": "sniper",
                    "regime": "TRENDING_UP",
                    "profit_factor": 2.33,
                    "win_rate": 66.7,
                    "total_trades": 6,
                    "total_pnl": 152.52,
                    "score": 32.0,
                }

        Returns:
            Number of routes saved
        """
        if symbol not in self._routing_table:
            self._routing_table[symbol] = {}
            self._routing_metrics[symbol] = {}

        # Group by regime → find best strategy per regime
        from collections import defaultdict
        regime_groups: dict[str, list[dict]] = defaultdict(list)
        for r in regime_results:
            regime_groups[r["regime"]].append(r)

        routes_saved = 0
        now = datetime.now(timezone.utc).isoformat()

        try:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            cur = conn.cursor()

            for regime, entries in regime_groups.items():
                # Filter: minimum 2 trades in this regime
                viable = [e for e in entries if e.get("total_trades", 0) >= 2]
                if not viable:
                    continue

                # Select best by: score (primary), then profit_factor (secondary)
                best = max(viable, key=lambda x: (
                    x.get("score", 0),
                    x.get("profit_factor", 0),
                ))

                strategy = best["strategy"]
                self._routing_table[symbol][regime] = strategy
                self._routing_metrics[symbol][regime] = {
                    "profit_factor": best.get("profit_factor", 0),
                    "win_rate": best.get("win_rate", 0),
                    "total_trades": best.get("total_trades", 0),
                    "total_pnl": best.get("total_pnl", 0),
                    "score": best.get("score", 0),
                }

                # Upsert into SQLite
                cur.execute("""
                    INSERT INTO backtest_routing
                        (symbol, regime, strategy, profit_factor, win_rate,
                         total_trades, total_pnl, score, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, regime)
                    DO UPDATE SET
                        strategy = excluded.strategy,
                        profit_factor = excluded.profit_factor,
                        win_rate = excluded.win_rate,
                        total_trades = excluded.total_trades,
                        total_pnl = excluded.total_pnl,
                        score = excluded.score,
                        updated_at = excluded.updated_at
                """, (
                    symbol, regime, strategy,
                    best.get("profit_factor", 0),
                    best.get("win_rate", 0),
                    best.get("total_trades", 0),
                    best.get("total_pnl", 0),
                    best.get("score", 0),
                    now,
                ))
                routes_saved += 1

            conn.commit()
            conn.close()

            logger.info("router_seeded", extra={
                "symbol": symbol,
                "routes": routes_saved,
                "table": self._routing_table.get(symbol, {}),
            })

        except Exception as e:
            logger.error("router_seed_error", extra={
                "symbol": symbol, "error": str(e),
            })

        return routes_saved

    # ────────────────────────────────────────────────────────────────
    # Load from SQLite
    # ────────────────────────────────────────────────────────────────

    def _load_from_db(self) -> None:
        """Load routing table from SQLite backtest_routing table."""
        try:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Check table exists
            cur.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='backtest_routing'
            """)
            if not cur.fetchone():
                conn.close()
                logger.info("router_no_table", extra={
                    "db": self._db_path,
                    "detail": "backtest_routing table not found — run backtest to seed",
                })
                return

            rows = cur.execute("""
                SELECT symbol, regime, strategy,
                       profit_factor, win_rate, total_trades,
                       total_pnl, score, updated_at
                FROM backtest_routing
                ORDER BY symbol, score DESC
            """).fetchall()

            for row in rows:
                sym = row["symbol"]
                regime = row["regime"]
                if sym not in self._routing_table:
                    self._routing_table[sym] = {}
                    self._routing_metrics[sym] = {}

                self._routing_table[sym][regime] = row["strategy"]
                self._routing_metrics[sym][regime] = {
                    "profit_factor": row["profit_factor"],
                    "win_rate": row["win_rate"],
                    "total_trades": row["total_trades"],
                    "total_pnl": row["total_pnl"],
                    "score": row["score"],
                    "updated_at": row["updated_at"],
                }

            conn.close()

            total_routes = sum(len(v) for v in self._routing_table.values())
            if total_routes > 0:
                logger.info("router_loaded", extra={
                    "symbols": list(self._routing_table.keys()),
                    "total_routes": total_routes,
                    "table": {s: dict(r) for s, r in self._routing_table.items()},
                })
            else:
                logger.info("router_empty", extra={
                    "detail": "No routing data — run backtest to populate",
                })

        except Exception as e:
            logger.warning("router_load_error", extra={
                "db": self._db_path, "error": str(e),
            })
