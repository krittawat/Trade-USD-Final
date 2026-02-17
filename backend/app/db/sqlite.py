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
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")  # balance safety + speed
        logger.info("sqlite_connected", extra={"path": str(self.db_path)})
        self._run_migrations()

    def _run_migrations(self) -> None:
        """สร้างตารางที่จำเป็น (idempotent)."""
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

        # Shadow trades — virtual trades จาก shadow strategies
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

        # Create indexes for performance
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_dt_symbol ON decision_traces(symbol, timestamp)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tj_symbol ON trade_journal(symbol, entry_time)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_st_symbol ON shadow_trades(symbol, timestamp)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_st_strategy ON shadow_trades(strategy_name, timestamp)
        """)

        self._conn.commit()
        logger.info("sqlite_migrations_complete")

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
            strategy_name, reason, json.dumps(details or {}),
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
