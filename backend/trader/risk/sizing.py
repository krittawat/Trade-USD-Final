# -*- coding: utf-8 -*-
"""
Institutional Sizing Engine — Fractional Kelly Criterion
========================================================
Purpose:
    Calculates dynamic lot sizes based on institutional-grade risk.
    Uses "Half-Kelly" to ensure account survival during losing streaks.
    Queries brain.db for historical strategy performance.
"""

import logging
import sqlite3
import json
from pathlib import Path
from typing import Dict, Any

from backend.trader.config.paths import BRAIN_DB_PATH

logger = logging.getLogger("opus_logger")

DB_PATH = Path(BRAIN_DB_PATH)

class FractionalKellySizer:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._conn = None
        self.default_risk = 1.0  # 1.0% base risk
        self.min_risk = 0.5     # 0.5% floor
        self.max_risk = 2.0     # 2.0% ceiling (Institutional Hard Cap)
        
    def _connect(self):
        if not self.db_path.exists():
            return None
        try:
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            return conn
        except:
            return None

    def get_dynamic_risk(self, strategy_name: str, symbol: str, regime: str = "ALL", fomo_penalty: float = 1.0) -> float:
        """
        Computes Fractional Kelly risk % for a specific strategy/symbol.
        fomo_penalty: multiplier (0.0 - 1.0) from Pullback Gate.
        """
        clean_strategy = strategy_name.lower().replace(" ", "_").replace("_institutional", "")
        clean_symbol = symbol.replace("m", "").replace("c", "")
        
        try:
            conn = self._connect()
            if not conn:
                return round(self.default_risk * fomo_penalty, 2)
            
            cursor = conn.cursor()
            query = """
                SELECT win_rate, profit_factor 
                FROM strategy_performance 
                WHERE strategy_name = ? AND symbol LIKE ? AND regime = ?
                LIMIT 1
            """
            cursor.execute(query, (clean_strategy, f"%{clean_symbol}%", regime))
            row = cursor.fetchone()
            
            if not row and regime != "ALL":
                cursor.execute(query, (clean_strategy, f"%{clean_symbol}%", "ALL"))
                row = cursor.fetchone()
            
            conn.close()
            
            risk_pct = self.default_risk
            if row:
                wr = row['win_rate'] / 100.0 if row['win_rate'] > 1.0 else row['win_rate']
                pf = row['profit_factor']
                
                if wr > 0 and pf > 0:
                    kelly = (wr * (pf + 1) - 1) / pf
                    risk_pct = kelly * 100.0 * 0.5 
            
            # Apply FOMO Penalty
            final_risk = risk_pct * fomo_penalty
            
            # Clamp to institutional limits
            clamped_risk = max(self.min_risk, min(self.max_risk, final_risk))
            
            if fomo_penalty < 1.0:
                logger.info(f"🛡️ [FOMO RISK] Price at extreme -> Risk reduced by {int((1-fomo_penalty)*100)}% ({clamped_risk:.2f}%)")
            
            return round(clamped_risk, 2)
            
        except Exception as e:
            logger.error(f"SIZER: Calculation error: {e}")
            
        return round(self.default_risk * fomo_penalty, 2)

# Singleton
sizer = FractionalKellySizer()
