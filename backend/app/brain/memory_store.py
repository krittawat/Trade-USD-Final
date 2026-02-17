"""
Memory Store — ที่เก็บหน่วยความจำ AI Brain (SQLite-backed).

หน้าที่:
    - เก็บ learned features แบบ compact (ไม่เก็บ raw tick ใน RAM)
    - เก็บ regime statistics (ความผันผวน, trend strength, session behavior)
    - เก็บ strategy performance ต่อ symbol/timeframe/regime
    - เก็บ win/loss patterns สำหรับ AI recommendation
    - เก็บ evolved parameters จาก self-training (genetic algorithm)
    - เก็บ training session history สำหรับ dashboard

กฎ RAM (8GB mode):
    - ข้อมูลถูก summarize ก่อนเก็บ (feature aggregates, ไม่ใช่ raw data)
    - ใช้ SQLite — ไม่กิน RAM เยอะ, ทำงานเป็นไฟล์
    - Periodic cleanup เพื่อลบข้อมูลเก่าที่ไม่ใช้

ตาราง SQLite:
    regime_stats           — สถิติต่อสภาวะตลาด (volatility, range, trend)
    strategy_performance   — ผลงาน strategy ต่อ symbol/regime/session
    feature_aggregates     — ค่าเฉลี่ย indicator ต่อสภาวะ
    training_sessions      — ประวัติ training sessions (self-training)
    evolved_params         — parameters ที่ evolve แล้ว (best DNA)
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# จำนวน trades ขั้นต่ำก่อนแนะนำ strategy (ป้องกัน overfitting จากข้อมูลน้อย)
MIN_TRADES_FOR_RECOMMENDATION = 5


class MemoryStore:
    """
    AI Memory — เก็บความรู้ที่เรียนรู้จากตลาด.

    ออกแบบเป็น compact storage:
        - ไม่เก็บ raw tick/candle (นั่นอยู่ใน QuestDB)
        - เก็บเฉพาะ aggregated features, statistics, performance tables
        - ค่าทั้งหมดอยู่ใน SQLite ไฟล์เดียว
    """

    def __init__(self, db_path: str = "backend/data/sqlite/brain.db") -> None:
        """
        กำหนด path ไฟล์ SQLite สำหรับ brain memory.

        Args:
            db_path: path ไปยังไฟล์ brain.db (default: backend/data/sqlite/brain.db)
        """
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    # ────────────────────────────────────────────────────────────────
    # Connect / Disconnect — เปิด-ปิดการเชื่อมต่อ
    # ────────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """
        เปิดการเชื่อมต่อ SQLite — สร้าง directory + ตารางที่จำเป็น.

        ขั้นตอน:
            1. สร้าง directory ถ้ายังไม่มี
            2. เปิด connection (row_factory = sqlite3.Row เพื่อให้อ้างชื่อคอลัมน์ได้)
            3. สร้างตาราง (idempotent — CREATE IF NOT EXISTS)
        """
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row  # ให้ query result เป็น dict-like
        self._create_tables()
        logger.info("brain_memory_connected", extra={"path": str(self.db_path)})

    def _create_tables(self) -> None:
        """
        สร้างตาราง memory (idempotent — รันซ้ำได้โดยไม่ error).

        ตาราง:
            1. regime_stats          — สถิติสภาวะตลาดต่อ symbol/regime/session
            2. strategy_performance  — ผลงาน strategy (win_rate, PF, trades, ...)
            3. feature_aggregates    — ค่าเฉลี่ย indicator (mean, std, min, max)
        """
        assert self._conn is not None

        # ─── ตาราง 1: regime_stats — สถิติต่อสภาวะตลาด ───
        # เก็บ rolling averages ของ volatility, range, trend strength
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS regime_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,          -- เช่น XAUUSDm, BTCUSDm
                regime TEXT NOT NULL,          -- TRENDING_UP, RANGING, HIGH_VOLATILITY, ...
                session TEXT,                  -- ASIA, LONDON, NY, OVERLAP
                timeframe TEXT,                -- M1, M5, M15, H1
                avg_volatility REAL,           -- EMA ของ ATR
                avg_range REAL,                -- EMA ของ price range
                trend_strength REAL,           -- EMA ของ ADX
                sample_count INTEGER DEFAULT 0, -- จำนวนตัวอย่างที่เก็บ
                updated_at TEXT NOT NULL        -- เวลาอัปเดตล่าสุด (ISO)
            )
        """)

        # ─── ตาราง 2: strategy_performance — ผลงาน strategy ───
        # เก็บสถิติ win/loss ต่อ strategy + symbol + regime + session
        # ใช้สำหรับ Brain recommendation (query ตาม PF × WR)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,    -- ชื่อ strategy เช่น "gold_scalp_pro"
                symbol TEXT NOT NULL,           -- สัญลักษณ์ที่เทรด
                regime TEXT,                    -- สภาวะตลาดตอนเทรด
                session TEXT,                   -- session ตอนเทรด
                win_rate REAL DEFAULT 0,        -- อัตราชนะ (0.0 - 1.0)
                profit_factor REAL DEFAULT 0,   -- PF = total_profit / total_loss
                avg_rr REAL DEFAULT 0,          -- Risk:Reward เฉลี่ย
                total_trades INTEGER DEFAULT 0, -- จำนวนเทรดทั้งหมด
                total_wins INTEGER DEFAULT 0,   -- จำนวนเทรดที่ชนะ
                total_losses INTEGER DEFAULT 0, -- จำนวนเทรดที่แพ้
                total_profit REAL DEFAULT 0,    -- กำไรรวม (USD)
                total_loss REAL DEFAULT 0,      -- ขาดทุนรวม (USD, เก็บเป็นค่าบวก)
                updated_at TEXT NOT NULL         -- เวลาอัปเดตล่าสุด
            )
        """)

        # ─── ตาราง 3: feature_aggregates — ค่าเฉลี่ย indicator ───
        # เก็บสถิติ indicator (RSI, MACD, ฯลฯ) ต่อ symbol + regime
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS feature_aggregates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                feature_name TEXT NOT NULL,     -- ชื่อ indicator เช่น "rsi_14", "atr_14"
                regime TEXT,
                mean_value REAL,               -- ค่าเฉลี่ย
                std_value REAL,                -- ส่วนเบี่ยงเบนมาตรฐาน
                min_value REAL,                -- ค่าต่ำสุด
                max_value REAL,                -- ค่าสูงสุด
                sample_count INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)

        # ─── ตาราง 4: training_sessions — ประวัติ training sessions ───
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS training_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                duration_seconds REAL DEFAULT 0,
                symbols_trained TEXT,           -- JSON array
                strategies_tested INTEGER DEFAULT 0,
                best_performers TEXT,            -- JSON array of results
                params_evolved TEXT,             -- JSON dict
                improvements TEXT,               -- JSON dict
                created_at TEXT NOT NULL
            )
        """)

        # ─── ตาราง 5: evolved_params — parameters ที่ evolve แล้ว ───
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS evolved_params (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                regime TEXT DEFAULT 'ALL',
                params TEXT NOT NULL,            -- JSON dict
                score REAL DEFAULT 0,
                generation INTEGER DEFAULT 0,
                validated INTEGER DEFAULT 0,     -- 1 = passed validation
                updated_at TEXT NOT NULL
            )
        """)

        # ─── ตาราง 6: pattern_performance — ผลงาน per pattern × symbol ───
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                regime TEXT DEFAULT 'ALL',
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_r REAL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)

        # ─── ตาราง 7: news_performance — ผลงาน per news context × symbol ───
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS news_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                news_impact TEXT NOT NULL,       -- HIGH, MEDIUM, LOW, NONE
                symbol TEXT NOT NULL,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_r REAL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)

        # ─── Indexes สำหรับ query เร็ว ───
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_perf_lookup
            ON strategy_performance(symbol, regime, session)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_evolved_lookup
            ON evolved_params(strategy_name, symbol, regime)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pattern_lookup
            ON pattern_performance(pattern_name, symbol, regime)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_news_lookup
            ON news_performance(news_impact, symbol)
        """)

        self._conn.commit()

    # ────────────────────────────────────────────────────────────────
    # get_best_strategy — ถาม AI Brain ว่า strategy ไหนดีที่สุด
    # ────────────────────────────────────────────────────────────────

    def get_best_strategy(
        self,
        symbol: str,
        regime: str = "UNKNOWN",
        session: str = "CLOSED",
    ) -> Optional[str]:
        """
        ถาม AI Brain: strategy ไหนดีที่สุดสำหรับสภาวะนี้?

        คำถาม:
            "อะไรที่เคยใช้ได้ผลดีสำหรับ {symbol} ใน session {session}, regime {regime}?"

        วิธีให้คะแนน (Scoring):
            score = profit_factor × win_rate (ยิ่งสูง = ยิ่งดี)

        เงื่อนไข:
            - ต้องมี total_trades >= 5 (ป้องกัน overfitting จากข้อมูลน้อย)
            - ต้องมี profit_factor > 0 (strategy ต้องเคยกำไร)

        ลำดับ query:
            1. หา exact match: symbol + regime + session
            2. ถ้าไม่เจอ → fallback: symbol only (any regime/session)

        Returns:
            str — ชื่อ strategy ที่ score สูงสุด
            None — ถ้ายังไม่มีข้อมูลเพียงพอ
        """
        if not self._conn:
            return None

        try:
            # --- Query 1: exact match (symbol + regime + session) ---
            # จัดอันดับตาม PF × WR จากมากไปน้อย
            row = self._conn.execute("""
                SELECT strategy_name, profit_factor, win_rate, total_trades
                FROM strategy_performance
                WHERE symbol = ?
                  AND regime = ?
                  AND session = ?
                  AND total_trades >= ?
                  AND profit_factor > 0
                ORDER BY (profit_factor * win_rate) DESC
                LIMIT 1
            """, (symbol, regime, session, MIN_TRADES_FOR_RECOMMENDATION)).fetchone()

            if row:
                strategy = row["strategy_name"]
                logger.info("brain_recommendation_found", extra={
                    "symbol": symbol,
                    "strategy": strategy,
                    "pf": round(row["profit_factor"], 2),
                    "wr": round(row["win_rate"], 2),
                    "trades": row["total_trades"],
                })
                return strategy

            # --- Query 2: fallback — หาแค่ตาม symbol (ignore regime/session) ---
            row = self._conn.execute("""
                SELECT strategy_name, profit_factor, win_rate, total_trades
                FROM strategy_performance
                WHERE symbol = ?
                  AND total_trades >= ?
                  AND profit_factor > 0
                ORDER BY (profit_factor * win_rate) DESC
                LIMIT 1
            """, (symbol, MIN_TRADES_FOR_RECOMMENDATION)).fetchone()

            if row:
                logger.info("brain_recommendation_fallback", extra={
                    "symbol": symbol,
                    "strategy": row["strategy_name"],
                })
                return row["strategy_name"]

        except Exception as e:
            logger.error("brain_query_error", extra={"error": str(e)})

        # ไม่พบข้อมูลเพียงพอ
        logger.debug("brain_no_recommendation", extra={
            "symbol": symbol,
            "reason": "ข้อมูลไม่เพียงพอสำหรับการแนะนำ",
        })
        return None

    # ────────────────────────────────────────────────────────────────
    # record_trade_outcome — บันทึกผลเทรด → อัปเดต performance
    # ────────────────────────────────────────────────────────────────

    def record_trade_outcome(
        self,
        strategy_name: str,
        symbol: str,
        regime: str,
        session: str,
        profit_usd: float,
        risk_reward: float = 0.0,
    ) -> None:
        """
        บันทึกผลเทรดเข้า memory — อัปเดต strategy_performance.

        Upsert logic:
            - ถ้ามีแถวอยู่แล้ว (strategy+symbol+regime+session ซ้ำ) → อัปเดตสถิติ
            - ถ้าไม่มี → INSERT แถวใหม่

        การคำนวณ:
            - win_rate = total_wins / total_trades
            - profit_factor = total_profit / total_loss (0 ถ้าไม่เคยขาดทุน)
            - avg_rr = running average ของ risk:reward ratio

        Args:
            strategy_name: ชื่อ strategy ที่ใช้เทรด
            symbol: สัญลักษณ์ เช่น XAUUSDm
            regime: สภาวะตลาดตอนเทรด เช่น TRENDING_UP
            session: session ตอนเทรด เช่น NY
            profit_usd: กำไร/ขาดทุนเป็น USD (บวก = กำไร, ลบ = ขาดทุน)
            risk_reward: R:R ratio ของเทรดนี้
        """
        if not self._conn:
            return

        now = datetime.now(timezone.utc).isoformat()
        is_win = profit_usd > 0  # กำไร = ชนะ

        try:
            # --- ตรวจว่ามีแถวอยู่แล้วหรือไม่ ---
            existing = self._conn.execute("""
                SELECT id, total_trades, total_wins, total_losses,
                       total_profit, total_loss
                FROM strategy_performance
                WHERE strategy_name = ? AND symbol = ?
                  AND regime = ? AND session = ?
            """, (strategy_name, symbol, regime, session)).fetchone()

            if existing:
                # --- UPDATE: อัปเดตสถิติเดิม ---
                t_trades = existing["total_trades"] + 1
                t_wins = existing["total_wins"] + (1 if is_win else 0)
                t_losses = existing["total_losses"] + (0 if is_win else 1)
                t_profit = existing["total_profit"] + (profit_usd if is_win else 0)
                t_loss = existing["total_loss"] + (abs(profit_usd) if not is_win else 0)

                # คำนวณ win_rate และ profit_factor ใหม่
                win_rate = t_wins / t_trades if t_trades > 0 else 0
                pf = t_profit / t_loss if t_loss > 0 else (t_profit if t_profit > 0 else 0)

                self._conn.execute("""
                    UPDATE strategy_performance
                    SET total_trades = ?, total_wins = ?, total_losses = ?,
                        total_profit = ?, total_loss = ?,
                        win_rate = ?, profit_factor = ?,
                        avg_rr = (avg_rr * (total_trades - 1) + ?) / ?,
                        updated_at = ?
                    WHERE id = ?
                """, (t_trades, t_wins, t_losses, t_profit, t_loss,
                      win_rate, pf, risk_reward, t_trades, now,
                      existing["id"]))
            else:
                # --- INSERT: สร้างแถวใหม่ ---
                win_rate = 1.0 if is_win else 0.0
                pf = abs(profit_usd) if is_win else 0.0

                self._conn.execute("""
                    INSERT INTO strategy_performance
                    (strategy_name, symbol, regime, session,
                     win_rate, profit_factor, avg_rr,
                     total_trades, total_wins, total_losses,
                     total_profit, total_loss, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """, (strategy_name, symbol, regime, session,
                      win_rate, pf, risk_reward,
                      1 if is_win else 0,      # total_wins
                      0 if is_win else 1,      # total_losses
                      profit_usd if is_win else 0,     # total_profit
                      abs(profit_usd) if not is_win else 0,  # total_loss
                      now))

            self._conn.commit()
            logger.debug("brain_trade_recorded", extra={
                "strategy": strategy_name,
                "symbol": symbol,
                "profit": profit_usd,
                "is_win": is_win,
            })

        except Exception as e:
            logger.error("brain_record_error", extra={"error": str(e)}, exc_info=True)

    # ────────────────────────────────────────────────────────────────
    # update_regime_stats — อัปเดตสถิติ regime (EMA)
    # ────────────────────────────────────────────────────────────────

    def update_regime_stats(
        self,
        symbol: str,
        regime: str,
        session: str = "",
        volatility: float = 0.0,
        price_range: float = 0.0,
        trend_strength: float = 0.0,
    ) -> None:
        """
        อัปเดต regime statistics ด้วย Exponential Moving Average (EMA).

        ใช้ EMA เพื่อให้ค่า statistics ปรับตัวตามข้อมูลล่าสุด:
            alpha = min(2/(n+1), 0.1)  ← cap ที่ 0.1 เพื่อไม่ให้ค่าเปลี่ยนเร็วเกินไป
            new_value = old_value × (1 - alpha) + new_input × alpha

        Args:
            symbol: สัญลักษณ์ เช่น XAUUSDm
            regime: สภาวะตลาด เช่น TRENDING_UP
            session: session เช่น NY
            volatility: ค่า ATR ปัจจุบัน
            price_range: ช่วงราคา (high - low)
            trend_strength: ค่า ADX ปัจจุบัน
        """
        if not self._conn:
            return

        now = datetime.now(timezone.utc).isoformat()

        try:
            # ตรวจว่ามีแถวอยู่แล้วหรือไม่
            existing = self._conn.execute("""
                SELECT id, avg_volatility, avg_range, trend_strength, sample_count
                FROM regime_stats
                WHERE symbol = ? AND regime = ? AND session = ?
            """, (symbol, regime, session)).fetchone()

            if existing:
                # --- UPDATE ด้วย EMA ---
                n = existing["sample_count"] + 1
                alpha = min(2.0 / (n + 1), 0.1)  # cap alpha ที่ 0.1 เพื่อความเสถียร
                new_vol = existing["avg_volatility"] * (1 - alpha) + volatility * alpha
                new_range = existing["avg_range"] * (1 - alpha) + price_range * alpha
                new_trend = existing["trend_strength"] * (1 - alpha) + trend_strength * alpha

                self._conn.execute("""
                    UPDATE regime_stats
                    SET avg_volatility = ?, avg_range = ?, trend_strength = ?,
                        sample_count = ?, updated_at = ?
                    WHERE id = ?
                """, (new_vol, new_range, new_trend, n, now, existing["id"]))
            else:
                # --- INSERT แถวใหม่ (ค่าแรกใช้เลย) ---
                self._conn.execute("""
                    INSERT INTO regime_stats
                    (symbol, regime, session, avg_volatility, avg_range,
                     trend_strength, sample_count, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                """, (symbol, regime, session, volatility, price_range,
                      trend_strength, now))

            self._conn.commit()

        except Exception as e:
            logger.error("regime_stats_error", extra={"error": str(e)})

    # ────────────────────────────────────────────────────────────────
    # get_performance_summary — ดึงสรุป performance (สำหรับ dashboard)
    # ────────────────────────────────────────────────────────────────

    def get_performance_summary(self, symbol: str = "") -> list[dict]:
        """
        ดึงสรุป performance ทุก strategy สำหรับ dashboard.

        จัดเรียงตาม score (PF × WR) จากมากไปน้อย.

        Args:
            symbol: ถ้าระบุ → filter เฉพาะ symbol นั้น, ว่าง → ทั้งหมด

        Returns:
            list[dict] — [{strategy_name, symbol, regime, session,
                           win_rate, profit_factor, avg_rr, total_trades}, ...]
        """
        if not self._conn:
            return []

        try:
            if symbol:
                # filter เฉพาะ symbol ที่ระบุ
                rows = self._conn.execute("""
                    SELECT strategy_name, symbol, regime, session,
                           win_rate, profit_factor, avg_rr, total_trades
                    FROM strategy_performance
                    WHERE symbol = ?
                    ORDER BY (profit_factor * win_rate) DESC
                """, (symbol,)).fetchall()
            else:
                # ดึงทั้งหมด
                rows = self._conn.execute("""
                    SELECT strategy_name, symbol, regime, session,
                           win_rate, profit_factor, avg_rr, total_trades
                    FROM strategy_performance
                    ORDER BY (profit_factor * win_rate) DESC
                """).fetchall()

            return [dict(r) for r in rows]

        except Exception as e:
            logger.error("perf_summary_error", extra={"error": str(e)})
            return []

    # ────────────────────────────────────────────────────────────────
    # Feature Aggregates — สถิติ indicator ต่อ symbol + regime
    # ────────────────────────────────────────────────────────────────

    def update_feature_aggregate(
        self,
        symbol: str,
        regime: str,
        feature_name: str,
        value: float,
    ) -> None:
        """
        อัปเดตสถิติ indicator แบบ online (ไม่ต้องเก็บ raw data).

        ใช้ running formula:
            - count += 1
            - mean = old_mean + (value - old_mean) / count
            - std ≈ sqrt(((count-1)*old_std² + (value-old_mean)*(value-new_mean)) / count)
            - min = min(old_min, value)
            - max = max(old_max, value)

        Args:
            symbol: สัญลักษณ์ เช่น XAUUSDm
            regime: สภาวะตลาด เช่น TRENDING_UP
            feature_name: ชื่อ indicator เช่น "rsi_14", "atr_14"
            value: ค่า indicator ปัจจุบัน
        """
        if not self._conn:
            return

        import math
        now = datetime.now(timezone.utc).isoformat()

        try:
            row = self._conn.execute("""
                SELECT id, mean_value, std_value, min_value, max_value, sample_count
                FROM feature_aggregates
                WHERE symbol = ? AND feature_name = ? AND regime = ?
            """, (symbol, feature_name, regime)).fetchone()

            if row:
                # --- Update: running statistics ---
                old_count = row["sample_count"] or 0
                old_mean = row["mean_value"] or 0.0
                old_std = row["std_value"] or 0.0
                old_min = row["min_value"] if row["min_value"] is not None else value
                old_max = row["max_value"] if row["max_value"] is not None else value

                new_count = old_count + 1
                # Running mean
                new_mean = old_mean + (value - old_mean) / new_count
                # Running std (Welford's simplified)
                if new_count > 1:
                    old_var = old_std ** 2
                    new_var = ((new_count - 1) * old_var + (value - old_mean) * (value - new_mean)) / new_count
                    new_std = math.sqrt(max(0, new_var))
                else:
                    new_std = 0.0
                new_min = min(old_min, value)
                new_max = max(old_max, value)

                self._conn.execute("""
                    UPDATE feature_aggregates
                    SET mean_value = ?, std_value = ?, min_value = ?, max_value = ?,
                        sample_count = ?, updated_at = ?
                    WHERE id = ?
                """, (new_mean, new_std, new_min, new_max, new_count, now, row["id"]))
            else:
                # --- Insert: ค่าแรก ---
                self._conn.execute("""
                    INSERT INTO feature_aggregates
                    (symbol, feature_name, regime, mean_value, std_value,
                     min_value, max_value, sample_count, updated_at)
                    VALUES (?, ?, ?, ?, 0, ?, ?, 1, ?)
                """, (symbol, feature_name, regime, value, value, value, now))

            self._conn.commit()

        except Exception as e:
            logger.error("feature_aggregate_update_error", extra={
                "symbol": symbol,
                "feature": feature_name,
                "error": str(e),
            })

    def get_feature_aggregates(self, symbol: str, regime: str = "UNKNOWN") -> dict:
        """
        ดึงสถิติ feature aggregates ทั้งหมดสำหรับ symbol + regime.

        Returns:
            dict: {
                "rsi_14": {"mean": 55.3, "std": 12.1, "min": 20.0, "max": 85.0, "count": 500},
                "atr_14": {"mean": 1.8, "std": 0.5, "min": 0.5, "max": 4.2, "count": 500},
            }
        """
        if not self._conn:
            return {}

        try:
            rows = self._conn.execute("""
                SELECT feature_name, mean_value, std_value, min_value, max_value, sample_count
                FROM feature_aggregates
                WHERE symbol = ? AND regime = ?
            """, (symbol, regime)).fetchall()

            return {
                row["feature_name"]: {
                    "mean": row["mean_value"],
                    "std": row["std_value"],
                    "min": row["min_value"],
                    "max": row["max_value"],
                    "count": row["sample_count"],
                }
                for row in rows
            }
        except Exception as e:
            logger.error("feature_aggregate_get_error", extra={
                "symbol": symbol,
                "error": str(e),
            })
            return {}

    # ────────────────────────────────────────────────────────────────
    # Self-Training: save/get evolved params + training sessions
    # ────────────────────────────────────────────────────────────────

    def save_evolved_params(
        self,
        strategy_name: str,
        symbol: str,
        regime: str = "ALL",
        params: dict | None = None,
        score: float = 0.0,
    ) -> None:
        """
        บันทึก evolved parameters (best DNA) — upsert.

        ถ้ามี row เดิม (strategy+symbol+regime) → อัปเดตถ้า score สูงกว่า.
        ถ้าไม่มี → INSERT ใหม่.
        """
        if not self._conn or params is None:
            return

        now = datetime.now(timezone.utc).isoformat()
        params_json = json.dumps(params)

        try:
            existing = self._conn.execute("""
                SELECT id, score FROM evolved_params
                WHERE strategy_name = ? AND symbol = ? AND regime = ?
            """, (strategy_name, symbol, regime)).fetchone()

            if existing:
                # อัปเดตถ้า score ใหม่ดีกว่า
                if score > (existing["score"] or 0):
                    self._conn.execute("""
                        UPDATE evolved_params
                        SET params = ?, score = ?, validated = 1,
                            generation = generation + 1, updated_at = ?
                        WHERE id = ?
                    """, (params_json, score, now, existing["id"]))
            else:
                self._conn.execute("""
                    INSERT INTO evolved_params
                    (strategy_name, symbol, regime, params, score,
                     generation, validated, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, 1, ?)
                """, (strategy_name, symbol, regime, params_json, score, now))

            self._conn.commit()
            logger.debug("evolved_params_saved", extra={
                "strategy": strategy_name, "symbol": symbol, "score": score,
            })
        except Exception as e:
            logger.error("evolved_params_save_error", extra={"error": str(e)})

    def get_evolved_params(
        self,
        strategy_name: str,
        symbol: str,
        regime: str = "ALL",
    ) -> dict | None:
        """
        ดึง evolved parameters สำหรับ strategy + symbol.

        Returns:
            dict: parameters ที่ดีที่สุด หรือ None ถ้ายังไม่มี
        """
        if not self._conn:
            return None

        try:
            row = self._conn.execute("""
                SELECT params, score FROM evolved_params
                WHERE strategy_name = ? AND symbol = ? AND regime = ?
                  AND validated = 1
                ORDER BY score DESC
                LIMIT 1
            """, (strategy_name, symbol, regime)).fetchone()

            if row and row["params"]:
                return json.loads(row["params"])
        except Exception as e:
            logger.error("evolved_params_get_error", extra={"error": str(e)})

        return None

    def get_all_evolved_params(self) -> list[dict]:
        """
        ดึง evolved params ทั้งหมด (สำหรับ API dashboard).
        """
        if not self._conn:
            return []
        try:
            rows = self._conn.execute("""
                SELECT strategy_name, symbol, regime, params,
                       score, generation, updated_at
                FROM evolved_params
                WHERE validated = 1
                ORDER BY score DESC
            """).fetchall()
            results = []
            for r in rows:
                entry = dict(r)
                try:
                    entry["params"] = json.loads(entry["params"])
                except Exception:
                    pass
                results.append(entry)
            return results
        except Exception as e:
            logger.error("get_all_evolved_error", extra={"error": str(e)})
            return []

    def save_training_session(self, report) -> None:
        """
        บันทึก training session report ลง SQLite.

        Args:
            report: TrainingReport instance
        """
        if not self._conn:
            return

        now = datetime.now(timezone.utc).isoformat()

        try:
            # Serialize complex fields to JSON
            best_json = json.dumps(
                [r.model_dump() for r in report.best_performers]
                if report.best_performers else [],
                default=str,
            )

            self._conn.execute("""
                INSERT INTO training_sessions
                (session_id, started_at, completed_at, duration_seconds,
                 symbols_trained, strategies_tested, best_performers,
                 params_evolved, improvements, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                report.session_id,
                report.started_at.isoformat(),
                report.completed_at.isoformat(),
                report.duration_seconds,
                json.dumps(report.symbols_trained),
                report.strategies_tested,
                best_json,
                json.dumps(report.params_evolved, default=str),
                json.dumps(report.improvements, default=str),
                now,
            ))
            self._conn.commit()
            logger.debug("training_session_saved", extra={
                "session_id": report.session_id,
            })
        except Exception as e:
            logger.error("training_session_save_error", extra={"error": str(e)})

    def get_training_history(self, limit: int = 10) -> list[dict]:
        """
        ดึงประวัติ training sessions ล่าสุด.

        Returns:
            list[dict]: [{session_id, started_at, duration, strategies, ...}]
        """
        if not self._conn:
            return []

        try:
            rows = self._conn.execute("""
                SELECT session_id, started_at, completed_at, duration_seconds,
                       symbols_trained, strategies_tested,
                       params_evolved, improvements
                FROM training_sessions
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()

            results = []
            for r in rows:
                entry = dict(r)
                # Parse JSON fields
                for field in ["symbols_trained", "params_evolved", "improvements"]:
                    try:
                        entry[field] = json.loads(entry[field]) if entry[field] else {}
                    except Exception:
                        pass
                results.append(entry)
            return results
        except Exception as e:
            logger.error("training_history_error", extra={"error": str(e)})
            return []

    # ────────────────────────────────────────────────────────────────
    # Pattern + News Performance tracking
    # ────────────────────────────────────────────────────────────────

    def record_pattern_outcomes(
        self,
        symbol: str,
        pattern_win_rates: dict,
        patterns_found: dict,
        regime: str = "ALL",
    ) -> None:
        """
        บันทึกผลลัพธ์ per-pattern จาก PracticeEngine (batch-optimized).

        Args:
            symbol: สัญลักษณ์
            pattern_win_rates: {pattern_name: win_rate}
            patterns_found: {pattern_name: count}
            regime: สภาวะตลาด
        """
        if not self._conn or not patterns_found:
            return

        now = datetime.now(timezone.utc).isoformat()
        try:
            # Batch fetch existing records
            pattern_names = list(patterns_found.keys())
            placeholders = ",".join("?" * len(pattern_names))
            existing_rows = self._conn.execute(f"""
                SELECT id, pattern_name, total_trades, wins, losses
                FROM pattern_performance
                WHERE symbol = ? AND regime = ?
                  AND pattern_name IN ({placeholders})
            """, [symbol, regime] + pattern_names).fetchall()

            existing_map = {r["pattern_name"]: dict(r) for r in existing_rows}

            update_params = []
            insert_params = []

            for p_name, count in patterns_found.items():
                wr = pattern_win_rates.get(p_name, 0)
                wins = int(count * wr)
                losses = count - wins

                if p_name in existing_map:
                    ex = existing_map[p_name]
                    new_total = ex["total_trades"] + count
                    new_wins = ex["wins"] + wins
                    new_losses = ex["losses"] + losses
                    new_wr = new_wins / new_total if new_total > 0 else 0
                    update_params.append((
                        new_total, new_wins, new_losses,
                        round(new_wr, 4), now, ex["id"],
                    ))
                else:
                    insert_params.append((
                        p_name, symbol, regime, count, wins,
                        losses, round(wr, 4), now,
                    ))

            if update_params:
                self._conn.executemany("""
                    UPDATE pattern_performance
                    SET total_trades = ?, wins = ?, losses = ?,
                        win_rate = ?, updated_at = ?
                    WHERE id = ?
                """, update_params)

            if insert_params:
                self._conn.executemany("""
                    INSERT INTO pattern_performance
                    (pattern_name, symbol, regime, total_trades, wins, losses,
                     win_rate, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, insert_params)

            self._conn.commit()
        except Exception as e:
            logger.error("pattern_record_error", extra={"error": str(e)})

    def get_best_patterns(
        self,
        symbol: str,
        regime: str = "ALL",
        min_trades: int = 5,
        limit: int = 10,
    ) -> list[dict]:
        """
        ดึง patterns ที่มี win_rate สูงสุด.

        Returns:
            [{pattern_name, win_rate, total_trades, ...}]
        """
        if not self._conn:
            return []
        try:
            rows = self._conn.execute("""
                SELECT pattern_name, win_rate, total_trades, wins, losses
                FROM pattern_performance
                WHERE symbol = ? AND (regime = ? OR regime = 'ALL')
                  AND total_trades >= ?
                ORDER BY win_rate DESC
                LIMIT ?
            """, (symbol, regime, min_trades, limit)).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_patterns_error", extra={"error": str(e)})
            return []

    def get_all_pattern_performance(self) -> list[dict]:
        """ดึง pattern performance ทั้งหมด (สำหรับ API dashboard)."""
        if not self._conn:
            return []
        try:
            rows = self._conn.execute("""
                SELECT pattern_name, symbol, regime, win_rate,
                       total_trades, wins, losses, updated_at
                FROM pattern_performance
                WHERE total_trades >= 3
                ORDER BY win_rate DESC
            """).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_all_patterns_error", extra={"error": str(e)})
            return []

    def record_news_outcomes(
        self,
        symbol: str,
        news_stats: dict,
    ) -> None:
        """
        บันทึกผลลัพธ์ per-news-context จาก PracticeEngine (batch-optimized).

        Args:
            symbol: สัญลักษณ์
            news_stats: {impact: {count, win_rate}}
        """
        if not self._conn or not news_stats:
            return

        now = datetime.now(timezone.utc).isoformat()
        try:
            # Batch fetch existing records
            impacts = list(news_stats.keys())
            placeholders = ",".join("?" * len(impacts))
            existing_rows = self._conn.execute(f"""
                SELECT id, news_impact, total_trades, wins, losses
                FROM news_performance
                WHERE symbol = ? AND news_impact IN ({placeholders})
            """, [symbol] + impacts).fetchall()

            existing_map = {r["news_impact"]: dict(r) for r in existing_rows}

            update_params = []
            insert_params = []

            for impact, stats in news_stats.items():
                count = stats.get("count", 0)
                wr = stats.get("win_rate", 0)
                wins = int(count * wr)
                losses = count - wins

                if impact in existing_map:
                    ex = existing_map[impact]
                    new_total = ex["total_trades"] + count
                    new_wins = ex["wins"] + wins
                    new_losses = ex["losses"] + losses
                    new_wr = new_wins / new_total if new_total > 0 else 0
                    update_params.append((
                        new_total, new_wins, new_losses,
                        round(new_wr, 4), now, ex["id"],
                    ))
                else:
                    insert_params.append((
                        impact, symbol, count, wins,
                        losses, round(wr, 4), now,
                    ))

            if update_params:
                self._conn.executemany("""
                    UPDATE news_performance
                    SET total_trades = ?, wins = ?, losses = ?,
                        win_rate = ?, updated_at = ?
                    WHERE id = ?
                """, update_params)

            if insert_params:
                self._conn.executemany("""
                    INSERT INTO news_performance
                    (news_impact, symbol, total_trades, wins, losses,
                     win_rate, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, insert_params)

            self._conn.commit()
        except Exception as e:
            logger.error("news_record_error", extra={"error": str(e)})

    def get_news_performance(self, symbol: str | None = None) -> list[dict]:
        """
        ดึง news performance ทั้งหมด.

        Returns:
            [{news_impact, symbol, win_rate, total_trades, ...}]
        """
        if not self._conn:
            return []
        try:
            if symbol:
                rows = self._conn.execute("""
                    SELECT news_impact, symbol, win_rate, total_trades, wins, losses
                    FROM news_performance
                    WHERE symbol = ?
                    ORDER BY total_trades DESC
                """, (symbol,)).fetchall()
            else:
                rows = self._conn.execute("""
                    SELECT news_impact, symbol, win_rate, total_trades, wins, losses
                    FROM news_performance
                    ORDER BY total_trades DESC
                """).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_news_perf_error", extra={"error": str(e)})
            return []

    # ────────────────────────────────────────────────────────────────
    # disconnect — ปิดการเชื่อมต่อ
    # ────────────────────────────────────────────────────────────────

    def disconnect(self) -> None:
        """ปิดการเชื่อมต่อ SQLite — ปล่อย resources."""
        if self._conn:
            self._conn.close()
            self._conn = None
