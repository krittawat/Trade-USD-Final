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
MIN_TRADES_FOR_RECOMMENDATION = 15

# Cap PF สูงสุดเมื่อไม่มี loss (ป้องกัน garbage PF เช่น 86.89)
MAX_PF_CAP = 10.0


class MemoryStore:
    """
    AI Memory — เก็บความรู้ที่เรียนรู้จากตลาด.

    ออกแบบเป็น compact storage:
        - ไม่เก็บ raw tick/candle (นั่นอยู่ใน QuestDB)
        - เก็บเฉพาะ aggregated features, statistics, performance tables
        - ค่าทั้งหมดอยู่ใน SQLite ไฟล์เดียว
    """

    def __init__(self, db_path: str | None = None) -> None:
        """
        กำหนด path ไฟล์ SQLite สำหรับ brain memory.

        Args:
            db_path: path ไปยังไฟล์ brain.db (default: backend/data/sqlite/brain.db)
        """
        if db_path is None:
            self.db_path = Path(__file__).resolve().parent.parent.parent / "data" / "sqlite" / "brain.db"
        else:
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
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
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
                symbol TEXT NOT NULL,          -- เช่น XAUUSDc, BTCUSDc
                regime TEXT NOT NULL,          -- TRENDING_UP, RANGING, HIGH_VOLATILITY, ...
                session TEXT,                  -- ASIA, LONDON, NY, OVERLAP
                timeframe TEXT,                -- M1, M5, M15, H1
                avg_volatility REAL,           -- EMA ของ ATR
                avg_range REAL,                -- EMA ของ price range
                trend_strength REAL,           -- EMA ของ ADX
                sample_count INTEGER DEFAULT 0, -- จำนวนตัวอย่างที่เก็บ
                updated_at TEXT DEFAULT ''     -- เวลาอัปเดตล่าสุด
            )
        """)
        
        # ─── Migration: Add updated_at if missing ───
        try:
            columns = [info[1] for info in self._conn.execute("PRAGMA table_info(regime_stats)").fetchall()]
            if "updated_at" not in columns:
                self._conn.execute("ALTER TABLE regime_stats ADD COLUMN updated_at TEXT DEFAULT ''")
                logger.info("brain_migration_regime_stats_added_column")
        except Exception as e:
            logger.warning("brain_migration_error", extra={"error": str(e)})
        
        # ─── ตาราง 11: backtest_history — เก็บผล backtest ───
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT,
                config_json TEXT,       -- Full config (SL, TP, Indicators)
                win_rate REAL,
                profit_factor REAL,
                total_trades INTEGER,
                total_pnl REAL,
                max_dd REAL,
                run_at TEXT
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

        # ─── ตาราง 8: sentiment_data — snapshot sentiment ล่าสุดต่อ symbol ───
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sentiment_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                fear_greed_value INTEGER DEFAULT 50,
                fear_greed_label TEXT DEFAULT 'Neutral',
                tv_rating TEXT DEFAULT 'NEUTRAL',
                tv_score REAL DEFAULT 0,
                social_score REAL DEFAULT 0,
                composite_score REAL DEFAULT 0,
                sources_available INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)

        # ─── ตาราง 9: discovered_patterns — ML-discovered patterns ───
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS discovered_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_id TEXT NOT NULL,
                cluster_center TEXT NOT NULL,
                label TEXT NOT NULL,
                total_examples INTEGER DEFAULT 0,
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
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sentiment_lookup
            ON sentiment_data(symbol)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_discovered_lookup
            ON discovered_patterns(pattern_id)
        """)

        # ─── ตาราง 10: personality_profiles — เก็บนิสัยตลาด (Behavioral DNA) ───
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS personality_profiles (
                symbol TEXT PRIMARY KEY,
                data TEXT NOT NULL,              -- JSON ของ PersonalityProfile
                updated_at TEXT NOT NULL
            )
        """)

        # ─── ตาราง 12: best_params — best params per symbol/regime (learning loop) ───
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS best_params (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                regime TEXT NOT NULL,
                strategy_name TEXT DEFAULT '',
                win_rate REAL DEFAULT 0,
                rr REAL DEFAULT 0,
                sl_atr REAL DEFAULT 1.5,
                tp_rr REAL DEFAULT 2.0,
                lot_multiplier REAL DEFAULT 1.0,
                total_trades INTEGER DEFAULT 0,
                max_dd REAL DEFAULT 0,
                expectancy REAL DEFAULT 0,
                updated_at TEXT NOT NULL,
                UNIQUE(symbol, regime)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_best_params_lookup
            ON best_params(symbol, regime)
        """)

        # ─── ตาราง 13: pattern_sequences — เรียนรู้ลำดับ pattern ──
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_sequences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                regime TEXT DEFAULT 'ALL',
                sequence TEXT NOT NULL,
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_profit REAL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_seq_lookup
            ON pattern_sequences(symbol, regime, sequence)
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
                  AND profit_factor >= 1.0
                  AND profit_factor <= ?
                  AND win_rate >= 0.40
                  AND total_losses > 0
                ORDER BY (profit_factor * win_rate) DESC
                LIMIT 1
            """, (symbol, regime, session, MIN_TRADES_FOR_RECOMMENDATION, MAX_PF_CAP)).fetchone()

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
                  AND profit_factor >= 1.0
                  AND profit_factor <= ?
                  AND win_rate >= 0.40
                  AND total_losses > 0
                ORDER BY (profit_factor * win_rate) DESC
                LIMIT 1
            """, (symbol, MIN_TRADES_FOR_RECOMMENDATION, MAX_PF_CAP)).fetchone()

            if row:
                logger.debug("brain_recommendation_fallback", extra={
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
            symbol: สัญลักษณ์ เช่น XAUUSDc
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
                pf = t_profit / t_loss if t_loss > 0 else min(MAX_PF_CAP, t_wins * 1.0)

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
                pf = min(MAX_PF_CAP, 1.0) if is_win else 0.0

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
            symbol: สัญลักษณ์ เช่น XAUUSDc
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

    def get_strategy_performance(
        self,
        strategy_name: str,
        symbol: str,
        regime: str = "UNKNOWN",
    ) -> dict | None:
        """ดึง performance ของ strategy เดียว."""
        if not self._conn:
            return None
        try:
            row = self._conn.execute("""
                SELECT strategy_name, symbol, regime, session,
                       win_rate, profit_factor, avg_rr, total_trades
                FROM strategy_performance
                WHERE strategy_name = ? AND symbol = ? AND regime = ?
                LIMIT 1
            """, (strategy_name, symbol, regime)).fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    # ────────────────────────────────────────────────────────────────
    # Shadow Win Rate — query from trading.db (cross-DB)
    # ────────────────────────────────────────────────────────────────

    _trading_db = None  # set by MasterLoop after init

    def set_trading_db(self, db) -> None:
        """Set reference to SQLiteStore for shadow data queries."""
        self._trading_db = db

    def get_shadow_win_rate(
        self, strategy_name: str, symbol: str
    ) -> float | None:
        """
        Get win_rate from shadow_scoreboard (lives in trading.db).

        Returns win_rate as percentage (0-100) or None if no data.
        """
        if not self._trading_db:
            return None
        try:
            rows = self._trading_db.get_shadow_scoreboard(symbol=symbol)
            for row in rows:
                if row.get("strategy_name") == strategy_name:
                    total = (row.get("wins", 0) or 0) + (row.get("losses", 0) or 0)
                    if total >= 5:
                        return row.get("win_rate", 0)
            return None
        except Exception:
            return None


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
            symbol: สัญลักษณ์ เช่น XAUUSDc
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
            return json.loads(row["params"]) if row else None
        except Exception:
            return None

    # ────────────────────────────────────────────────────────────────
    # Backtest History — เก็บผล backtest
    # ────────────────────────────────────────────────────────────────

    def save_backtest_run(
        self,
        strategy_name: str,
        symbol: str,
        timeframe: str,
        config: dict,
        result: dict,
    ) -> None:
        """
        บันทึกผลการ Backtest ลง SQLite.

        Args:
            strategy_name: ชื่อ strategy
            symbol: คู่เงิน
            timeframe: Timeframe ที่ใช้ test
            config: dict ของ parameters ที่ใช้ (SL, TP, etc.)
            result: dict ของผลลัพธ์ (win_rate, pf, pnl, dd, trades)
        """
        if not self._conn:
            return

        now = datetime.now(timezone.utc).isoformat()
        config_json = json.dumps(config)

        try:
            self._conn.execute("""
                INSERT INTO backtest_history
                (strategy_name, symbol, timeframe, config_json,
                 win_rate, profit_factor, total_trades,
                 total_pnl, max_dd, run_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                strategy_name,
                symbol,
                timeframe,
                config_json,
                result.get("win_rate", 0),
                result.get("profit_factor", 0),
                result.get("total_trades", 0),
                result.get("total_pnl", 0),
                result.get("max_dd", 0),
                now
            ))
            self._conn.commit()
            logger.info("backtest_result_saved", extra={
                "strategy": strategy_name,
                "symbol": symbol,
                "pf": result.get("profit_factor"),
                "pnl": result.get("total_pnl")
            })

        except Exception as e:
            logger.error("save_backtest_error", extra={"error": str(e)})

    def get_all_evolved_params(self) -> list[dict]:
        """
        Get all evolved params (for API dashboard).
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
                try:
                    results.append({
                        "strategy_name": r["strategy_name"],
                        "symbol": r["symbol"],
                        "regime": r["regime"],
                        "params": json.loads(r["params"]),
                        "score": r["score"],
                        "generation": r["generation"],
                        "updated_at": r["updated_at"]
                    })
                except Exception:
                    continue
            return results

        except Exception as e:
            logger.error("evolved_params_list_error", extra={"error": str(e)})
            return []

    # ────────────────────────────────────────────────────────────────
    # Best Params — per symbol/regime (Regime Intelligence Learning)
    # ────────────────────────────────────────────────────────────────

    def save_best_params(
        self,
        symbol: str,
        regime: str,
        params: dict,
    ) -> None:
        """
        บันทึก best params สำหรับ symbol × regime — upsert.

        Args:
            symbol: เช่น XAUUSDc
            regime: เช่น STRONG_TREND
            params: dict keys: strategy_name, win_rate, rr, sl_atr,
                    tp_rr, lot_multiplier, total_trades, max_dd, expectancy
        """
        if not self._conn:
            return

        now = datetime.now(timezone.utc).isoformat()

        try:
            existing = self._conn.execute(
                "SELECT id FROM best_params WHERE symbol = ? AND regime = ?",
                (symbol, regime),
            ).fetchone()

            if existing:
                self._conn.execute("""
                    UPDATE best_params
                    SET strategy_name = ?, win_rate = ?, rr = ?,
                        sl_atr = ?, tp_rr = ?, lot_multiplier = ?,
                        total_trades = ?, max_dd = ?, expectancy = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    params.get("strategy_name", ""),
                    params.get("win_rate", 0),
                    params.get("rr", 0),
                    params.get("sl_atr", 1.5),
                    params.get("tp_rr", 2.0),
                    params.get("lot_multiplier", 1.0),
                    params.get("total_trades", 0),
                    params.get("max_dd", 0),
                    params.get("expectancy", 0),
                    now,
                    existing["id"],
                ))
            else:
                self._conn.execute("""
                    INSERT INTO best_params
                    (symbol, regime, strategy_name, win_rate, rr,
                     sl_atr, tp_rr, lot_multiplier,
                     total_trades, max_dd, expectancy, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol, regime,
                    params.get("strategy_name", ""),
                    params.get("win_rate", 0),
                    params.get("rr", 0),
                    params.get("sl_atr", 1.5),
                    params.get("tp_rr", 2.0),
                    params.get("lot_multiplier", 1.0),
                    params.get("total_trades", 0),
                    params.get("max_dd", 0),
                    params.get("expectancy", 0),
                    now,
                ))

            self._conn.commit()
            logger.debug("best_params_saved", extra={
                "symbol": symbol, "regime": regime,
            })
        except Exception as e:
            logger.error("best_params_save_error", extra={"error": str(e)})

    def get_best_params(
        self,
        symbol: str,
        regime: str,
    ) -> dict | None:
        """
        ดึง best params สำหรับ symbol × regime.

        Returns:
            dict: {strategy_name, win_rate, rr, sl_atr, tp_rr,
                   lot_multiplier, total_trades, ...} or None
        """
        if not self._conn:
            return None

        try:
            row = self._conn.execute("""
                SELECT strategy_name, win_rate, rr, sl_atr, tp_rr,
                       lot_multiplier, total_trades, max_dd, expectancy
                FROM best_params
                WHERE symbol = ? AND regime = ?
                  AND total_trades >= ?
            """, (symbol, regime, MIN_TRADES_FOR_RECOMMENDATION)).fetchone()

            return dict(row) if row else None
        except Exception:
            return None

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

    # ────────────────────────────────────────────────────────────────
    # Pattern Sequence Learning — บันทึก/เรียกดูลำดับ patterns
    # ────────────────────────────────────────────────────────────────

    def record_pattern_sequence(
        self,
        symbol: str,
        sequence: str,
        is_win: bool,
        profit: float = 0.0,
        regime: str = "ALL",
    ) -> None:
        """
        บันทึกผลลัพธ์ของ pattern sequence (เช่น "doji→engulfing").

        Args:
            symbol: สัญลักษณ์
            sequence: ลำดับ patterns เช่น "doji→engulfing→breakout"
            is_win: ชนะหรือไม่
            profit: กำไร/ขาดทุน
            regime: สภาวะตลาด
        """
        if not self._conn or not sequence:
            return

        now = datetime.now(timezone.utc).isoformat()
        try:
            row = self._conn.execute("""
                SELECT id, total_trades, wins, avg_profit
                FROM pattern_sequences
                WHERE symbol = ? AND regime = ? AND sequence = ?
            """, (symbol, regime, sequence)).fetchone()

            if row:
                new_total = row["total_trades"] + 1
                new_wins = row["wins"] + (1 if is_win else 0)
                new_wr = new_wins / new_total if new_total > 0 else 0
                old_avg = row["avg_profit"] or 0
                new_avg = old_avg + (profit - old_avg) / new_total
                self._conn.execute("""
                    UPDATE pattern_sequences
                    SET total_trades = ?, wins = ?, win_rate = ?,
                        avg_profit = ?, updated_at = ?
                    WHERE id = ?
                """, (new_total, new_wins, round(new_wr, 4),
                      round(new_avg, 4), now, row["id"]))
            else:
                self._conn.execute("""
                    INSERT INTO pattern_sequences
                    (symbol, regime, sequence, total_trades, wins,
                     win_rate, avg_profit, updated_at)
                    VALUES (?, ?, ?, 1, ?, ?, ?, ?)
                """, (symbol, regime, sequence, 1 if is_win else 0,
                      1.0 if is_win else 0.0, round(profit, 4), now))

            self._conn.commit()
        except Exception as e:
            logger.error("seq_record_error", extra={"error": str(e)})

    def get_best_sequences(
        self,
        symbol: str,
        regime: str = "ALL",
        min_trades: int = 3,
        limit: int = 10,
    ) -> list[dict]:
        """
        ดึง pattern sequences ที่มี win_rate สูงสุด.

        Returns:
            [{sequence, win_rate, total_trades, avg_profit}]
        """
        if not self._conn:
            return []
        try:
            rows = self._conn.execute("""
                SELECT sequence, win_rate, total_trades, wins, avg_profit
                FROM pattern_sequences
                WHERE symbol = ? AND (regime = ? OR regime = 'ALL')
                  AND total_trades >= ?
                ORDER BY win_rate DESC
                LIMIT ?
            """, (symbol, regime, min_trades, limit)).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_seq_error", extra={"error": str(e)})
            return []

    # ────────────────────────────────────────────────────────────────
    # Per-Symbol Adaptive Pattern Weights
    # ────────────────────────────────────────────────────────────────

    def get_pattern_weights_for_symbol(
        self,
        symbol: str,
        regime: str = "ALL",
        min_trades: int = 5,
    ) -> dict[str, float]:
        """
        คืน per-pattern weight multiplier สำหรับ symbol นี้.

        - win_rate >= 70% → weight 1.5
        - win_rate >= 55% → weight 1.2
        - win_rate <= 40% → weight 0.5
        - win_rate <= 30% → weight 0.3
        - else → weight 1.0

        Returns:
            {"bullish_engulfing": 1.5, "doji": 0.5, ...}
        """
        if not self._conn:
            return {}
        try:
            rows = self._conn.execute("""
                SELECT pattern_name, win_rate, total_trades
                FROM pattern_performance
                WHERE symbol = ? AND (regime = ? OR regime = 'ALL')
                  AND total_trades >= ?
            """, (symbol, regime, min_trades)).fetchall()

            weights = {}
            for r in rows:
                wr = r["win_rate"]
                if wr >= 0.70:
                    weights[r["pattern_name"]] = 1.5
                elif wr >= 0.55:
                    weights[r["pattern_name"]] = 1.2
                elif wr <= 0.30:
                    weights[r["pattern_name"]] = 0.3
                elif wr <= 0.40:
                    weights[r["pattern_name"]] = 0.5
                else:
                    weights[r["pattern_name"]] = 1.0
            return weights
        except Exception as e:
            logger.error("get_pattern_weights_error", extra={"error": str(e)})
            return {}

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
    # Sentiment Data — CRUD
    # ────────────────────────────────────────────────────────────────

    def save_sentiment(self, symbol: str, sentiment) -> None:
        """บันทึก sentiment snapshot ล่าสุดสำหรับ symbol."""
        if not self._conn:
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            # Upsert: ลบเก่า → insert ใหม่ (เก็บแค่ล่าสุด)
            self._conn.execute(
                "DELETE FROM sentiment_data WHERE symbol = ?", (symbol,)
            )
            self._conn.execute("""
                INSERT INTO sentiment_data
                (symbol, fear_greed_value, fear_greed_label, tv_rating, tv_score,
                 social_score, composite_score, sources_available, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                symbol,
                getattr(sentiment, 'fear_greed_value', 50),
                getattr(sentiment, 'fear_greed_label', 'Neutral'),
                getattr(sentiment, 'tv_rating', 'NEUTRAL'),
                getattr(sentiment, 'tv_score', 0.0),
                getattr(sentiment, 'social_score', 0.0),
                getattr(sentiment, 'composite_score', 0.0),
                getattr(sentiment, 'sources_available', 0),
                now,
            ))
            self._conn.commit()
        except Exception as e:
            logger.error("save_sentiment_error", extra={"error": str(e)})

    def get_latest_sentiment(self, symbol: str | None = None) -> list[dict]:
        """ดึง sentiment ล่าสุด (ทุก symbol หรือ symbol เดียว)."""
        if not self._conn:
            return []
        try:
            if symbol:
                rows = self._conn.execute("""
                    SELECT symbol, fear_greed_value, fear_greed_label, tv_rating,
                           tv_score, social_score, composite_score,
                           sources_available, updated_at
                    FROM sentiment_data WHERE symbol = ?
                """, (symbol,)).fetchall()
            else:
                rows = self._conn.execute("""
                    SELECT symbol, fear_greed_value, fear_greed_label, tv_rating,
                           tv_score, social_score, composite_score,
                           sources_available, updated_at
                    FROM sentiment_data ORDER BY updated_at DESC
                """).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_sentiment_error", extra={"error": str(e)})
            return []

    # ────────────────────────────────────────────────────────────────
    # Discovered Patterns — CRUD
    # ────────────────────────────────────────────────────────────────

    def save_discovered_pattern(self, pattern) -> None:
        """บันทึก/อัปเดต discovered pattern."""
        if not self._conn:
            return
        try:
            import json
            now = datetime.now(timezone.utc).isoformat()
            centroid_json = json.dumps(pattern.centroid) if hasattr(pattern, 'centroid') else "[]"

            # Upsert by pattern_id
            existing = self._conn.execute(
                "SELECT id FROM discovered_patterns WHERE pattern_id = ?",
                (pattern.pattern_id,)
            ).fetchone()

            if existing:
                self._conn.execute("""
                    UPDATE discovered_patterns
                    SET label = ?, cluster_center = ?, total_examples = ?,
                        win_rate = ?, avg_r = ?, updated_at = ?
                    WHERE pattern_id = ?
                """, (
                    pattern.label, centroid_json, pattern.total_examples,
                    pattern.win_rate, pattern.avg_r, now, pattern.pattern_id,
                ))
            else:
                self._conn.execute("""
                    INSERT INTO discovered_patterns
                    (pattern_id, cluster_center, label, total_examples,
                     win_rate, avg_r, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    pattern.pattern_id, centroid_json, pattern.label,
                    pattern.total_examples, pattern.win_rate, pattern.avg_r, now,
                ))
            self._conn.commit()
        except Exception as e:
            logger.error("save_pattern_error", extra={"error": str(e)})

    def get_discovered_patterns(self, min_examples: int = 5) -> list[dict]:
        """ดึง discovered patterns ที่มี examples เพียงพอ."""
        if not self._conn:
            return []
        try:
            rows = self._conn.execute("""
                SELECT pattern_id, label, total_examples, win_rate, avg_r, updated_at
                FROM discovered_patterns
                WHERE total_examples >= ?
                ORDER BY win_rate DESC
                LIMIT 20
            """, (min_examples,)).fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("get_patterns_error", extra={"error": str(e)})
            return []


    # ────────────────────────────────────────────────────────────────
    # Personality Profiles — เก็บนิสัยตลาด
    # ────────────────────────────────────────────────────────────────

    def save_personality_profile(self, profile: "PersonalityProfile") -> None:
        """
        บันทึก PersonalityProfile ลง SQLite.
        """
        if not self._conn:
            return

        import dataclasses
        now = datetime.now(timezone.utc).isoformat()
        
        try:
            # Convert dataclass to dict
            data = dataclasses.asdict(profile)
            json_data = json.dumps(data)

            self._conn.execute("""
                INSERT INTO personality_profiles (symbol, data, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    data = excluded.data,
                    updated_at = excluded.updated_at
            """, (profile.symbol, json_data, now))
            
            self._conn.commit()
            logger.debug("personality_profile_saved", extra={"symbol": profile.symbol})
            
        except Exception as e:
            logger.error("save_personality_error", extra={"error": str(e)})

    def get_personality_profile(self, symbol: str) -> Optional[dict]:
        """
        ดึง PersonalityProfile (as dict) จาก SQLite.
        """
        if not self._conn:
            return None

        try:
            row = self._conn.execute("""
                SELECT data FROM personality_profiles WHERE symbol = ?
            """, (symbol,)).fetchone()

            if row:
                return json.loads(row["data"])
        except Exception as e:
            logger.error("get_personality_error", extra={"error": str(e)})
        
        return None

    # ────────────────────────────────────────────────────────────────
    # disconnect — ปิดการเชื่อมต่อ
    # ────────────────────────────────────────────────────────────────

    def disconnect(self) -> None:
        """ปิดการเชื่อมต่อ SQLite — ปล่อย resources."""
        if self._conn:
            self._conn.close()
            self._conn = None
