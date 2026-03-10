import requests
import logging
from datetime import datetime

logger = logging.getLogger("opus_logger")

ORACLE_URL = "http://127.0.0.1:8001/api/v1/signal/btc"
TIMEOUT_SEC = 1.0

def signal_btc_oracle(df, context):
    """
    BTC Crypto Oracle Strategy (v1)
    Fetches real-time liquidation clusters and squeeze probabilities from the external FastAPI oracle.
    """
    symbol = context.get('symbol', '').upper()
    if 'BTC' not in symbol:
        return None

    try:
        response = requests.get(ORACLE_URL, timeout=TIMEOUT_SEC)
        
        # Oracle is explicitly returning 503 if no market data is seen yet
        if response.status_code != 200:
            return None
            
        data = response.json()
        signal_type = data.get("signal", "NEUTRAL")
        
        if signal_type == "NEUTRAL":
            return None
            
        confidence_pct = data.get("confidence_score", 0) / 100.0  # Convert 0-100 to 0.0-1.0
        reason = data.get("reason", "Oracle signal")
        target_zone = data.get("target_liquidity_zone", 0.0)
        
        # Oracle uses Binance mark price, we need to map the entry bounds to MT5 Close price
        latest = df.iloc[-1]
        mt5_close = latest['close']
        atr = latest.get('atr', 1000) # Fallback to 1000 if no ATR
        
        # Convert Oracle signal to Opus Standard Decision Object
        sl_points = mt5_close * 0.015 # 1.5% stop loss for BTC
        if signal_type == "LONG":
            sl = mt5_close - sl_points
            tp1 = target_zone if target_zone > mt5_close else mt5_close + (sl_points * 2) 
            tp2 = mt5_close + (sl_points * 3)
            tp3 = mt5_close + (sl_points * 4)
        else:
            sl = mt5_close + sl_points
            tp1 = target_zone if target_zone < mt5_close and target_zone > 0 else mt5_close - (sl_points * 2)
            tp2 = mt5_close - (sl_points * 3)
            tp3 = mt5_close - (sl_points * 4)

        # Dynamic Stop-hunt adjustment based on ATR
        # If ATR is very high, standard % might not be enough
        min_sl = atr * 2.0
        actual_sl_dist = abs(mt5_close - sl)
        if actual_sl_dist < min_sl:
            if signal_type == "LONG":
                sl = mt5_close - min_sl
            else:
                sl = mt5_close + min_sl

        # Calculate final R:R
        actual_sl_dist = abs(mt5_close - sl)
        actual_tp_dist = abs(mt5_close - tp1)
        rr = actual_tp_dist / actual_sl_dist if actual_sl_dist > 0 else 0
        
        # Only take signals with decent risk reward (e.g. at least 1R)
        if rr < 1.0:
            return None

        decision = {
            'symbol': context.get('symbol'),
            'timeframe': context.get('timeframe'),
            'model': 'BTC_ORACLE',
            'side': signal_type,
            'confidence': confidence_pct,
            'entry_type': 'MARKET',
            'entry_price': mt5_close,
            'sl': round(sl, 2),
            'tp1': round(tp1, 2),
            'tp2': round(tp2, 2),
            'tp3': round(tp3, 2),
            'rationale': [
                f"🧠 Oracle: {reason}",
                f"🧲 Target Liq Zone: {target_zone:.2f}",
                f"🎯 R:R = {rr:.2f}"
            ],
            'timestamp': datetime.now().isoformat()
        }
        
        return decision

    except requests.exceptions.RequestException as e:
        # Expected if Oracle is off or busy. Catch quietly to not pollute logs.
        logger.debug(f"BTC Oracle request failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Error parsing BTC Oracle signal: {e}", exc_info=True)
        return None
