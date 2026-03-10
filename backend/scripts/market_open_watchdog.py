import MetaTrader5 as mt5
import time
import os
import sys
import pandas as pd
from datetime import datetime

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.logging import get_logger
from app.core.config import get_settings
from app.risk.position_guardian import PositionGuardian

logger = get_logger("MarketWatchdog")

def calculate_atr(symbol, period=14):
    """Fetch M5 candles and calculate ATR."""
    candles = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, period + 1)
    if candles is None or len(candles) < period + 1:
        return None
    df = pd.DataFrame(candles)
    high = df['high']
    low = df['low']
    close = df['close']
    tr = pd.concat([high - low, 
                    (high - close.shift(1)).abs(), 
                    (low - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.rolling(window=period).mean().iloc[-1]

def audit_and_fix():
    print(f"\n[{datetime.now()}] 🛡️ ANTIGRAVITY MARKET OPEN WATCHDOG")
    print("="*60)
    
    if not mt5.initialize():
        print("❌ MT5 Initialization failed")
        return

    positions = mt5.positions_get()
    if positions is None:
        print("❌ Failed to get positions")
        mt5.shutdown()
        return

    print(f"📊 Active Positions: {len(positions)}")
    
    # Track USOILm hedge
    usoilm_buy = 0.0
    usoilm_sell = 0.0
    
    settings = get_settings()

    for p in positions:
        ticket = p.ticket
        symbol = p.symbol
        p_type = "BUY" if p.type == 0 else "SELL"
        volume = p.volume
        entry = p.price_open
        current = p.price_current
        sl = p.sl
        tp = p.tp
        profit = p.profit

        print(f"[{symbol}] #{ticket} {p_type} {volume} | Entry: {entry} | Curr: {current} | P/L: {profit:.2} | SL: {sl}")

        # 1. Hedge Check
        if "USOIL" in symbol.upper():
            if p_type == "BUY": usoilm_buy += volume
            else: usoilm_sell += volume

        # 2. Toxic SL Check (SELL position but SL is below entry/current while in loss)
        is_toxic = False
        if p_type == "SELL":
            if sl > 0 and sl < current and profit < 0:
                is_toxic = True
        elif p_type == "BUY":
            if sl > current and profit < 0:
                is_toxic = True

        if is_toxic:
            print(f"  ⚠️ TOXIC SL DETECTED! Fixing ticket {ticket}...")
            atr = calculate_atr(symbol)
            if atr:
                # Use 1.5 * M5 ATR as safety buffer
                buffer = atr * 1.5
                new_sl = current + buffer if p_type == "SELL" else current - buffer
                
                # Round to symbol digits
                sym_info = mt5.symbol_info(symbol)
                new_sl = round(new_sl, sym_info.digits)
                
                print(f"  🔧 Adjusting SL to safe level: {new_sl} (Current + {buffer:.4f})")
                
                request = {
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": symbol,
                    "position": ticket,
                    "sl": new_sl,
                    "tp": tp
                }
                res = mt5.order_send(request)
                if res.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"  ✅ SUCCESS: Ticket {ticket} SL fixed.")
                    logger.info("watchdog_sl_fixed", extra={"ticket": ticket, "new_sl": new_sl})
                else:
                    print(f"  ❌ FAILED: {res.comment}")
            else:
                print(f"  ❌ FAILED to calculate ATR for {symbol}")

    # 3. Hedge Report
    print("-" * 60)
    print(f"⛽ USOILm Hedge Audit: BUY {usoilm_buy:.2f} | SELL {usoilm_sell:.2f}")
    if abs(usoilm_buy - usoilm_sell) < 0.001:
        print("  ✅ FULLY HEDGED (Safe)")
    else:
        print(f"  ⚠️ UNBALANCED HEDGE: Net {usoilm_buy - usoilm_sell:.2f}")

    print("=" * 60)
    print("Watchdog cycle complete. Next audit in 60s...")

if __name__ == "__main__":
    while True:
        try:
            audit_and_fix()
        except Exception as e:
            print(f"Error in watchdog: {e}")
        time.sleep(60)
