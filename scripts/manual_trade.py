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
    print("🚀 Manual Trade Executor — XAUUSD")
    
    settings = get_settings()
    client = MT5Client(settings)
    
    if not client.connect():
        print("❌ MT5 Connection Failed")
        return

    symbol = "XAUUSD" # or "XAUUSDm" depending on broker mapping, client handles it
    
    # 1. Get Current Price
    bid, ask = client.get_current_price(symbol)
    if ask == 0:
        print(f"❌ Cannot get price for {symbol}")
        client.disconnect()
        return

    print(f"💰 {symbol} Price: Bid={bid} Ask={ask}")
    
    # 2. Risk Parameters (Safe Defaults)
    sl_pips = 200  # 200 points = $2.00 on Gold
    tp_pips = 400  # 400 points = $4.00 on Gold
    lot_size = 0.01
    
    sl_price = round(ask - (sl_pips * 0.01), 2)
    tp_price = round(ask + (tp_pips * 0.01), 2)
    
    print(f"📋 Plan: BUY {lot_size} lots @ {ask}")
    print(f"   SL: {sl_price} (approx {sl_pips} pips)")
    print(f"   TP: {tp_price} (approx {tp_pips} pips)")
    
    confirm = input("⚠️ Execute this trade? (Y/N): ")
    if confirm.lower() != 'y':
        print("❌ Cancelled")
        client.disconnect()
        return

    # 3. Create Order Plan
    plan = OrderPlan(
        symbol=symbol,
        action=Action.BUY,
        lot_size=lot_size,
        entry_price=ask,
        stop_loss=sl_price,
        take_profit=tp_price,
        confidence=1.0,
        reason="Manual User Request",
        risk_usd=10.0, # Estimated
        strategy_name="MANUAL",
        time=datetime.utcnow()
    )
    
    # 4. Execute
    try:
        result = client.send_order(plan)
        print(f"✅ Order Sent! Ticket: {result['ticket']}")
        print(f"   Comment: {result['comment']}")
        print(f"   Retcode: {result['retcode']}")
    except Exception as e:
        print(f"❌ Order Failed: {e}")
        
    client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
