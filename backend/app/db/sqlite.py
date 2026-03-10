"""
SQLite Store — ฐานข้อมูลฝังตัวสำหรับ trade journal + decision traces + runtime state.

เหตุผลที่ใช้ SQLite:
    - ใช้ RAM น้อยมาก (เหมาะกับเครื่อง 8GB)
    - ไม่ต้องติดตั้ง service เพิ่ม
    - Embedded — ทำงานในตัว
"""

import json
import sqlite3
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Optional

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ─── Trace buffer config ───
_TRACE_FLUSH_SIZE = 10  # flush ทุก 10 traces (ลด disk I/O)


class SQLiteStore:
    """
    SQLite wrapper — trade journal, decision traces, runtime state.
    ทำ migration อัตโนมัติเมื่อเริ่มระบบ.

    Performance:
        - Decision traces ถูก buffer → batch INSERT + single commit
        - flush ทุก 10 traces หรือเมื่อเรียก flush_traces()
        - prune_old_traces() ลบข้อมูลเกิน 7 วัน
    """

    def __init__(self, settings: Settings) -> None:
        self.db_path = Path(settings.sqlite_db_path)
        self._conn: sqlite3.Connection | None = None
        self._trace_buffer: list[tuple] = []  # buffer สำหรับ batch INSERT

    def connect(self) -> None:
        """เปิดการเชื่อมต่อ SQLite — สร้างไฟล์ถ้ายังไม่มี."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # balance safety + speed
        logger.info("sqlite_connected", extra={"path": str(self.db_path)})
        self._run_migrations()

    def _run_migrations(self) -> None:
        """สร้างตารางที่จำเป็น (idempotent) และ migrate schema ใหม่."""
        assert self._conn is not None, "ต้อง connect() ก่อนใช้งาน"

        # Trade journal
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                lot_size REAL NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                stop_loss REAL NOT NULL,
                take_profit REAL,
                risk_usd REAL NOT NULL,
                risk_pct REAL DEFAULT 0,
                profit_usd REAL,
                strategy_name TEXT,
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                regime TEXT,
                session TEXT,
                tags TEXT,
                notes TEXT,
                ticket INTEGER,
                mode TEXT DEFAULT 'DRY_RUN'
            )
        """)

        # Decision traces
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS decision_traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                stage TEXT NOT NULL,
                result TEXT NOT NULL,
                action TEXT,
                confidence REAL DEFAULT 0,
                strategy_name TEXT,
                reason TEXT,
                details TEXT,
                regime TEXT,
                session TEXT,
                cycle INTEGER DEFAULT 0,
                mode TEXT DEFAULT 'DRY_RUN'
            )
        """)

        # Runtime state
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS runtime_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Shadow trades
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                regime TEXT,
                session TEXT,
                entry_price REAL DEFAULT 0,
                stop_loss REAL DEFAULT 0,
                take_profit REAL DEFAULT 0,
                lot_size REAL DEFAULT 0,
                risk_usd REAL DEFAULT 0,
                risk_pct REAL DEFAULT 0,
                cycle INTEGER DEFAULT 0,
                reason TEXT
            )
        """)

        # Backtest routing
        self._conn.execute("""
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

        # Strategy params
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_params (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                params_json TEXT NOT NULL,
                win_rate REAL DEFAULT 0,
                profit_factor REAL DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                max_drawdown_pct REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                backtest_days INTEGER DEFAULT 0,
                label TEXT DEFAULT '',
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(symbol, strategy_name)
            )
        """)

        # Strategy registry — dynamic strategy-regime mapping (replaces hardcoded TEMPLATE_REGISTRY)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_registry (
                strategy_name TEXT PRIMARY KEY,
                class_name TEXT NOT NULL,
                module_path TEXT NOT NULL,
                timeframe TEXT DEFAULT 'M5',
                asset_class TEXT DEFAULT '*',
                suitable_regimes TEXT DEFAULT '[]',
                priority INTEGER DEFAULT 50,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        # Backtest results
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                label TEXT DEFAULT '',
                params_json TEXT NOT NULL,
                win_rate REAL DEFAULT 0,
                profit_factor REAL DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                max_drawdown_pct REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,
                backtest_days INTEGER DEFAULT 0,
                expectancy REAL DEFAULT 0,
                per_regime_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            )
        """)

        # ─── Migration: Add new columns for Currency/Lot Mode ───
        self._safe_add_column("trade_journal", "account_currency", "TEXT DEFAULT 'USD'")
        self._safe_add_column("trade_journal", "lot_mode", "TEXT DEFAULT 'STANDARD'")
        self._safe_add_column("trade_journal", "symbol_suffix", "TEXT DEFAULT ''")
        self._safe_add_column("trade_journal", "balance_usd", "REAL DEFAULT 0")
        self._safe_add_column("trade_journal", "balance_account", "REAL DEFAULT 0")
        
        self._safe_add_column("decision_traces", "mode_details", "TEXT") # json details

        # ─── Shadow evaluation columns (migration) ───
        self._safe_add_column("shadow_trades", "outcome", "TEXT DEFAULT 'PENDING'")
        self._safe_add_column("shadow_trades", "outcome_pnl", "REAL DEFAULT 0")
        self._safe_add_column("shadow_trades", "evaluated_at", "TEXT")

        # Shadow scoreboard — aggregated strategy performance from shadow trades
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS shadow_scoreboard (
                strategy_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                regime TEXT NOT NULL DEFAULT 'ALL',
                total_signals INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                pending INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_confidence REAL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (strategy_name, symbol, regime)
            )
        """)

        # Create indexes
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_dt_symbol ON decision_traces(symbol, timestamp)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_tj_symbol ON trade_journal(symbol, entry_time)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_st_symbol ON shadow_trades(symbol, timestamp)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_st_strategy ON shadow_trades(strategy_name, timestamp)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_st_outcome ON shadow_trades(outcome, timestamp)")
        
        # New indexes for strategy params/backtest
        try:
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_sp_symbol ON strategy_params(symbol, strategy_name)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_br_symbol ON backtest_results(symbol, strategy_name)")
        except Exception:
            pass

        # ─── Ticks table (แทน QuestDB) ───
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS ticks (
                ts TEXT NOT NULL,
                symbol TEXT NOT NULL,
                bid REAL,
                ask REAL,
                last REAL,
                volume INTEGER DEFAULT 0,
                flags INTEGER DEFAULT 0
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ticks_sym_ts ON ticks (symbol, ts DESC)"
        )

        self._conn.commit()
        logger.info("sqlite_migrations_complete")

    def _safe_add_column(self, table: str, column: str, type_def: str) -> None:
        """Add column if not exists (SQLite doesn't support IF NOT EXISTS for columns)."""
        try:
            # Check if column exists
            cursor = self._conn.execute(f"PRAGMA table_info({table})")
            columns = [info[1] for info in cursor.fetchall()]
            if column not in columns:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_def}")
                logger.info("sqlite_column_added", extra={"table": table, "column": column})
        except Exception as e:
            logger.warning("sqlite_add_column_failed", extra={"table": table, "column": column, "error": str(e)})

    # ====================================================================
    # Decision Trace Methods
    # ====================================================================

    def save_decision_trace(
        self,
        *,
        symbol: str,
        stage: str,
        result: str,
        action: str = "",
        confidence: float = 0.0,
        strategy_name: str = "",
        reason: str = "",
        details: dict | None = None,
        regime: str = "",
        session: str = "",
        cycle: int = 0,
        mode: str = "DRY_RUN",
    ) -> None:
        """
        บันทึก decision trace — ใช้ buffer เพื่อลด disk I/O.

        Traces ถูกสะสมใน _trace_buffer → batch INSERT + single commit
        เมื่อถึง _TRACE_FLUSH_SIZE (10 traces).
        """
        if not self._conn:
            return
        # ─── สะสมใน buffer ───
        self._trace_buffer.append((
            datetime.now(timezone.utc).isoformat(),
            symbol, stage, result, action, confidence,
            strategy_name, reason, json.dumps(details or {}, default=str),
            regime, session, cycle, mode,
        ))
        # ─── Auto-flush เมื่อ buffer เต็ม ───
        if len(self._trace_buffer) >= _TRACE_FLUSH_SIZE:
            self.flush_traces()

    def flush_traces(self) -> None:
        """
        Batch INSERT ทุก traces ที่ค้างอยู่ใน buffer → single commit.

        เรียกจาก MasterLoop ท้ายทุก cycle เพื่อไม่ให้ข้อมูลค้าง.
        """
        if not self._conn or not self._trace_buffer:
            return
        try:
            self._conn.executemany(
                """INSERT INTO decision_traces
                   (timestamp, symbol, stage, result, action, confidence,
                    strategy_name, reason, details, regime, session, cycle, mode)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                self._trace_buffer,
            )
            self._conn.commit()
            self._trace_buffer.clear()
        except Exception as e:
            logger.error("flush_traces_error", extra={
                "error": str(e), "buffered": len(self._trace_buffer),
            })
            self._trace_buffer.clear()  # ล้าง buffer แม้ error เพื่อไม่ให้สะสม

    def prune_old_traces(self, keep_days: int = 7) -> int:
        """
        ลบ decision traces เก่ากว่า N วัน — ป้องกัน DB โตไม่หยุด.

        Args:
            keep_days: เก็บข้อมูลกี่วัน (default: 7)

        Returns:
            จำนวน rows ที่ถูกลบ
        """
        if not self._conn:
            return 0
        try:
            from datetime import timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
            cursor = self._conn.execute(
                "DELETE FROM decision_traces WHERE timestamp < ?", (cutoff,)
            )
            deleted = cursor.rowcount
            self._conn.commit()
            if deleted > 0:
                logger.info("traces_pruned", extra={
                    "deleted": deleted, "keep_days": keep_days,
                })
            return deleted
        except Exception as e:
            logger.error("prune_traces_error", extra={"error": str(e)})
            return 0

    def get_latest_decisions(self, limit: int = 20) -> list[dict]:
        """ดึง decisions ล่าสุด (grouped by symbol → latest per symbol)."""
        if not self._conn:
            return []
        rows = self._conn.execute(
            """SELECT dt.* FROM decision_traces dt
               INNER JOIN (
                   SELECT symbol, MAX(timestamp) as max_ts
                   FROM decision_traces GROUP BY symbol
               ) latest ON dt.symbol = latest.symbol AND dt.timestamp = latest.max_ts
               ORDER BY dt.timestamp DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_decisions(
        self,
        symbol: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """ดึง decisions จาก SQLite."""
        if not self._conn:
            return []
        if symbol:
            rows = self._conn.execute(
                """SELECT * FROM decision_traces WHERE symbol = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (symbol, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM decision_traces ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_recent_trades(self, limit: int = 50) -> list[dict]:
        """ดึงรายการเทรดล่าสุดสำหรับ Auto Coach."""
        if not self._conn:
            return []
        rows = self._conn.execute(
            """SELECT * FROM trade_journal 
               WHERE profit_usd IS NOT NULL 
               ORDER BY exit_time DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ====================================================================
    # Trade Journal Methods
    # ====================================================================

    def save_trade(
        self,
        *,
        symbol: str,
        action: str,
        lot_size: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float = 0.0,
        risk_usd: float = 0.0,
        risk_pct: float = 0.0,
        strategy_name: str = "",
        regime: str = "",
        session: str = "",
        ticket: int = 0,
        mode: str = "DRY_RUN",
        tags: list[str] | None = None,
    ) -> int:
        """บันทึกเทรดลง journal."""
        if not self._conn:
            return 0
        try:
            cursor = self._conn.execute(
                """INSERT INTO trade_journal
                   (symbol, action, lot_size, entry_price, stop_loss, take_profit,
                    risk_usd, risk_pct, strategy_name, entry_time, regime, session,
                    ticket, mode, tags)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol,
                    action,
                    lot_size,
                    entry_price,
                    stop_loss,
                    take_profit,
                    risk_usd,
                    risk_pct,
                    strategy_name,
                    datetime.now(timezone.utc).isoformat(),
                    regime,
                    session,
                    ticket,
                    mode,
                    json.dumps(tags or []),
                ),
            )
            self._conn.commit()
            return cursor.lastrowid or 0
        except Exception as e:
            logger.error("save_trade_error", extra={"error": str(e)})
            return 0

    def get_daily_pl(self, target_date: date | None = None) -> float:
        """คำนวณ PL วันนี้จาก trade journal."""
        if not self._conn:
            return 0.0
        if target_date is None:
            target_date = date.today()
        date_str = target_date.isoformat()
        row = self._conn.execute(
            """SELECT COALESCE(SUM(profit_usd), 0) as daily_pl
               FROM trade_journal
               WHERE date(exit_time) = ? AND profit_usd IS NOT NULL""",
            (date_str,),
        ).fetchone()
        return float(row["daily_pl"]) if row else 0.0

    def get_trade_count_today(self, symbol: str | None = None) -> int:
        """นับจำนวนเทรดวันนี้."""
        if not self._conn:
            return 0
        date_str = date.today().isoformat()
        if symbol:
            row = self._conn.execute(
                """SELECT COUNT(*) as cnt FROM trade_journal
                   WHERE date(entry_time) = ? AND symbol = ?""",
                (date_str, symbol),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) as cnt FROM trade_journal WHERE date(entry_time) = ?",
                (date_str,),
            ).fetchone()
        return int(row["cnt"]) if row else 0

    def get_analytics(self, symbol: str | None = None) -> dict:
        """คำนวณ performance metrics จาก trade journal."""
        if not self._conn:
            return self._empty_analytics()

        if symbol:
            rows = self._conn.execute(
                """SELECT * FROM trade_journal
                   WHERE profit_usd IS NOT NULL AND symbol = ?
                   ORDER BY exit_time DESC""",
                (symbol,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """SELECT * FROM trade_journal
                   WHERE profit_usd IS NOT NULL
                   ORDER BY exit_time DESC""",
            ).fetchall()

        if not rows:
            return self._empty_analytics()

        trades = [dict(r) for r in rows]
        total = len(trades)
        wins = [t for t in trades if (t.get("profit_usd") or 0) > 0]
        losses = [t for t in trades if (t.get("profit_usd") or 0) < 0]

        win_rate = len(wins) / total * 100 if total > 0 else 0
        total_profit = sum(t.get("profit_usd", 0) for t in wins)
        total_loss = abs(sum(t.get("profit_usd", 0) for t in losses))
        profit_factor = total_profit / total_loss if total_loss > 0 else 0

        # Max drawdown (sequential)
        equity_curve = []
        running_equity = 0.0
        for t in sorted(trades, key=lambda x: x.get("exit_time", "")):
            running_equity += t.get("profit_usd", 0)
            equity_curve.append(running_equity)

        max_dd = 0.0
        peak = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd

        avg_profit = sum(t.get("profit_usd", 0) for t in trades) / total if total > 0 else 0

        return {
            "total_trades": total,
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_dd, 2),
            "expectancy": round(avg_profit, 2),
            "avg_r": 0.0,  # needs risk tracking
            "sharpe_ratio": 0.0,  # needs daily returns
            "total_profit": round(total_profit, 2),
            "total_loss": round(total_loss, 2),
        }

    def _empty_analytics(self) -> dict:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "expectancy": 0.0,
            "avg_r": 0.0,
            "sharpe_ratio": 0.0,
            "total_profit": 0.0,
            "total_loss": 0.0,
        }

    # ====================================================================
    # Runtime State
    # ====================================================================

    def get_state(self, key: str, default: str = "") -> str:
        """ดึง runtime state."""
        if not self._conn:
            return default
        row = self._conn.execute(
            "SELECT value FROM runtime_state WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        """บันทึก runtime state."""
        if not self._conn:
            return
        self._conn.execute(
            """INSERT OR REPLACE INTO runtime_state (key, value, updated_at)
               VALUES (?, ?, ?)""",
            (key, value, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    # ====================================================================
    # Shadow Trades
    # ====================================================================

    def save_shadow_trades_batch(self, trades: list[tuple]) -> None:
        """Batch INSERT shadow trades — เรียกจาก ShadowRunner.flush()."""
        if not self._conn or not trades:
            return
        try:
            self._conn.executemany(
                """INSERT INTO shadow_trades
                   (timestamp, symbol, strategy_name, action, confidence,
                    regime, session, entry_price, stop_loss, take_profit,
                    lot_size, risk_usd, risk_pct, cycle, reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                trades,
            )
            self._conn.commit()
        except Exception as e:
            logger.error("save_shadow_batch_error", extra={"error": str(e)})

    def get_shadow_trades(
        self,
        symbol: str | None = None,
        strategy: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """ดึง shadow trades ล่าสุด."""
        if not self._conn:
            return []
        query = "SELECT * FROM shadow_trades WHERE 1=1"
        params: list = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if strategy:
            query += " AND strategy_name = ?"
            params.append(strategy)
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_shadow_analytics(
        self,
        symbol: str | None = None,
        hours: int = 24,
    ) -> list[dict]:
        """สรุป shadow performance แต่ละ strategy."""
        if not self._conn:
            return []
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        query = """
            SELECT strategy_name,
                   COUNT(*) as signal_count,
                   ROUND(AVG(confidence), 3) as avg_confidence,
                   SUM(CASE WHEN action = 'BUY' THEN 1 ELSE 0 END) as buy_signals,
                   SUM(CASE WHEN action = 'SELL' THEN 1 ELSE 0 END) as sell_signals,
                   ROUND(AVG(risk_pct), 2) as avg_risk_pct,
                   ROUND(AVG(lot_size), 3) as avg_lot_size
            FROM shadow_trades
            WHERE timestamp > ?
        """
        params: list = [cutoff]
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        query += " GROUP BY strategy_name ORDER BY signal_count DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_live_signal_stats(
        self,
        symbol: str | None = None,
        hours: int = 24,
    ) -> dict:
        """สรุป LIVE signal stats จาก decision_traces."""
        if not self._conn:
            return {}
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        query = """
            SELECT strategy_name,
                   COUNT(*) as signal_count,
                   ROUND(AVG(confidence), 3) as avg_confidence,
                   SUM(CASE WHEN action = 'BUY' THEN 1 ELSE 0 END) as buys,
                   SUM(CASE WHEN action = 'SELL' THEN 1 ELSE 0 END) as sells
            FROM decision_traces
            WHERE timestamp > ? AND action != 'HOLD' AND action != ''
        """
        params: list = [cutoff]
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        query += " GROUP BY strategy_name ORDER BY signal_count DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def prune_old_shadow_trades(self, keep_days: int = 7) -> int:
        """ลบ shadow trades เก่า — ป้องกัน DB โต."""
        if not self._conn:
            return 0
        try:
            from datetime import timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
            cursor = self._conn.execute(
                "DELETE FROM shadow_trades WHERE timestamp < ?", (cutoff,)
            )
            deleted = cursor.rowcount
            self._conn.commit()
            if deleted > 0:
                logger.info("shadow_trades_pruned", extra={"deleted": deleted})
            return deleted
        except Exception as e:
            logger.error("prune_shadow_error", extra={"error": str(e)})
            return 0

    # ────────────────────────────────────────────────────────────────
    # Shadow Evaluation Methods
    # ────────────────────────────────────────────────────────────────

    def get_unevaluated_shadows(self, limit: int = 200) -> list[dict]:
        """Get shadow trades that haven't been evaluated yet."""
        if not self._conn:
            return []
        try:
            rows = self._conn.execute("""
                SELECT id, timestamp, symbol, strategy_name, action,
                       confidence, regime, session,
                       entry_price, stop_loss, take_profit
                FROM shadow_trades
                WHERE outcome = 'PENDING' OR outcome IS NULL
                ORDER BY timestamp ASC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_unevaluated_shadows_error", extra={"error": str(e)})
            return []

    def update_shadow_outcome(
        self, shadow_id: int, outcome: str, pnl: float
    ) -> None:
        """Update a shadow trade with its evaluated outcome."""
        if not self._conn:
            return
        try:
            self._conn.execute("""
                UPDATE shadow_trades
                SET outcome = ?, outcome_pnl = ?, evaluated_at = ?
                WHERE id = ?
            """, (outcome, round(pnl, 4), datetime.now(timezone.utc).isoformat(), shadow_id))
            self._conn.commit()
        except Exception as e:
            logger.error("update_shadow_outcome_error", extra={"error": str(e), "id": shadow_id})

    def update_shadow_outcomes_batch(self, updates: list[tuple]) -> int:
        """Batch update shadow outcomes. Each tuple: (outcome, pnl, id)."""
        if not self._conn or not updates:
            return 0
        try:
            now = datetime.now(timezone.utc).isoformat()
            data = [(o, round(p, 4), now, sid) for o, p, sid in updates]
            self._conn.executemany("""
                UPDATE shadow_trades
                SET outcome = ?, outcome_pnl = ?, evaluated_at = ?
                WHERE id = ?
            """, data)
            self._conn.commit()
            return len(data)
        except Exception as e:
            logger.error("batch_shadow_outcome_error", extra={"error": str(e)})
            return 0

    def upsert_shadow_score(
        self, strategy_name: str, symbol: str, regime: str,
        wins: int, losses: int, pending: int, total_pnl: float,
        avg_confidence: float,
    ) -> None:
        """Upsert shadow scoreboard entry."""
        if not self._conn:
            return
        total = wins + losses + pending
        wr = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0.0
        try:
            self._conn.execute("""
                INSERT INTO shadow_scoreboard
                    (strategy_name, symbol, regime, total_signals, wins, losses,
                     pending, total_pnl, win_rate, avg_confidence, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_name, symbol, regime)
                DO UPDATE SET
                    total_signals = excluded.total_signals,
                    wins = excluded.wins,
                    losses = excluded.losses,
                    pending = excluded.pending,
                    total_pnl = excluded.total_pnl,
                    win_rate = excluded.win_rate,
                    avg_confidence = excluded.avg_confidence,
                    updated_at = excluded.updated_at
            """, (strategy_name, symbol, regime, total, wins, losses,
                  pending, round(total_pnl, 2), wr, round(avg_confidence, 3),
                  datetime.now(timezone.utc).isoformat()))
            self._conn.commit()
        except Exception as e:
            logger.error("upsert_shadow_score_error", extra={"error": str(e)})

    def get_shadow_scoreboard(self, symbol: str | None = None) -> list[dict]:
        """Get shadow scoreboard — ranked by win_rate."""
        if not self._conn:
            return []
        try:
            query = "SELECT * FROM shadow_scoreboard WHERE total_signals > 0"
            params: list = []
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            query += " ORDER BY win_rate DESC, total_pnl DESC"
            rows = self._conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_shadow_scoreboard_error", extra={"error": str(e)})
            return []

    def get_shadow_stats_for_scoring(self) -> list[dict]:
        """Aggregate shadow trades for scoreboard refresh."""
        if not self._conn:
            return []
        try:
            rows = self._conn.execute("""
                SELECT strategy_name, symbol, regime,
                       COUNT(*) as total,
                       SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END) as losses,
                       SUM(CASE WHEN outcome = 'PENDING' OR outcome IS NULL THEN 1 ELSE 0 END) as pending,
                       SUM(COALESCE(outcome_pnl, 0)) as total_pnl,
                       AVG(confidence) as avg_confidence
                FROM shadow_trades
                WHERE action IN ('BUY', 'SELL')
                GROUP BY strategy_name, symbol, regime
            """).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("shadow_stats_error", extra={"error": str(e)})
            return []

    # ====================================================================
    # Strategy Params — persist optimized parameters
    # ====================================================================

    def save_strategy_params(
        self,
        *,
        symbol: str,
        strategy_name: str,
        params: dict,
        win_rate: float = 0.0,
        profit_factor: float = 0.0,
        total_pnl: float = 0.0,
        max_drawdown_pct: float = 0.0,
        total_trades: int = 0,
        backtest_days: int = 0,
        label: str = "",
    ) -> None:
        """บันทึก/อัพเดทค่า params ที่ optimize แล้ว — ใช้ UPSERT by (symbol, strategy_name)."""
        if not self._conn:
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute("""
                INSERT INTO strategy_params
                    (symbol, strategy_name, params_json, win_rate, profit_factor,
                     total_pnl, max_drawdown_pct, total_trades, backtest_days,
                     label, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(symbol, strategy_name)
                DO UPDATE SET
                    params_json = excluded.params_json,
                    win_rate = excluded.win_rate,
                    profit_factor = excluded.profit_factor,
                    total_pnl = excluded.total_pnl,
                    max_drawdown_pct = excluded.max_drawdown_pct,
                    total_trades = excluded.total_trades,
                    backtest_days = excluded.backtest_days,
                    label = excluded.label,
                    is_active = 1,
                    updated_at = excluded.updated_at
            """, (
                symbol, strategy_name, json.dumps(params),
                win_rate, profit_factor, total_pnl, max_drawdown_pct,
                total_trades, backtest_days, label, now, now,
            ))
            self._conn.commit()
            logger.info("strategy_params_saved", extra={
                "symbol": symbol, "strategy": strategy_name,
                "label": label, "win_rate": win_rate,
            })
        except Exception as e:
            logger.error("save_strategy_params_error", extra={"error": str(e)})

    def get_strategy_params(
        self,
        symbol: str,
        strategy_name: str,
    ) -> dict | None:
        """ดึงค่า params ที่ optimize แล้ว — return None ถ้าไม่มี."""
        if not self._conn:
            return None
        try:
            row = self._conn.execute(
                """SELECT * FROM strategy_params
                   WHERE symbol = ? AND strategy_name = ? AND is_active = 1""",
                (symbol, strategy_name),
            ).fetchone()
            if row:
                d = dict(row)
                d['params'] = json.loads(d.get('params_json', '{}'))
                return d
            return None
        except Exception as e:
            logger.error("get_strategy_params_error", extra={"error": str(e)})
            return None

    def get_all_strategy_params(self, symbol: str | None = None) -> list[dict]:
        """ดึงทุก params ที่ optimize แล้ว."""
        if not self._conn:
            return []
        try:
            if symbol:
                rows = self._conn.execute(
                    "SELECT * FROM strategy_params WHERE symbol = ? AND is_active = 1 ORDER BY win_rate DESC",
                    (symbol,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM strategy_params WHERE is_active = 1 ORDER BY symbol, win_rate DESC",
                ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d['params'] = json.loads(d.get('params_json', '{}'))
                result.append(d)
            return result
        except Exception as e:
            logger.error("get_all_params_error", extra={"error": str(e)})
            return []

    # ====================================================================
    # Backtest Results — audit trail
    # ====================================================================

    def save_backtest_result(
        self,
        *,
        symbol: str,
        strategy_name: str,
        label: str = "",
        params: dict | None = None,
        win_rate: float = 0.0,
        profit_factor: float = 0.0,
        total_pnl: float = 0.0,
        max_drawdown_pct: float = 0.0,
        total_trades: int = 0,
        winning_trades: int = 0,
        losing_trades: int = 0,
        backtest_days: int = 0,
        expectancy: float = 0.0,
        per_regime: dict | None = None,
    ) -> None:
        """บันทึกผลลัพธ์ backtest สำหรับ audit trail — append-only."""
        if not self._conn:
            return
        try:
            self._conn.execute("""
                INSERT INTO backtest_results
                    (symbol, strategy_name, label, params_json,
                     win_rate, profit_factor, total_pnl, max_drawdown_pct,
                     total_trades, winning_trades, losing_trades,
                     backtest_days, expectancy, per_regime_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol, strategy_name, label,
                json.dumps(params or {}),
                win_rate, profit_factor, total_pnl, max_drawdown_pct,
                total_trades, winning_trades, losing_trades,
                backtest_days, expectancy,
                json.dumps(per_regime or {}),
                datetime.now(timezone.utc).isoformat(),
            ))
            self._conn.commit()
        except Exception as e:
            logger.error("save_backtest_result_error", extra={"error": str(e)})

    def get_backtest_results(
        self,
        symbol: str | None = None,
        strategy_name: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """ดึงผลลัพธ์ backtest จาก audit trail."""
        if not self._conn:
            return []
        try:
            query = "SELECT * FROM backtest_results WHERE 1=1"
            params_list: list = []
            if symbol:
                query += " AND symbol = ?"
                params_list.append(symbol)
            if strategy_name:
                query += " AND strategy_name = ?"
                params_list.append(strategy_name)
            query += " ORDER BY created_at DESC LIMIT ?"
            params_list.append(limit)
            rows = self._conn.execute(query, params_list).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d['params'] = json.loads(d.get('params_json', '{}'))
                d['per_regime'] = json.loads(d.get('per_regime_json', '{}'))
                result.append(d)
            return result
        except Exception as e:
            logger.error("get_backtest_results_error", extra={"error": str(e)})
            return []

    # ====================================================================
    # Backtest Routing — data-driven strategy selection
    # ====================================================================

    def save_backtest_routing(
        self,
        *,
        symbol: str,
        regime: str,
        strategy: str,
        profit_factor: float = 0.0,
        win_rate: float = 0.0,
        total_trades: int = 0,
        total_pnl: float = 0.0,
        score: float = 0.0,
    ) -> None:
        """Upsert backtest routing entry — best strategy per (symbol, regime)."""
        if not self._conn:
            return
        try:
            self._conn.execute("""
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
                profit_factor, win_rate, total_trades,
                total_pnl, score,
                datetime.now(timezone.utc).isoformat(),
            ))
            self._conn.commit()
        except Exception as e:
            logger.error("save_routing_error", extra={"error": str(e)})

    def get_backtest_routing(self, symbol: str | None = None) -> list[dict]:
        """Get backtest routing table (optionally filtered by symbol)."""
        if not self._conn:
            return []
        try:
            if symbol:
                rows = self._conn.execute(
                    "SELECT * FROM backtest_routing WHERE symbol = ? ORDER BY score DESC",
                    (symbol,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM backtest_routing ORDER BY symbol, score DESC",
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_routing_error", extra={"error": str(e)})
            return []

    # ====================================================================
    # Strategy Registry — dynamic strategy-regime mapping
    # ====================================================================

    def save_strategy_registry(
        self,
        *,
        strategy_name: str,
        class_name: str,
        module_path: str,
        timeframe: str = "M5",
        asset_class: str = "*",
        suitable_regimes: list[str] | None = None,
        priority: int = 50,
        is_active: bool = True,
    ) -> None:
        """Upsert strategy registry entry — dynamic strategy-regime mapping."""
        if not self._conn:
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute("""
                INSERT INTO strategy_registry
                    (strategy_name, class_name, module_path, timeframe,
                     asset_class, suitable_regimes, priority, is_active,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_name)
                DO UPDATE SET
                    class_name = excluded.class_name,
                    module_path = excluded.module_path,
                    timeframe = excluded.timeframe,
                    asset_class = excluded.asset_class,
                    suitable_regimes = excluded.suitable_regimes,
                    priority = excluded.priority,
                    is_active = excluded.is_active,
                    updated_at = excluded.updated_at
            """, (
                strategy_name, class_name, module_path, timeframe,
                asset_class, json.dumps(suitable_regimes or []),
                priority, 1 if is_active else 0, now, now,
            ))
            self._conn.commit()
        except Exception as e:
            logger.error("save_strategy_registry_error", extra={"error": str(e)})

    def get_strategy_registry(self, active_only: bool = True) -> list[dict]:
        """Get all registered strategies (optionally active only)."""
        if not self._conn:
            return []
        try:
            if active_only:
                rows = self._conn.execute(
                    "SELECT * FROM strategy_registry WHERE is_active = 1 ORDER BY priority DESC",
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM strategy_registry ORDER BY priority DESC",
                ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["suitable_regimes"] = json.loads(d.get("suitable_regimes", "[]"))
                result.append(d)
            return result
        except Exception as e:
            logger.error("get_strategy_registry_error", extra={"error": str(e)})
            return []

    # ====================================================================
    # Tick Storage (แทน QuestDB — ประหยัด RAM, ไม่ต้อง JVM)
    # ====================================================================

    _TICK_BUFFER: list[tuple] = []
    _TICK_FLUSH_SIZE = 50  # flush ทุก 50 ticks (batch I/O)

    def ingest_ticks(self, symbol: str, ticks: list[dict]) -> None:
        """
        Batch INSERT ticks — ใช้ executemany + single commit.

        Args:
            symbol: ชื่อ symbol เช่น XAUUSDc
            ticks: list ของ tick dict จาก MT5 (keys: time, bid, ask, last, volume, flags)
        """
        if not self._conn or not ticks:
            return
        try:
            rows = []
            for t in ticks:
                ts_val = t.get("time")
                if isinstance(ts_val, (int, float)):
                    from datetime import datetime as _dt, timezone as _tz
                    ts_str = _dt.fromtimestamp(ts_val, tz=_tz.utc).isoformat()
                elif isinstance(ts_val, datetime):
                    ts_str = ts_val.isoformat()
                else:
                    continue
                rows.append((
                    ts_str, symbol,
                    t.get("bid", 0.0), t.get("ask", 0.0), t.get("last", 0.0),
                    int(t.get("volume", 0)), int(t.get("flags", 0)),
                ))
            if not rows:
                return
            self._conn.executemany(
                "INSERT INTO ticks (ts, symbol, bid, ask, last, volume, flags) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
            logger.debug("ticks_ingested", extra={"symbol": symbol, "count": len(rows)})
        except Exception as e:
            logger.debug("ticks_ingest_error", extra={"symbol": symbol, "error": str(e)})

    def get_latest_ticks(self, symbols: list[str]) -> dict[str, dict]:
        """
        ดึง tick ล่าสุดของแต่ละ symbol.

        Returns:
            {symbol: {ts, bid, ask, last, volume}} — dict per symbol
        """
        if not self._conn or not symbols:
            return {}
        result: dict[str, dict] = {}
        try:
            for sym in symbols:
                row = self._conn.execute(
                    "SELECT ts, bid, ask, last, volume "
                    "FROM ticks WHERE symbol = ? ORDER BY ts DESC LIMIT 1",
                    (sym,),
                ).fetchone()
                if row:
                    result[sym] = dict(row)
        except Exception as e:
            logger.debug("get_latest_ticks_error", extra={"error": str(e)})
        return result

    def cleanup_old_ticks(self, retention_days: int = 3) -> int:
        """
        ลบ ticks เก่ากว่า N วัน — ป้องกัน DB โตไม่หยุด.

        Default: เก็บ 3 วัน (เหมือน QuestDB เดิม)
        """
        if not self._conn or retention_days <= 0:
            return 0
        try:
            from datetime import timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
            cursor = self._conn.execute("DELETE FROM ticks WHERE ts < ?", (cutoff,))
            deleted = cursor.rowcount
            self._conn.commit()
            if deleted > 0:
                logger.info("ticks_pruned", extra={"deleted": deleted, "retention_days": retention_days})
            return deleted
        except Exception as e:
            logger.error("ticks_prune_error", extra={"error": str(e)})
            return 0

    # ====================================================================
    # Health
    # ====================================================================

    def health_check(self) -> bool:
        """ตรวจสอบว่า SQLite ยังทำงานได้."""
        try:
            if self._conn:
                self._conn.execute("SELECT 1")
                return True
        except Exception:
            pass
        return False

    def disconnect(self) -> None:
        """ปิดการเชื่อมต่อ — flush traces ค้างก่อนปิด."""
        if self._conn:
            self.flush_traces()  # flush ข้อมูลค้างก่อนปิด
            self._conn.close()
            self._conn = None
            logger.info("sqlite_disconnected")
