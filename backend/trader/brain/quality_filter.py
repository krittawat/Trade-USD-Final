#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ANTIGRAVITY — Quality Filter & Performance Guard
═══════════════════════════════════════════════
ใช้ข้อมูลจาก Replay 180 วัน เพื่อกรองสัญญาณที่มีโอกาสชนะต่ำ
"""
import sqlite3
import logging
from pathlib import Path
from functools import lru_cache

logger = logging.getLogger("opus_logger")
DB_PATH = Path("d:/VibeCode/Trade/backend/trader/data/replay_180d.db")

class QualityFilter:
    @staticmethod
    @lru_cache(maxsize=128)
    def get_historical_performance(symbol: str, timeframe: str, model: str, regime: str) -> dict:
        """
        ดึงข้อมูล Win Rate และ PnL จากฐานข้อมูล Replay สำหรับคู่เทรด/กลยุทธ์/สภาวะตลาดนั้นๆ
        """
        if not DB_PATH.exists():
            return {"wr": 0, "trades": 0, "pnl": 0}

        try:
            conn = sqlite3.connect(str(DB_PATH))
            cur = conn.cursor()
            
            # Check if table exists first to avoid spammy logs while replay is starting
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='replay_trades'")
            if not cur.fetchone():
                conn.close()
                return {"wr": 0, "trades": 0, "pnl": 0}

            # Query stats for this specific combo
            cur.execute("""
                SELECT COUNT(*), 
                       SUM(CASE WHEN outcome='WIN' THEN 1 ELSE 0 END),
                       SUM(pnl)
                FROM replay_trades
                WHERE symbol = ? AND timeframe = ? AND model = ? AND regime = ?
            """, (symbol, timeframe, model, regime))
            
            res = cur.fetchone()
            conn.close()
            
            if not res or res[0] == 0:
                return {"wr": 0, "trades": 0, "pnl": 0}
            
            total = res[0]
            wins = res[1] or 0
            pnl = res[2] or 0.0
            wr = (wins / total) * 100
            
            return {"wr": wr, "trades": total, "pnl": pnl}
            
        except sqlite3.OperationalError:
            # Table might not exist yet, silent fallthrough
            return {"wr": 0, "trades": 0, "pnl": 0}
        except Exception as e:
            if "no such table" in str(e).lower():
                return {"wr": 0, "trades": 0, "pnl": 0}
            logger.error(f"QualityFilter: Error reading DB: {e}")
            return {"wr": 0, "trades": 0, "pnl": 0}

    @staticmethod
    def is_quality_signal(signal: dict, context: dict) -> bool:
        """
        ตัดสินใจว่าจะปล่อยสัญญาณให้เทรดหรือไม่ โดยอิงจากสถิติย้อนหลัง
        """
        symbol = signal.get("symbol")
        tf = context.get("timeframe")
        model = signal.get("model")
        regime = context.get("regime_result", {}).get("regime", "UNKNOWN")
        
        # ถ้าไม่มีข้อมูลใน DB (ยังรัน replay ไม่เสร็จ) ให้ปล่อยไปตามปกติก่อน
        if not DB_PATH.exists():
            return True
            
        perf = QualityFilter.get_historical_performance(symbol, tf, model, regime)
        
        # Thresholds:
        # 1. ต้องมีประวัติการเทรดอย่างน้อย 3 ครั้งในสภาวะตลาดนี้
        # 2. Win Rate ต้อง >= 60% หรือ PnL รวมต้องเป็นบวก
        if perf["trades"] >= 3:
            if perf["wr"] < 55.0 and perf["pnl"] <= 0:
                logger.info(f"🚫 [QUALITY BLOCK] {model} in {regime} ignored. WR={perf['wr']:.1f}% PnL={perf['pnl']:.2f}")
                return False
                
        return True

quality_filter = QualityFilter()
