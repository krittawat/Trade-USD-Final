# -*- coding: utf-8 -*-
import pandas as pd
import logging
from backend.app.domain.models import SymbolProfile
from backend.app.strategy.templates.aether_flow import AetherFlowStrategy
from backend.trader.data.fetcher import fetcher
import MetaTrader5 as mt5

logger = logging.getLogger("opus_logger")

_instances = {}

def signal_aether_flow(df: pd.DataFrame, context: dict) -> dict:
    symbol = context.get('symbol', 'UNKNOWN')
    timeframe = context.get('timeframe', 'M5')
    
    if symbol not in _instances:
        # AetherFlowStrategy handles symbol parameter internal application
        _instances[symbol] = AetherFlowStrategy(name="aether_flow")
        
    strategy = _instances[symbol]
    
    # Pre-fetch H1 candles for MTF Gate if needed by Aether Flow
    # The AetherFlowStrategy uses H1 EMA 50 as a hard gate for Gold/BTC.
    h1_candles = None
    try:
        # Fetching H1 for MTF support
        h1_candles = fetcher.get_rates(symbol, mt5.TIMEFRAME_H1, 200)
    except Exception as e:
        logger.warning(f"AETHER_FLOW: Failed to fetch H1 candles for {symbol}: {e}")

    # Create profile
    profile = SymbolProfile(symbol=symbol, timeframe=timeframe)
    
    try:
        # Pass h1_candles via kwargs as expected by AetherFlowStrategy.analyze
        decision = strategy.analyze(df, profile, h1_candles=h1_candles)
        
        if decision.action.name in ('BUY', 'SELL'):
            entry = float(df['close'].iloc[-1])
            sl = float(decision.stop_loss)
            tp1 = float(decision.take_profit)
            
            # Metadata for execution engine (Dynamic BE)
            extra = decision.extra or {}
            
            # The Aether Flow strategy manages its own TP calculation
            # To match common trader format, we provide tp2/tp3 based on TP1 R-multiple
            risk = abs(entry - sl)
            tp_mult = getattr(strategy, 'tp_mult', 2.5)
            
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
                "model": "AETHER_FLOW",
                "be_activation_r": extra.get("be_activation_r", 1.0)
            }
        else:
            # logger.debug(f"aether_flow [{symbol}] HOLD: {decision.reason}")
            pass
            
    except Exception as e:
        logger.error(f"Error in aether_flow_live: {e}", exc_info=True)
        
    return None
