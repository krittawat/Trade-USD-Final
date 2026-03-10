"""
OnlineLearner — เรียนรู้จากผลเทรดแบบ real-time.

หน้าที่:
    1. บันทึกผลเทรดทุกครั้ง (win/loss, strategy, session, indicators)
    2. สะสมสถิติ: win rate ต่อ hour, session, strategy, regime
    3. คำนวณ adaptive parameters (แนะนำช่วงเวลาที่ดี/แย่)
    4. แนะนำเปลี่ยน strategy ถ้า performance ต่ำเกิน

กฎ RAM (8GB mode):
    - เก็บแค่ rolling window 500 trades
    - ไม่เก็บ raw candle data — เก็บแค่ summary stats
    - Auto-compact ทุก 100 trades

Safety:
    - ผลลัพธ์เป็น advisor เท่านั้น — ไม่ override Risk Engine
    - ต้องมีอย่างน้อย 10 trades ก่อนแนะนำ
"""

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── Config ───
MAX_TRADES_WINDOW = 500   # keep last 500 trades in memory
MIN_TRADES_FOR_INSIGHT = 10
COMPACT_EVERY = 100
DATA_DIR = Path("data/learning")


@dataclass
class TradeRecord:
    """บันทึกผลเทรด 1 รายการ (compact)."""
    symbol: str = ""
    direction: str = ""           # BUY / SELL
    strategy: str = ""
    profit: float = 0.0
    session: str = ""             # ASIA / LONDON / NY
    hour: int = 0                 # 0-23
    day_of_week: int = 0          # 0=Mon, 6=Sun
    duration_minutes: int = 0
    confidence: float = 0.0
    regime: str = ""
    rsi: float = 50.0
    web_sentiment: float = 0.0    # from WebResearcher
    timestamp: float = 0.0


@dataclass
class LearningInsight:
    """Insight ที่เรียนรู้ได้."""
    category: str  # "hour", "session", "strategy", "regime"
    key: str       # e.g. "XAUUSD|14" or "NY"
    message: str
    data: dict = field(default_factory=dict)
    created_at: str = ""


class OnlineLearner:
    """
    เรียนรู้จากเทรดทุกตัว → สะสมสถิติ → แนะนำปรับปรุง.

    วิธีใช้:
        learner = OnlineLearner()
        learner.learn_from_trade({...})
        insights = learner.get_learning_insights()
        params = learner.get_adaptive_params("XAUUSD")
    """

    def __init__(self) -> None:
        self.trades: list[TradeRecord] = []
        self.insights: list[LearningInsight] = []

        # ─── Rolling statistics ─── (auto-updated on each trade)
        self.hour_stats: dict[str, dict] = defaultdict(
            lambda: {"wins": 0, "losses": 0, "profit": 0.0}
        )
        self.session_stats: dict[str, dict] = defaultdict(
            lambda: {"wins": 0, "losses": 0, "profit": 0.0}
        )
        self.strategy_stats: dict[str, dict] = defaultdict(
            lambda: {"wins": 0, "losses": 0, "profit": 0.0, "count": 0}
        )
        self.symbol_stats: dict[str, dict] = defaultdict(
            lambda: {"wins": 0, "losses": 0, "profit": 0.0, "count": 0}
        )

        # ─── Adaptive parameters (computed after insights) ───
        self.adaptive_params: dict[str, dict] = {}

        self._trade_count = 0
        self._load_persisted()

    # ────────────────────────────────────────────────────────────────
    # learn_from_trade() — บันทึกและเรียนรู้จากเทรด 1 ตัว
    # ────────────────────────────────────────────────────────────────

    def learn_from_trade(self, trade_data: dict) -> None:
        """
        บันทึกผลเทรดและอัพเดท rolling statistics.

        Args:
            trade_data: dict with keys:
                symbol, direction, strategy, profit, session,
                hour, day_of_week, duration_minutes, confidence,
                regime, rsi, web_sentiment
        """
        record = TradeRecord(
            symbol=trade_data.get("symbol", ""),
            direction=trade_data.get("direction", ""),
            strategy=trade_data.get("strategy", ""),
            profit=float(trade_data.get("profit", 0.0)),
            session=trade_data.get("session", ""),
            hour=int(trade_data.get("hour", 0)),
            day_of_week=int(trade_data.get("day_of_week", 0)),
            duration_minutes=int(trade_data.get("duration_minutes", 0)),
            confidence=float(trade_data.get("confidence", 0.0)),
            regime=trade_data.get("regime", ""),
            rsi=float(trade_data.get("rsi", 50.0)),
            web_sentiment=float(trade_data.get("web_sentiment", 0.0)),
            timestamp=time.time(),
        )

        self.trades.append(record)
        self._trade_count += 1

        # Update rolling stats
        is_win = record.profit > 0
        sym = record.symbol

        hour_key = f"{sym}|{record.hour}"
        self.hour_stats[hour_key]["wins" if is_win else "losses"] += 1
        self.hour_stats[hour_key]["profit"] += record.profit

        session_key = f"{sym}|{record.session}"
        self.session_stats[session_key]["wins" if is_win else "losses"] += 1
        self.session_stats[session_key]["profit"] += record.profit

        strat_key = f"{sym}|{record.strategy}"
        self.strategy_stats[strat_key]["wins" if is_win else "losses"] += 1
        self.strategy_stats[strat_key]["profit"] += record.profit
        self.strategy_stats[strat_key]["count"] += 1

        self.symbol_stats[sym]["wins" if is_win else "losses"] += 1
        self.symbol_stats[sym]["profit"] += record.profit
        self.symbol_stats[sym]["count"] += 1

        # Compact if needed
        if self._trade_count % COMPACT_EVERY == 0:
            self._compact()
            self._generate_insights()
            self._compute_adaptive_params()
            self._persist()

        logger.info("online_learn_recorded", extra={
            "symbol": sym,
            "profit": record.profit,
            "strategy": record.strategy,
            "session": record.session,
            "total_trades": self._trade_count,
        })

    # ────────────────────────────────────────────────────────────────
    # get_learning_insights() — ข้อมูลที่เรียนรู้ได้
    # ────────────────────────────────────────────────────────────────

    def get_learning_insights(self) -> list[dict]:
        """คืน insights ที่เรียนรู้ได้ทั้งหมด."""
        if not self.insights:
            self._generate_insights()
        return [asdict(i) for i in self.insights[-20:]]

    def get_performance_summary(self) -> dict:
        """สรุป performance จาก trades ที่สะสม."""
        if not self.trades:
            return {"status": "no_data", "total_trades": 0}

        total = len(self.trades)
        wins = sum(1 for t in self.trades if t.profit > 0)
        losses = total - wins
        total_profit = sum(t.profit for t in self.trades)
        avg_profit = total_profit / total if total > 0 else 0.0
        win_rate = wins / total if total > 0 else 0.0

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 3),
            "total_profit": round(total_profit, 2),
            "avg_profit": round(avg_profit, 2),
            "best_trade": round(max((t.profit for t in self.trades), default=0.0), 2),
            "worst_trade": round(min((t.profit for t in self.trades), default=0.0), 2),
        }

    # ────────────────────────────────────────────────────────────────
    # get_adaptive_params() — พารามิเตอร์ที่ปรับตามประสบการณ์
    # ────────────────────────────────────────────────────────────────

    def get_adaptive_params(self, symbol: str) -> dict:
        """คืน adaptive parameters สำหรับ symbol."""
        if not self.adaptive_params:
            self._compute_adaptive_params()
        return self.adaptive_params.get(symbol, {})

    def get_best_trading_hours(self, symbol: str = "ALL") -> list[dict]:
        """คืน hours ที่ win rate สูงสุด."""
        results = []
        for key, stats in self.hour_stats.items():
            sym, hour = key.rsplit("|", 1)
            if symbol != "ALL" and sym != symbol:
                continue
            total = stats["wins"] + stats["losses"]
            if total < 3:
                continue
            wr = stats["wins"] / total
            results.append({
                "symbol": sym,
                "hour": int(hour),
                "win_rate": round(wr, 3),
                "trades": total,
                "profit": round(stats["profit"], 2),
            })
        results.sort(key=lambda x: x["win_rate"], reverse=True)
        return results[:10]

    def should_adjust_strategy(self, symbol: str) -> dict | None:
        """
        ตรวจว่าควรเปลี่ยน strategy หรือไม่.

        Returns:
            dict with recommendation or None
        """
        best_strat = None
        best_wr = 0.0
        worst_strat = None
        worst_wr = 1.0

        for key, stats in self.strategy_stats.items():
            sym, strat = key.rsplit("|", 1)
            if sym != symbol:
                continue
            total = stats["wins"] + stats["losses"]
            if total < MIN_TRADES_FOR_INSIGHT:
                continue
            wr = stats["wins"] / total
            if wr > best_wr:
                best_wr = wr
                best_strat = strat
            if wr < worst_wr:
                worst_wr = wr
                worst_strat = strat

        if best_strat and worst_strat and best_strat != worst_strat and (best_wr - worst_wr) > 0.15:
            return {
                "action": "SWITCH",
                "from_strategy": worst_strat,
                "to_strategy": best_strat,
                "from_wr": round(worst_wr, 3),
                "to_wr": round(best_wr, 3),
                "reason": f"{best_strat} has {best_wr:.0%} WR vs {worst_strat} at {worst_wr:.0%}",
            }
        return None

    def get_status(self) -> dict:
        """สถานะของ OnlineLearner."""
        return {
            "active": True,
            "total_trades_learned": self._trade_count,
            "trades_in_memory": len(self.trades),
            "insights_count": len(self.insights),
            "symbols_tracked": list(self.symbol_stats.keys()),
        }

    # ────────────────────────────────────────────────────────────────
    # Internal — Insight Generation
    # ────────────────────────────────────────────────────────────────

    def _generate_insights(self) -> None:
        """สร้าง insights จาก rolling stats."""
        self.insights.clear()
        now = datetime.now(timezone.utc).isoformat()

        # ─── Best/Worst Hours ───
        for key, stats in self.hour_stats.items():
            total = stats["wins"] + stats["losses"]
            if total < MIN_TRADES_FOR_INSIGHT:
                continue
            wr = stats["wins"] / total
            sym, hour = key.rsplit("|", 1)

            if wr >= 0.65:
                self.insights.append(LearningInsight(
                    category="hour",
                    key=key,
                    message=f"🟢 {sym} Hour {hour}: {wr:.0%} WR ({total} trades, ${stats['profit']:.2f})",
                    data={"win_rate": wr, "trades": total, "profit": stats["profit"]},
                    created_at=now,
                ))
            elif wr <= 0.35:
                self.insights.append(LearningInsight(
                    category="hour",
                    key=key,
                    message=f"🔴 {sym} Hour {hour}: {wr:.0%} WR — AVOID ({total} trades)",
                    data={"win_rate": wr, "trades": total, "profit": stats["profit"]},
                    created_at=now,
                ))

        # ─── Best/Worst Sessions ───
        for key, stats in self.session_stats.items():
            total = stats["wins"] + stats["losses"]
            if total < MIN_TRADES_FOR_INSIGHT:
                continue
            wr = stats["wins"] / total
            sym, session = key.rsplit("|", 1)

            if wr >= 0.60:
                self.insights.append(LearningInsight(
                    category="session",
                    key=key,
                    message=f"🟢 {sym} {session}: {wr:.0%} WR — Good session",
                    data={"win_rate": wr, "trades": total, "profit": stats["profit"]},
                    created_at=now,
                ))
            elif wr <= 0.40:
                self.insights.append(LearningInsight(
                    category="session",
                    key=key,
                    message=f"🔴 {sym} {session}: {wr:.0%} WR — Bad session",
                    data={"win_rate": wr, "trades": total, "profit": stats["profit"]},
                    created_at=now,
                ))

        # ─── Strategy Rankings ───
        for key, stats in self.strategy_stats.items():
            total = stats["count"]
            if total < MIN_TRADES_FOR_INSIGHT:
                continue
            wr = stats["wins"] / (stats["wins"] + stats["losses"]) if (stats["wins"] + stats["losses"]) > 0 else 0
            sym, strat = key.rsplit("|", 1)

            self.insights.append(LearningInsight(
                category="strategy",
                key=key,
                message=f"📊 {sym} {strat}: {wr:.0%} WR, ${stats['profit']:.2f} ({total} trades)",
                data={"win_rate": wr, "trades": total, "profit": stats["profit"]},
                created_at=now,
            ))

        logger.info("insights_generated", extra={"count": len(self.insights)})

    def _compute_adaptive_params(self) -> None:
        """คำนวณ adaptive parameters จาก stats."""
        symbols = set(self.symbol_stats.keys())
        for sym in symbols:
            best_hours = self.get_best_trading_hours(sym)
            best_sessions = []
            for key, stats in self.session_stats.items():
                s, session = key.rsplit("|", 1)
                if s != sym:
                    continue
                total = stats["wins"] + stats["losses"]
                if total >= MIN_TRADES_FOR_INSIGHT:
                    wr = stats["wins"] / total
                    if wr >= 0.55:
                        best_sessions.append(session)

            self.adaptive_params[sym] = {
                "best_hours": [h["hour"] for h in best_hours[:5]],
                "best_sessions": best_sessions,
                "overall_wr": round(
                    self.symbol_stats[sym]["wins"]
                    / max(1, self.symbol_stats[sym]["count"]),
                    3,
                ),
                "total_profit": round(self.symbol_stats[sym]["profit"], 2),
            }

    def _compact(self) -> None:
        """Trim trades to MAX_TRADES_WINDOW."""
        if len(self.trades) > MAX_TRADES_WINDOW:
            self.trades = self.trades[-MAX_TRADES_WINDOW:]

    # ────────────────────────────────────────────────────────────────
    # Persistence — save/load to disk
    # ────────────────────────────────────────────────────────────────

    def _persist(self) -> None:
        """Save aggregated stats to JSON."""
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            path = DATA_DIR / "online_learner_stats.json"

            data = {
                "trade_count": self._trade_count,
                "hour_stats": dict(self.hour_stats),
                "session_stats": dict(self.session_stats),
                "strategy_stats": dict(self.strategy_stats),
                "symbol_stats": dict(self.symbol_stats),
                "adaptive_params": self.adaptive_params,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }

            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            tmp.replace(path)

            logger.debug("learner_persisted", extra={"path": str(path)})
        except Exception as e:
            logger.warning("learner_persist_error", extra={"error": str(e)})

    def _load_persisted(self) -> None:
        """Load aggregated stats from disk."""
        try:
            path = DATA_DIR / "online_learner_stats.json"
            if not path.exists():
                return

            data = json.loads(path.read_text(encoding="utf-8"))
            self._trade_count = data.get("trade_count", 0)
            self.adaptive_params = data.get("adaptive_params", {})

            # Restore defaultdicts
            for key, val in data.get("hour_stats", {}).items():
                self.hour_stats[key] = val
            for key, val in data.get("session_stats", {}).items():
                self.session_stats[key] = val
            for key, val in data.get("strategy_stats", {}).items():
                self.strategy_stats[key] = val
            for key, val in data.get("symbol_stats", {}).items():
                self.symbol_stats[key] = val

            logger.info("learner_loaded", extra={
                "trade_count": self._trade_count,
                "symbols": list(self.symbol_stats.keys()),
            })
        except Exception as e:
            logger.warning("learner_load_error", extra={"error": str(e)})
