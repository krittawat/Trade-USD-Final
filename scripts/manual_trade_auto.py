import sys
import os
import asyncio
from datetime import datetime

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.core.config import get_settings
from app.mt5.client import MT5Client
from app.domain.models import OrderPlan, Action

async def main():
    print("🚀 Auto Trade Executor — XAUUSD")
    
    settings = get_settings()
    client = MT5Client(settings)
    
    if not client.connect():
        print("❌ MT5 Connection Failed")
        return

    symbol = "XAUUSD" # or "XAUUSDc" depending on broker mapping, client handles it
    
    # Check if symbol exists/visible
    info = client.get_symbol_info(symbol)
    if not info:
        symbol = "XAUUSDc" # Try alternative (Cent account)
        info = client.get_symbol_info(symbol)
    
    if not info:
        print(f"❌ Symbol {symbol} not found")
        client.disconnect()
        return

    # 1. Get Current Price
    bid, ask = client.get_current_price(symbol)
    if ask == 0:
        print(f"❌ Cannot get price for {symbol}")
        client.disconnect()
        return

    print(f"💰 {symbol} Price: Bid={bid} Ask={ask}")
    print(f"ℹ️ Info: Point={info.point} Digits={info.digits}")
    
    # 2. Risk Parameters (Wider for safety)
    sl_pips = 500  # Hardcoded safe value > 100
    tp_pips = 1000
    lot_size = 0.01
    
    sl_price = round(ask - (sl_pips * info.point), info.digits)
    tp_price = round(ask + (tp_pips * info.point), info.digits)
    
    print(f"📋 Plan: BUY {lot_size} lots @ {ask}")
    print(f"   SL: {sl_price} (dist {sl_pips} pts)")
    print(f"   TP: {tp_price} (dist {tp_pips} pts)")
    
    # 3. Create Order Plan
    plan = OrderPlan(
        symbol=symbol,
        action=Action.BUY,
        lot_size=lot_size,
        entry_price=ask,
        stop_loss=sl_price,
        take_profit=tp_price,
        confidence=1.0,
        reason="Manual User Request (Auto Exec)",
        risk_usd=10.0,
        risk_pct=1.0, # Added missing field
        strategy_name="MANUAL",
        time=datetime.utcnow()
    )
    
    # 4. Execute
    try:
        print(f"⚡ Sending BUY {lot_size} lot for {symbol}...")
        result = client.send_order(plan)
        print(f"✅ Order Sent! Ticket: {result['ticket']}")
        print(f"   Comment: {result['comment']}")
        print(f"   Retcode: {result['retcode']}")
    except Exception as e:
        print(f"❌ Order Failed: {e}")
        
    client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
