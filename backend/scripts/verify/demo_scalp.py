import asyncio
from app.core.config import get_settings
from app.mt5.client import MT5Client
from app.domain.models import OrderPlan
from app.domain.enums import Action

def demo_scalp():
    settings = get_settings()
    settings.trading_mode = "LIVE"
    mt5 = MT5Client(settings)
    
    if not mt5.connect():
        print("FAIL: Cannot connect to MT5")
        return
        
    print("--- 🚀 ANTIGRAVITY SCALP DEMO ---")
    
    symbols = ['XAUUSD', 'XAGUSD']
    
    for sym in symbols:
        price_bid, price_ask = mt5.get_current_price(sym)
        if price_ask == 0.0:
            print(f"[{sym}] SKIPPED: Market closed or missing tick data.")
            continue
            
        print(f"[{sym}] Current Ask Price: {price_ask}")
        
        action = Action.BUY
        lot_size = 0.05  # 5 Cent Lots
        
        sl_dist = 1.5 if 'XAU' in sym else 0.10
        tp_dist = 2.5 if 'XAU' in sym else 0.15
        
        sl = round(price_ask - sl_dist, 3)
        tp = round(price_ask + tp_dist, 3)
        
        plan = OrderPlan(
            symbol=sym,
            action=action,
            lot_size=lot_size,
            price=price_ask,
            stop_loss=sl,
            take_profit=tp,
            strategy_name="Demo_Scalper",
            risk_usd=1.0,
            risk_pct=0.1,
            magic=888888,
            comment="AG|Demo_Scalp"
        )
        
        try:
            print(f"[{sym}] Sending Scalp Order: {lot_size} Lots | SL: {sl} | TP: {tp}")
            res = mt5.send_order(plan)
            print(f"[{sym}] ✅ SUCCESS! Ticket: {res['ticket']} @ {res['price']}")
        except Exception as e:
            print(f"[{sym}] ❌ FAILED: {e}")
            
    mt5.disconnect()
    print("--- 🏁 DEMO COMPLETE ---")

if __name__ == "__main__":
    demo_scalp()
