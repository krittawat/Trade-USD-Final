import logging
import json
import pandas as pd
from datetime import datetime
from backend.trader.storage.sqlite_db import db

logger = logging.getLogger("shadow_engine")

class ShadowEngine:
    """
    Shadow Practice Engine
    Tracks virtual trades for EVERY signal to accumulate experience.
    """
    def __init__(self):
        self.pending_cache = {} # symbol -> list of shadow trades

    def capture_practice_signal(self, signal: dict, df: pd.DataFrame, context: dict = None, events: list = None):
        """
        Records a signal as a virtual trade.
        Stores the features at the time of entry for future learning.
        """
        try:
            symbol = signal.get('symbol')
            # Extract core features for learning (last row)
            features = ['roc_5', 'roc_13', 'natr', 'ema_gap', 'rsi', 'range', 'upper_wick', 'lower_wick', 'hour_sin', 'hour_cos']
            latest = df.iloc[-1]
            
            # Check if all features exist
            feat_dict = {}
            for f in features:
                if f in latest:
                    feat_dict[f] = float(latest[f])
            
            # Add Institutional Features
            if 'displacement' in latest: feat_dict['displacement'] = float(latest['displacement'])
            if 'fvg_gap' in latest: feat_dict['fvg_gap'] = float(latest['fvg_gap'])
            
            # Add Context Features
            if context:
                feat_dict['regime'] = context.get('regime_result', {}).get('regime', 'UNKNOWN')
                feat_dict['htf_align'] = 1 if context.get('htf_ema_align') == 'BULLISH' else -1
            
            # Liquidity Events
            if events:
                feat_dict['liq_count'] = len(events)
            
            feat_json = json.dumps(feat_dict)
            
            # Save to DB
            shadow_id = db.record_shadow_entry(signal, feat_json)
            
            logger.info(f"  🧠 [SHADOW] Practice entry recorded for {symbol} ({signal['model']}) | ID: {shadow_id}")
            return shadow_id
            
        except Exception as e:
            logger.error(f"Failed to capture practice signal: {e}")
            return None

    def track_experience(self, symbol: str, current_price: float):
        """
        Checks all pending shadow trades for a symbol.
        Updates outcome if TP or SL is hit.
        """
        try:
            pending = db.get_pending_shadow_trades(symbol)
            if not pending:
                return

            for trade in pending:
                side = trade['side'].upper()
                entry = trade['entry_price']
                sl = trade['sl']
                tp = trade['tp']
                tid = trade['id']
                
                outcome = 0
                pnl_r = 0.0
                
                if side == "BUY":
                    if current_price >= tp:
                        outcome = 1 # WIN
                        pnl_r = abs(tp - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 1.0
                    elif current_price <= sl:
                        outcome = -1 # LOSS
                        pnl_r = -1.0
                else: # SELL
                    if current_price <= tp:
                        outcome = 1 # WIN
                        pnl_r = abs(entry - tp) / abs(sl - entry) if abs(sl - entry) > 0 else 1.0
                    elif current_price >= sl:
                        outcome = -1 # LOSS
                        pnl_r = -1.0
                
                if outcome != 0:
                    db.update_shadow_outcome(tid, outcome, pnl_r)
                    status = "WIN" if outcome == 1 else "LOSS"
                    color = "\033[92m" if outcome == 1 else "\033[91m"
                    logger.info(f"  🎓 [SHADOW] Experience Recorded: {symbol} {color}{status}\033[0m | ID: {tid} | R: {pnl_r:.2f}")

        except Exception as e:
            logger.error(f"Error tracking shadow experience: {e}")

shadow_engine = ShadowEngine()
