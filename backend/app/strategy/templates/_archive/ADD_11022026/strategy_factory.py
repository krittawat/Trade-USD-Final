from typing import Optional, Dict, Any
import logging
from app.strategy.base_strategy import BaseStrategy

# Import Legacy and New Strategies
# Note: In a full migration, we would standardize all to BaseStrategy classes.
# For now, we support both but prefer New.

from app.strategy.antigravity import AntigravityStrategy
from app.strategy.mobile_pro import MobileProStrategy
from app.strategy.antichop import antichop_strategy
from app.strategy.gold_reversal_grid import gold_reversal_strategy
from app.strategy.gold_scalp_pro import gold_scalp_pro_strategy
from app.strategy.gold_sniper_mini import gold_sniper_mini_strategy
from app.strategy.historical_adaptive import historical_adaptive_strategy
from app.strategy.sniper_pro import sniper_pro_strategy
from app.strategy.dual_scalp import dual_scalp_strategy
from app.strategy.hyper_scalp import hyper_scalp_strategy
from app.strategy.eurusd_scalp import EurUsdScalpStrategy
from app.strategy.usdjpy_trend import UsdJpyTrendStrategy
from app.strategy.gbpusd_momentum import GbpUsdMomentumStrategy
from app.strategy.antigravity_fusion import antigravity_fusion
from app.strategy.predicta_v4 import predicta_v4
from app.strategy.predicta_strategy import PredictaStrategy
from app.strategy.hybrid_gold import hybrid_gold_strategy
from app.strategy.vfinal_strategy import v_final_strategy
from app.strategy.btc_ultimate_strategy import btc_ultimate_strategy
from app.strategy.easy_entry import easy_entry_strategy

# Dynamic/Safe Imports
try:
    from app.strategy.easy_trend import easy_trend_strategy
except ImportError:
    easy_trend_strategy = None

# Logger
logger = logging.getLogger("StrategyFactory")

class StrategyFactory:
    """
    Central Factory to retrieve and configure trading strategies.
    Supports 'Tuning' per symbol.
    """
    def __init__(self):
        self._cache = {} # Cache for class-based instances
        
        # Registry of available strategies
        self.registry = {
            "ANTICHOP": antichop_strategy,
            "MOBILEPRO": MobileProStrategy, # Class type
            "GOLDREVERSAL": gold_reversal_strategy,
            "GOLD_SCALP_PRO": gold_scalp_pro_strategy,
            "GOLDSNIPERMINI": gold_sniper_mini_strategy,
            "HISTORICAL_ADAPTIVE": historical_adaptive_strategy,
            "BOT_20012069": historical_adaptive_strategy,
            "SNIPER_PRO": sniper_pro_strategy,
            "DUAL_SCALP": dual_scalp_strategy,
            "HYPER_SCALP": hyper_scalp_strategy,
            "EURUSD_SCALP": EurUsdScalpStrategy, # Class type
            "USDJPY_TREND": UsdJpyTrendStrategy, # Class type
            "GBPUSD_MOMENTUM": GbpUsdMomentumStrategy, # Class type
            "EASY_TREND": easy_trend_strategy,
            "EASY_ENTRY": easy_entry_strategy,
            "FUSION": antigravity_fusion,
            "PREDICTA_V4": predicta_v4,
            "PREDICTA_FUTURES": PredictaStrategy, # Class type
            "HYBRID_GOLD": hybrid_gold_strategy,
            "V_FINAL": v_final_strategy,
            "ORACLE": btc_ultimate_strategy,
            "DEFAULT": AntigravityStrategy # Class type
        }

    def get_strategy(self, strategy_name: str, symbol: str = "XAUUSD") -> Any:
        """
        Get a strategy instance or module, tuned for the specific symbol.
        """
        key = strategy_name.upper()
        strategy_obj = self.registry.get(key, self.registry["DEFAULT"])
        
        # Override Default for XAUUSD -> Gold Scalp Pro (V2)
        if (key == "DEFAULT" or key == "AUTO") and "XAU" in symbol.upper():
             logger.info(f"✨ Auto-Switching to GOLD_SCALP_PRO for {symbol}")
             strategy_obj = self.registry["GOLD_SCALP_PRO"]
        
        # Handle None (missing imports)
        if strategy_obj is None:
            logger.warning(f"Strategy {key} not found or failed import. using DEFAULT.")
            strategy_obj = self.registry["DEFAULT"]

        # Check if it's a Class (needs instantiation) or Module/Object (already instantiated)
        instance = None
        
        if isinstance(strategy_obj, type):
            # It's a class, instantiate it (Singleton-ish per strategy name check?)
            # For now, new instance to ensure clean state or cached?
            # Better to cache classes to avoid re-init overhead if stateless
            if key not in self._cache:
                try:
                    self._cache[key] = strategy_obj()
                    logger.info(f"Initialized new strategy instance: {key}")
                except Exception as e:
                    logger.error(f"Failed to init strategy class {key}: {e}")
                    return self.registry["DEFAULT"]()
            instance = self._cache[key]
        else:
            # It's a module or pre-instantiated object (Legacy)
            instance = strategy_obj

        # --- TUNING STAGE ---
        self._tune_strategy(instance, symbol)
        
        # Load params from DB if supported
        if hasattr(instance, "load_params_from_db"):
            s_name = key
            if hasattr(instance, "name"):
                s_name = instance.name.upper()
            instance.load_params_from_db(s_name)

        return instance

    def _tune_strategy(self, strategy: Any, symbol: str):
        """
        Apply symbol-specific adjustments (Tuning).
        """
        # Example Tuning Logic
        if "XAU" in symbol.upper():
            # Gold typically needs wider stops or specific multipliers
            if hasattr(strategy, "params") and isinstance(strategy.params, dict):
                # strategy.params["volatility_multiplier"] = 1.2
                pass
                
        elif "XAG" in symbol.upper():
             # Silver is noisier
             if hasattr(strategy, "params") and isinstance(strategy.params, dict):
                 # strategy.params["atr_period"] = 21 
                 pass
        
        elif "BTC" in symbol.upper():
            # Crypto needs weekend mode or stricter spread checks
            pass
