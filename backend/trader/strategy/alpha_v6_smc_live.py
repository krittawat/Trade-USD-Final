import pandas as pd
import logging
from backend.app.domain.models import SymbolProfile
from backend.app.strategy.templates.alpha_v6_smc import AlphaV6SMCStrategy

logger = logging.getLogger("opus_logger")

_instances = {}

def signal_alpha_v6_smc(df: pd.DataFrame, context: dict) -> dict:
    symbol = context.get('symbol', 'UNKNOWN')
    
    if symbol not in _instances:
        _instances[symbol] = AlphaV6SMCStrategy(symbol=symbol)
        
    strategy = _instances[symbol]
    
    # Dynamic Parameters from AI Brain
    strat_params = context.get('brain_params', {}).get('alpha_v6', {})
    if strat_params:
        for k, v in strat_params.items():
            if hasattr(strategy, k):
                setattr(strategy, k, v)
        logger.debug(f"ALPHA_V6_SMC: Applied AI Evolved Params to instance for {symbol}")

    
    # Fake a profile for now
    profile = SymbolProfile(symbol=symbol, timeframe=context.get('timeframe', 'M15'))
    
    try:
        decision = strategy.analyze(df, profile)
        
        if decision.action.name in ('BUY', 'SELL'):
            # Convert simple TP to multiple TPs to match live JSON format
            entry = float(df['close'].iloc[-1])
            sl = float(decision.stop_loss)
            risk = abs(entry - sl)
            
            tp1 = float(decision.take_profit)
            
            # Additional TPs to fulfill expected keys in the live environment
            tp_mult = strategy.tp_mult
            tp2 = entry + (risk * (tp_mult * 1.5)) if decision.action.name == 'BUY' else entry - (risk * (tp_mult * 1.5))
            tp3 = entry + (risk * (tp_mult * 2.0)) if decision.action.name == 'BUY' else entry - (risk * (tp_mult * 2.0))
            
            return {
                "symbol": symbol,
                "side": decision.action.name,
                "entry_type": "MARKET",
                "entry_price": entry,
                "sl": round(sl, 5),
                "tp1": round(tp1, 5),
                "tp2": round(tp2, 5),
                "tp3": round(tp3, 5),
                "rationale": [decision.reason] + decision.tags,
                "confidence": float(decision.confidence),
                "model": "ALPHA_V6_SMC"
            }
        else:
            logger.debug(f"alpha_v6_smc [{symbol}] HOLD: {decision.reason}")
    except Exception as e:
        logger.error(f"Error in alpha_v6_smc_live: {e}")
        
    return None
