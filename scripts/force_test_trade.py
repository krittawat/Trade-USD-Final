import sys
import os
import asyncio
from datetime import datetime, timezone

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.core.config import get_settings
from app.mt5.client import MT5Client
from app.domain.models import OrderPlan, Action

def main():
    print("🚀 Force Test Trade Executor")
    
    settings = get_settings()
    client = MT5Client(settings)
    
    if not client.connect():
        print("❌ MT5 Connection Failed")
        return

    # Use first symbol from settings or default
    symbols = settings.trading_symbols.split(",")
    symbol = symbols[0].strip() if symbols else "XAUUSDc"
    
    print(f"🎯 Target Symbol: {symbol}")
    
    # 1. Get Current Price
    bid, ask = client.get_current_price(symbol)
    if ask == 0:
        print(f"❌ Cannot get price for {symbol}")
        client.disconnect()
        return

    print(f"💰 Price: Bid={bid} Ask={ask}")
    
    # 2. Risk Parameters (Test Trade)
    # Gold: 1.00 price diff = 100 points (usually) -> Check digits
    symbol_info = client.get_symbol_info(symbol)
    point = symbol_info.point
    digits = symbol_info.digits
    
    # Safe distance: 500 points
    dist_points = 500
    sl_dist = dist_points * point
    tp_dist = dist_points * point
    
    lot_size = 0.01
    
    sl_price = round(ask - sl_dist, digits)
    tp_price = round(ask + tp_dist, digits)
    
    print(f"📋 Plan: BUY {lot_size} lots @ {ask}")
    print(f"   SL: {sl_price} ({dist_points} pts)")
    print(f"   TP: {tp_price} ({dist_points} pts)")
    
    # 3. Create Order Plan
    plan = OrderPlan(
        symbol=symbol,
        action=Action.BUY,
        lot_size=lot_size,
        entry_price=ask,
        stop_loss=sl_price,
        take_profit=0.0, # Test Manual Protection (Case B)
        confidence=1.0,
        reason="Manual Test Trade",
        risk_usd=10.0, 
        risk_pct=0.1,
        strategy_name="MANUAL_TEST",
        magic=0, # Force Manual Magic
        timestamp=datetime.now(timezone.utc)
    )
    
    # 4. Execute
    try:
        result = client.send_order(plan)
        if result['retcode'] == 10009: # DRAW_DONE
            print(f"✅ SUCCESS! Ticket: {result['ticket']}")
            print(f"   Position Opened.")
        else:
            print(f"⚠️ Order Finished with Code: {result['retcode']}")
            print(f"   Comment: {result['comment']}")
            
    except Exception as e:
        print(f"❌ Order Failed: {e}")
        
    client.disconnect()

if __name__ == "__main__":
    main()
