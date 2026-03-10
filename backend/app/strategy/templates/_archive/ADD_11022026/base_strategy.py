from abc import ABC, abstractmethod
from pydantic import BaseModel, Field, model_validator
from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
import logging
from app.services.questdb_service import questdb_service

logger = logging.getLogger("BaseStrategy")

class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"      # Wait / Do Nothing
    WAIT = "WAIT"      # Legacy alias for HOLD
    NO_TRADE = "NO_TRADE" # Legacy alias for HOLD

class EntryType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"

class RiskModel(str, Enum):
    FIXED = "fixed_risk_2pct"
    ATR = "atr"
    STRUCTURE = "structure"

class StrategyDecision(BaseModel):
    """
    Standardized Decision Object from any Strategy.
    Follows 'Zero-Bug' Engineering Contract.
    """
    # Allow extra fields so bot_manager can dynamically set attributes
    model_config = {"extra": "allow"}
    
    action: SignalType
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Confidence score 0.0-1.0")
    reason: str = Field(default="", description="Human readable reason for the decision")
    
    # Execution Details (Mandatory if BUY/SELL)
    entry_type: EntryType = EntryType.MARKET
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = Field(None, description="Absolute price for SL. Mandatory for BUY/SELL.")
    take_profit: Optional[float] = None
    
    # Risk & Metadata
    risk_model: RiskModel = RiskModel.FIXED
    risk_pct: Optional[float] = Field(None, description="Risk percent for this trade (set by risk manager)")
    comment: str = Field(default="", description="Trade comment for logging")
    tags: List[str] = Field(default_factory=list, description="Tags: e.g. ['scalp', 'trend']")
    debug_data: Dict[str, Any] = Field(default_factory=dict, description="Internal calc values for debugging")
    
    # Legacy Compatibility (Optional, can be derived)
    signal: Optional[str] = None # Deprecated, use action

    def model_post_init(self, __context: Any) -> None:
        # Auto-fill legacy signal field if missing
        if not self.signal:
            self.signal = self.action.value

    def to_dict(self):
        return self.model_dump()

    @model_validator(mode='before')
    @classmethod
    def migrate_legacy_signal(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # If 'action' is missing but 'signal' exists, copy it over
            if 'action' not in data and 'signal' in data:
                data['action'] = data['signal']
            # If 'signal' is NONE/NO_TRADE/WAIT, map to HOLD
            if data.get('action') in ["NONE", "NO_TRADE", "WAIT"]:
                data['action'] = SignalType.HOLD
            
            # Legacy field migration: sl -> stop_loss, tp -> take_profit
            if 'sl' in data and 'stop_loss' not in data:
                data['stop_loss'] = data.pop('sl')
            elif 'sl' in data:
                data.pop('sl')
            if 'tp' in data and 'take_profit' not in data:
                data['take_profit'] = data.pop('tp')
            elif 'tp' in data:
                data.pop('tp')
            
            # Remove non-model fields that legacy code passes
            for legacy_key in ['warning']:
                data.pop(legacy_key, None)
            
            # Normalize confidence: some strategies use 0-100, model expects 0-1
            conf = data.get('confidence')
            if conf is not None and isinstance(conf, (int, float)) and conf > 1.0:
                data['confidence'] = conf / 100.0
        return data

    def is_valid(self, min_conf: float = 0.60):
        """
        Strict validation of the decision.
        """
        # 1. Check Action
        if self.action not in [SignalType.BUY, SignalType.SELL]:
            return False

        # 2. Check Confidence
        if self.confidence < min_conf:
            return False

        # 3. Check SL (CRITICAL SAFETY)
        if (self.action in [SignalType.BUY, SignalType.SELL]) and (self.stop_loss is None or self.stop_loss <= 0):
            # logger.error(f"Strategy Decision INVALID: {self.action} requires STOP LOSS.")
            return False
            
        return True

    @property
    def sl(self): return self.stop_loss

    @property
    def tp(self): return self.take_profit
        
    @property
    def reasons(self):
        return [self.reason] if self.reason else []


class BaseStrategy(ABC):
    """
    Abstract Base Class for all Trading Strategies.
    Enforces the 'analyze' signature.
    """
    
    def __init__(self, name: str = "BaseStrategy"):
        self.name = name
        self.params = {}
        self.logger = logging.getLogger(f"Strategy.{name}")

    @abstractmethod
    def analyze(self, 
               symbol: str, 
               timeframe: str, 
               data: Any, 
               account_state: Optional[Dict] = None,
               **kwargs) -> StrategyDecision:
        """
        Core logic: Input Market Data -> Output StrategyDecision
        
        Args:
            symbol: Trading pair (e.g. 'XAUUSD')
            timeframe: Candle timeframe (e.g. 'M5')
            data: Market data (DataFrame or dict with 'candles', 'spread', etc.)
            account_state: Optional dict with equity, daily_pnl, etc.
        
        Returns:
            StrategyDecision: The strict decision object.
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
        try:
            query = f"SELECT param_key, param_value, param_type FROM sys_strategy_params WHERE strategy_name = '{strategy_name}' ORDER BY timestamp DESC"
            df = questdb_service.query_pandas(query)
            
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
