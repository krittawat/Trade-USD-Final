"""Investigate EURUSDc SL hits — what strategy, SL distance, ATR?"""
import MetaTrader5 as mt5
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta, timezone

mt5.initialize()

symbol = "EURUSDc"

# 1. Check recent deals for EURUSDc
now = datetime.now(timezone.utc)
since = now - timedelta(hours=3)
deals = mt5.history_deals_get(since, now)

print(f"=== EURUSDc Deals (last 3 hours) ===\n")
for d in deals:
    if d.symbol == symbol and d.entry == 1:
        REASON = {0:"CLIENT",1:"MOBILE",2:"WEB",3:"EXPERT",4:"SL",5:"TP",6:"SO"}
        reason = REASON.get(d.reason, f"R={d.reason}")
        profit = d.profit + d.commission + d.swap
        t = datetime.fromtimestamp(d.time)
        print(f"  {t.strftime('%H:%M')} {reason:7s} ${profit:+.2f} vol={d.volume} price={d.price}")

# 2. Check current ATR for EURUSDc
rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 250)
if rates is not None:
    df = pd.DataFrame(rates)
    atr = ta.atr(df['high'], df['low'], df['close'], length=14)
    atr_val = atr.iloc[-1]
    spread = mt5.symbol_info(symbol).spread
    point = mt5.symbol_info(symbol).point
    digits = mt5.symbol_info(symbol).digits
    
    sl_2atr = atr_val * 2.0
    tp_4atr = atr_val * 4.0  # scalping RR=2
    spread_cost = spread * point
    
    print(f"\n=== EURUSDc Market Analysis ===")
    print(f"ATR(14) M5:    {atr_val:.{digits}f}")
    print(f"SL (2.0xATR):  {sl_2atr:.{digits}f}")
    print(f"TP (4.0xATR):  {tp_4atr:.{digits}f}")
    print(f"Spread:        {spread} pts ({spread_cost:.{digits}f})")
    print(f"Spread as % of SL: {spread_cost/sl_2atr*100:.1f}%")
    print(f"Price:         {df['close'].iloc[-1]:.{digits}f}")
    
    # Check if SL is too close to noise
    recent_range = df['high'].iloc[-20:].max() - df['low'].iloc[-20:].min()
    print(f"\nLast 20 bars range: {recent_range:.{digits}f}")
    print(f"SL as % of range:  {sl_2atr/recent_range*100:.1f}%")
    
    if sl_2atr < spread_cost * 3:
        print("\n!! WARNING: SL is less than 3x spread — very tight!")
    if spread_cost / sl_2atr > 0.15:
        print("!! WARNING: Spread cost > 15% of SL — unfavorable risk")

# 3. Check open positions
positions = mt5.positions_get(symbol=symbol)
if positions:
    print(f"\n=== Open {symbol} Positions ===")
    for p in positions:
        print(f"  Ticket={p.ticket} {'BUY' if p.type==0 else 'SELL'} vol={p.volume} entry={p.price_open} SL={p.sl} TP={p.tp} P/L=${p.profit:.2f}")
        if p.sl > 0:
            sl_dist = abs(p.price_open - p.sl)
            tp_dist = abs(p.tp - p.price_open) if p.tp > 0 else 0
            rr = tp_dist / sl_dist if sl_dist > 0 else 0
            print(f"    SL dist={sl_dist:.{digits}f} TP dist={tp_dist:.{digits}f} RR={rr:.2f}")
else:
    print(f"\nNo open {symbol} positions")

mt5.shutdown()
