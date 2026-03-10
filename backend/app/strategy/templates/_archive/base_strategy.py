from abc import ABC, abstractmethod
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum
import logging

logger = logging.getLogger("BaseStrategy")

# QuestDB removed — params loaded from SQLite via StrategyFactory

class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    HOLD = "HOLD"
    NO_TRADE = "NO_TRADE"

class StrategyBase(ABC):
    @abstractmethod
    def analyze(self, df, direction_mode: str = "AUTO"):
        pass

class StrategyDecision(BaseModel):
    signal: str  # 'BUY', 'SELL', 'NO_TRADE'
    entry_price: Optional[float] = None
    sl: Optional[float] = None
    tp: Optional[float] = None
    reason: str = ""
    confidence: float = 0.0
    risk_pct: float = 1.0
    risk_multiplier: float = 1.0
    entry_method: str = "Standard"
    strategy_name: str = ""
    timeframe: str = ""
    risk_reward_ratio: float = 0.0

    def to_dict(self):
        return self.model_dump()

    # ── Compatibility with backtester (expects Decision.action) ──
    @property
    def action(self):
        """Map .signal string to Action enum for backtester compatibility."""
        from app.domain.enums import Action
        s = self.signal.value if hasattr(self.signal, "value") else self.signal
        mapping = {"BUY": Action.BUY, "SELL": Action.SELL}
        return mapping.get(s, Action.HOLD)

    # Compatibility Layer for Legacy BotManager
    def is_valid(self, min_conf: float = 60):
        # Handle string "BUY"/"SELL" or Enum with .value
        s = self.signal.value if hasattr(self.signal, "value") else self.signal
        return s in ("BUY", "SELL") and self.confidence >= min_conf

    @property
    def stop_loss(self):
        return self.sl

    @property
    def take_profit(self):
        return self.tp
        
    @property
    def reasons(self):
        # Legacy expectation of list
        return [self.reason] if self.reason else []

class BaseStrategy(ABC):
    @abstractmethod
    def analyze(self, **kwargs) -> StrategyDecision:
        """
        Analyze market data and return a decision.
        """
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """
        Return the current configuration and status of the strategy.
        """
        pass

    def check_exit(self, df, position, **kwargs) -> Optional[Dict[str, Any]]:
        """
        Optional: Strategy-specific exit logic.
        Returns None to hold, or a dict with "action" ("CLOSE", "MODIFY_SL", "MODIFY_TP")
        """
        return None

    def load_params_from_db(self, strategy_name: str):
        """
        Load parameters from QuestDB (sys_strategy_params).
        Updates self.params or instance attributes.
        """
        if _questdb_service is None:
            logger.debug(f"QuestDB service not available, skipping param load for {strategy_name}")
            return
        try:
            query = f"SELECT param_key, param_value, param_type FROM sys_strategy_params WHERE strategy_name = '{strategy_name}' ORDER BY timestamp DESC"
            df = _questdb_service.query_pandas(query)
            
            if df.empty:
                logger.warning(f"No params found in DB for {strategy_name}")
                return

            # Deduplicate by key (latest wins due to ORDER BY DESC)
            # pandas drop_duplicates keeps first by default
            df = df.drop_duplicates(subset=['param_key'])
            
            loaded_count = 0
            for _, row in df.iterrows():
                key = row['param_key']
                val = row['param_value']
                
                # Type conversion based on param_type or inference
                # param_value in DB is DOUBLE.
                # If param_type is BOOL, convert 1.0 -> True
                p_type = row.get('param_type', 'FLOAT')
                
                final_val = val
                if p_type == 'BOOL':
                    final_val = bool(val)
                elif p_type == 'INT':
                    final_val = int(val)
                
                # Update logic
                # 1. Update self.params dict if exists
                if hasattr(self, "params") and isinstance(self.params, dict):
                    self.params[key] = final_val
                    
                # 2. Update instance attribute if exists (e.g. self.RSI_PERIOD)
                if hasattr(self, key):
                     setattr(self, key, final_val)
                
                # 3. Handle CONFIG dicts (SniperPro / HyperScalp)
                if hasattr(self, "CONFIG") and isinstance(self.CONFIG, dict):
                    if key in self.CONFIG:
                        self.CONFIG[key] = final_val
                
                if hasattr(self, "CONFIGS") and isinstance(self.CONFIGS, dict):
                     # Loop through all configs to find key? Or assume DEFAULT/Active?
                     # Simple: Update ALL occurrences in sub-dicts
                     for cfg_name, cfg_data in self.CONFIGS.items():
                         if isinstance(cfg_data, dict) and key in cfg_data:
                             cfg_data[key] = final_val

                loaded_count += 1
                
            logger.info(f"Loaded {loaded_count} params for {strategy_name} from DB")
            
        except Exception as e:
            logger.error(f"Failed to load params for {strategy_name}: {e}")
