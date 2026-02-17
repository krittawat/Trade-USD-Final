"""
Trade Analyzer — วิเคราะห์ผลเทรดย้อนหลังเพื่อหา patterns.

คุณสมบัติ:
    - Win/loss breakdown ตาม session, regime, strategy, day-of-week
    - หา indicator conditions ที่สัมพันธ์กับ win/loss
    - Feed results กลับเข้า Brain (MemoryStore)
    - สร้าง summary report เป็น JSON

ใช้ข้อมูลจาก:
    - SQLite: decision traces + trade journal
    - DuckDB: ถ้าต้องการ analytics ที่ซับซ้อนกว่า (optional)
"""

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger("TradeAnalyzer")


class TradeAnalyzer:
    """
    วิเคราะห์ผลเทรดจาก SQLite → หา patterns → feed กลับเข้า Brain.
    """

    def __init__(self, db_path: str = "backend/data/sqlite/brain.db") -> None:
        self.db_path = db_path

    def analyze(self, min_trades: int = 10) -> dict:
        """
        วิเคราะห์ผลเทรดทั้งหมด.

        Returns:
            dict: {
                "total_trades": int,
                "overall": {win_rate, pf, avg_rr, total_pnl},
                "by_session": {...},
                "by_regime": {...},
                "by_strategy": {...},
                "by_day": {...},
                "worst_patterns": [...],  # สิ่งที่ต้องหลีกเลี่ยง
                "best_patterns": [...],   # สิ่งที่ต้องเน้น
            }
        """
        if not Path(self.db_path).exists():
            logger.warning("trade_analyzer_no_db", extra={"path": self.db_path})
            return {"total_trades": 0, "message": "No database found"}

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        try:
            trades = self._fetch_trades(conn)
        except Exception as e:
            logger.error("trade_analyzer_fetch_error", extra={"error": str(e)})
            conn.close()
            return {"total_trades": 0, "error": str(e)}

        if len(trades) < min_trades:
            conn.close()
            return {
                "total_trades": len(trades),
                "message": f"Need at least {min_trades} trades for analysis",
            }

        report = {
            "total_trades": len(trades),
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "overall": self._compute_stats(trades),
            "by_session": self._breakdown(trades, key="session"),
            "by_regime": self._breakdown(trades, key="regime"),
            "by_strategy": self._breakdown(trades, key="strategy_name"),
            "by_day": self._breakdown_by_day(trades),
            "worst_patterns": self._find_worst(trades),
            "best_patterns": self._find_best(trades),
        }

        conn.close()

        logger.info("trade_analysis_complete", extra={
            "total_trades": report["total_trades"],
            "win_rate": report["overall"]["win_rate"],
            "patterns_found": len(report["worst_patterns"]) + len(report["best_patterns"]),
        })

        return report

    def _fetch_trades(self, conn: sqlite3.Connection) -> list[dict]:
        """ดึง trades จาก strategy_performance table."""
        cursor = conn.execute("""
            SELECT strategy_name, symbol, regime, session,
                   total_trades, wins, losses,
                   total_profit, total_loss,
                   avg_rr, win_rate, profit_factor,
                   updated_at
            FROM strategy_performance
            WHERE total_trades > 0
            ORDER BY updated_at DESC
        """)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def _compute_stats(self, trades: list[dict]) -> dict:
        """คำนวณ overall statistics."""
        total_trades = sum(t.get("total_trades", 0) for t in trades)
        total_wins = sum(t.get("wins", 0) for t in trades)
        total_profit = sum(t.get("total_profit", 0) for t in trades)
        total_loss = abs(sum(t.get("total_loss", 0) for t in trades))

        win_rate = (total_wins / total_trades * 100) if total_trades > 0 else 0
        pf = (total_profit / total_loss) if total_loss > 0 else float('inf')
        avg_rr_vals = [t.get("avg_rr", 0) for t in trades if t.get("total_trades", 0) > 0]
        avg_rr = sum(avg_rr_vals) / len(avg_rr_vals) if avg_rr_vals else 0

        return {
            "total_trades": total_trades,
            "wins": total_wins,
            "win_rate": round(win_rate, 1),
            "profit_factor": round(pf, 2),
            "total_pnl": round(total_profit - total_loss, 2),
            "avg_rr": round(avg_rr, 2),
        }

    def _breakdown(self, trades: list[dict], key: str) -> dict:
        """Win/loss breakdown ตาม key (session, regime, strategy)."""
        groups: dict[str, list[dict]] = defaultdict(list)
        for t in trades:
            group_val = t.get(key, "UNKNOWN")
            groups[group_val].append(t)

        result = {}
        for group_name, group_trades in groups.items():
            stats = self._compute_stats(group_trades)
            stats["group"] = group_name
            result[group_name] = stats

        return result

    def _breakdown_by_day(self, trades: list[dict]) -> dict:
        """Performance breakdown by day-of-week."""
        groups: dict[str, list[dict]] = defaultdict(list)
        for t in trades:
            updated_at = t.get("updated_at", "")
            if updated_at:
                try:
                    dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    day_name = dt.strftime("%A")  # Monday, Tuesday, etc.
                    groups[day_name].append(t)
                except (ValueError, TypeError):
                    groups["Unknown"].append(t)

        result = {}
        for day, day_trades in groups.items():
            result[day] = self._compute_stats(day_trades)

        return result

    def _find_worst(self, trades: list[dict], top_n: int = 5) -> list[dict]:
        """หา patterns ที่แย่ที่สุด (ควรหลีกเลี่ยง)."""
        patterns = []
        for t in trades:
            if t.get("total_trades", 0) >= 3:  # ต้องมีอย่างน้อย 3 เทรด
                wr = t.get("win_rate", 50)
                pf = t.get("profit_factor", 1.0)
                if wr < 40 or (pf is not None and pf < 0.8):
                    patterns.append({
                        "strategy": t.get("strategy_name", "?"),
                        "regime": t.get("regime", "?"),
                        "session": t.get("session", "?"),
                        "win_rate": wr,
                        "profit_factor": pf,
                        "trades": t.get("total_trades", 0),
                        "warning": "LOW_WIN_RATE" if wr < 40 else "LOW_PF",
                    })

        patterns.sort(key=lambda x: x.get("win_rate", 50))
        return patterns[:top_n]

    def _find_best(self, trades: list[dict], top_n: int = 5) -> list[dict]:
        """หา patterns ที่ดีที่สุด (ควรเน้น)."""
        patterns = []
        for t in trades:
            if t.get("total_trades", 0) >= 5:
                wr = t.get("win_rate", 0)
                pf = t.get("profit_factor", 0)
                if wr > 60 and pf and pf > 1.5:
                    patterns.append({
                        "strategy": t.get("strategy_name", "?"),
                        "regime": t.get("regime", "?"),
                        "session": t.get("session", "?"),
                        "win_rate": wr,
                        "profit_factor": pf,
                        "trades": t.get("total_trades", 0),
                    })

        patterns.sort(key=lambda x: x.get("profit_factor", 0), reverse=True)
        return patterns[:top_n]


    def feed_to_brain(self, brain_memory, report: dict) -> int:
        """
        Feed analysis results กลับเข้า Brain MemoryStore.

        Returns:
            จำนวน patterns ที่ feed เข้าไป
        """
        count = 0

        # Feed worst patterns as "avoid" signals
        for pattern in report.get("worst_patterns", []):
            try:
                brain_memory.update_feature_aggregate(
                    symbol="GLOBAL",
                    regime=pattern.get("regime", "ALL"),
                    feature_name=f"avoid_{pattern['strategy']}_{pattern['session']}",
                    value=pattern.get("win_rate", 0),
                )
                count += 1
            except Exception:
                pass

        # Feed best patterns as "prefer" signals
        for pattern in report.get("best_patterns", []):
            try:
                brain_memory.update_feature_aggregate(
                    symbol="GLOBAL",
                    regime=pattern.get("regime", "ALL"),
                    feature_name=f"prefer_{pattern['strategy']}_{pattern['session']}",
                    value=pattern.get("profit_factor", 1.0),
                )
                count += 1
            except Exception:
                pass

        logger.info("brain_patterns_fed", extra={"count": count})
        return count
