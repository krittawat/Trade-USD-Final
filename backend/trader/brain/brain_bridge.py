# -*- coding: utf-8 -*-
"""
Brain Bridge — Dynamic Parameter Loader (trader -> brain.db)
=========================================================
PURPOSE:
    Connects the high-performance Opus Engine (trader) to the AI Memory (brain.db).
    Allows strategies to load "evolved" parameters based on the current market regime.

LOGIC:
    1. Connects to brain.db (read-only SQLite).
    2. Caches parameters to minimize I/O.
    3. Provides parameters to the Strategy Selector.
"""

import json
import sqlite3
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger("opus_logger")

class BrainBridge:
    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Default institutional path: backend/data/sqlite/brain.db
            self.db_path = Path(__file__).resolve().parent.parent.parent / "data" / "sqlite" / "brain.db"
        else:
            self.db_path = Path(db_path)
            
        self._conn: Optional[sqlite3.Connection] = None
        self._cache: Dict[str, Dict[str, Any]] = {}  # Cache by symbol:regime:strategy

    def connect(self) -> bool:
        """Connect to brain.db."""
        if not self.db_path.exists():
            logger.warning(f"BRAIN BRIDGE: brain.db not found at {self.db_path}. AI Memory disabled.")
            return False
            
        try:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            logger.info(f"BRAIN BRIDGE: Connected to AI Memory at {self.db_path.name}")
            return True
        except Exception as e:
            logger.error(f"BRAIN BRIDGE: Connection failed: {e}")
            return False

    def get_evolved_params(self, symbol: str, regime: str, strategy_name: str) -> Optional[Dict[str, Any]]:
        """
        Fetch optimized parameters for a specific strategy/symbol/regime.
        
        Args:
            symbol: e.g. 'XAUUSD'
            regime: e.g. 'RANGING', 'TRENDING_UP'
            strategy_name: e.g. 'gold_elite', 'gold_scalp_pro'
            
        Returns:
            Dict of parameters or None if not found.
        """
        if not self._conn:
            return None
            
        # Standardize naming (some scripts use XAUUSDc, others XAUUSD)
        clean_symbol = symbol.replace("c", "").replace("m", "")
        clean_strategy = strategy_name.lower().replace(" ", "_")
        
        cache_key = f"{clean_symbol}:{regime}:{clean_strategy}"
        if cache_key in self._cache:
            return self._cache[cache_key]
            
        try:
            cursor = self._conn.cursor()
            # Fetch the one with highest score
            query = """
                SELECT params, score FROM evolved_params 
                WHERE symbol LIKE ? AND regime = ? AND strategy_name = ?
                ORDER BY score DESC LIMIT 1
            """
            cursor.execute(query, (f"%{clean_symbol}%", regime, clean_strategy))
            row = cursor.fetchone()
            
            if row:
                params = json.loads(row['params'])
                # Remove metadata keys that might start with underscore
                clean_params = {k: v for k, v in params.items() if not k.startswith("_")}
                self._cache[cache_key] = clean_params
                logger.debug(f"BRAIN BRIDGE: Loaded {clean_strategy} params for {clean_symbol} ({regime})")
                return clean_params
            
            # Fallback to 'ALL' regime if specific regime not found
            if regime != "ALL":
                cursor.execute(query, (f"%{clean_symbol}%", "ALL", clean_strategy))
                row = cursor.fetchone()
                if row:
                    params = json.loads(row['params'])
                    clean_params = {k: v for k, v in params.items() if not k.startswith("_")}
                    self._cache[cache_key] = clean_params
                    return clean_params

        except Exception as e:
            logger.error(f"BRAIN BRIDGE: Query error: {e}")
            
        return None

# Singleton instance
brain_bridge = BrainBridge()
