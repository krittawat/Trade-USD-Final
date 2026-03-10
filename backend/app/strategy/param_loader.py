"""
Strategy Param Loader — Load strategy parameters from SQLite DB.

No more hardcoding! All strategy parameters are stored in the
`strategy_params` table and loaded at strategy init time.

Usage:
    loader = StrategyParamLoader(sqlite_store)
    params = loader.get_params("gold_elite", "XAUUSDc", DEFAULTS)
    # params = DB values merged over DEFAULTS

Flow:
    1. Strategy defines DEFAULTS dict (code-level safe fallback)
    2. At __init__, call loader.get_params(name, symbol, DEFAULTS)
    3. DB values override DEFAULTS (if present)
    4. Strategy uses self.p["key"] everywhere instead of module constants
"""

import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Module-level singleton (set by master_loop at startup)
_instance: "StrategyParamLoader | None" = None


def get_param_loader() -> "StrategyParamLoader | None":
    """Get the global param loader instance."""
    return _instance


def set_param_loader(loader: "StrategyParamLoader") -> None:
    """Set the global param loader instance (called once at startup)."""
    global _instance
    _instance = loader
    logger.info("param_loader_initialized")


class StrategyParamLoader:
    """
    Loads strategy parameters from SQLite strategy_params table.

    Thread-safe: reads are from a cached dict, writes go through SQLite.
    """

    def __init__(self, sqlite_store) -> None:
        self._store = sqlite_store
        self._cache: dict[str, dict] = {}  # key = "strategy:symbol"
        self._load_all()

    def _load_all(self) -> None:
        """Load all active strategy params from DB into cache."""
        try:
            rows = self._store.get_all_strategy_params()
            for row in rows:
                key = f"{row['strategy_name']}:{row['symbol']}"
                self._cache[key] = row.get("params", {})
            logger.info("param_loader_loaded", extra={
                "count": len(self._cache),
                "keys": list(self._cache.keys())[:10],
            })
        except Exception as e:
            logger.warning("param_loader_load_failed", extra={"error": str(e)})

    def get_params(
        self,
        strategy_name: str,
        symbol: str,
        defaults: dict,
    ) -> dict:
        """
        Get params for a strategy+symbol combo.

        Priority: DB values > defaults (code fallback)
        """
        key = f"{strategy_name}:{symbol}"
        db_params = self._cache.get(key, {})

        # Also try without symbol suffix (e.g. "XAUUSDc" -> "XAUUSD")
        if not db_params:
            clean_symbol = symbol.rstrip("cmCM.")
            alt_key = f"{strategy_name}:{clean_symbol}"
            db_params = self._cache.get(alt_key, {})

        # Also try strategy-only key (global params)
        if not db_params:
            global_key = f"{strategy_name}:*"
            db_params = self._cache.get(global_key, {})

        # Merge: defaults ← DB overrides
        merged = {**defaults}
        for k, v in db_params.items():
            if k in merged:
                # Type-cast DB value to match default's type
                try:
                    default_type = type(merged[k])
                    if default_type == int:
                        merged[k] = int(float(v))
                    elif default_type == float:
                        merged[k] = float(v)
                    elif default_type == bool:
                        merged[k] = str(v).lower() in ("true", "1", "yes")
                    else:
                        merged[k] = v
                except (ValueError, TypeError):
                    merged[k] = v
            else:
                merged[k] = v

        if db_params:
            logger.info("params_from_db", extra={
                "strategy": strategy_name,
                "symbol": symbol,
                "db_keys": list(db_params.keys()),
            })

        return merged

    def reload(self) -> None:
        """Reload all params from DB (call after API update)."""
        self._cache.clear()
        self._load_all()
        logger.info("param_loader_reloaded")

    def save_params(
        self,
        strategy_name: str,
        symbol: str,
        params: dict,
        **kwargs,
    ) -> None:
        """Save params to DB and update cache."""
        self._store.save_strategy_params(
            symbol=symbol,
            strategy_name=strategy_name,
            params=params,
            **kwargs,
        )
        key = f"{strategy_name}:{symbol}"
        self._cache[key] = params
