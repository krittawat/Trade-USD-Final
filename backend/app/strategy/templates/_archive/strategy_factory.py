
# d:\Trade\gold-risk-engine\backend\app\strategy\strategy_factory.py

from app.strategy.antigravity import AntigravityStrategy, Signal
from app.strategy.antichop import antichop_strategy
from app.strategy.mobile_pro import MobileProStrategy
from app.strategy.gold_reversal_grid import gold_reversal_strategy
from app.strategy.historical_adaptive import historical_adaptive_strategy
from app.strategy.gold_sniper_mini import gold_sniper_mini_strategy
from app.strategy.sniper_pro import sniper_pro_strategy
from app.strategy.dual_scalp import dual_scalp_strategy
from app.strategy.antigravity_fusion import antigravity_fusion
from app.strategy.predicta_v4 import predicta_v4
from app.strategy.predicta_strategy import PredictaStrategy
from app.strategy.easy_entry import easy_entry_strategy
from app.strategy.vfinal_strategy import v_final_strategy
from app.strategy.btc_ultimate_strategy import btc_ultimate_strategy
from app.strategy.hyper_scalp import hyper_scalp_strategy
from app.strategy.hybrid_gold import hybrid_gold_strategy
from app.strategy.gold_scalp_pro import gold_scalp_pro_strategy

class StrategyFactory:
    def __init__(self):
        self.strategies = {
            "ANTICHOP": antichop_strategy,
            "MOBILEPRO": MobileProStrategy(),
            "GOLDREVERSAL": gold_reversal_strategy,
            "GOLD_SCALP_PRO": gold_scalp_pro_strategy,
            "GOLDSNIPERMINI": gold_sniper_mini_strategy,
            "HISTORICAL_ADAPTIVE": historical_adaptive_strategy,
            "BOT_20012069": historical_adaptive_strategy,
            "SNIPER_PRO": sniper_pro_strategy,
            "DUAL_SCALP": dual_scalp_strategy,
            "HYPER_SCALP": hyper_scalp_strategy,
            "EASY_TREND": easy_trend_strategy if 'easy_trend_strategy' in globals() else None, # Safe handling if missing import
            "EASY_ENTRY": easy_entry_strategy,
            "FUSION": antigravity_fusion,
            "PREDICTA_V4": predicta_v4,
            "PREDICTA_FUTURES": PredictaStrategy(),
            "HYBRID_GOLD": hybrid_gold_strategy,
            "EASY_FUSION": easy_fusion_strategy if 'easy_fusion_strategy' in globals() else None,
            "SMART_FUSION": smart_fusion_strategy if 'smart_fusion_strategy' in globals() else None,
            "V_FINAL": v_final_strategy,
            "ORACLE": btc_ultimate_strategy,
            "DEFAULT": AntigravityStrategy()
        }

    def get_strategy(self, strategy_name: str):
        strategy = self.strategies.get(strategy_name.upper(), self.strategies["DEFAULT"])
        
        # Load params from DB if supported
        if hasattr(strategy, "load_params_from_db"):
            # Use original name if possible, else key
            s_name = strategy_name.upper()
            if hasattr(strategy, "name"):
                s_name = strategy.name.upper()
            
            strategy.load_params_from_db(s_name)
            
        return strategy
