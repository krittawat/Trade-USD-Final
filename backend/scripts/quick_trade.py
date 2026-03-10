"""
Aggressive trading script - London Session
1. Fix dangerous positions (add SL to naked orders)
2. Place high-probability trades on Gold/Silver/BTC
3. Use multi-timeframe analysis for better entries
"""
import MetaTrader5 as mt5
import numpy as np
import sys

if not mt5.initialize():
    print(f"MT5 INIT FAIL: {mt5.last_error()}")
    sys.exit(1)

info = mt5.account_info()
balance = info.balance / 100  # cent to USD
equity = info.equity / 100
print(f"Account: bal=${balance:.2f} eq=${equity:.2f}")

# ──────────── STEP 1: Fix naked positions (no SL) ────────────
pos = mt5.positions_get()
if pos:
    print(f"\n=== {len(pos)} Open Positions ===")
    for p in pos:
        d = "BUY" if p.type == 0 else "SELL"
        pnl = p.profit / 100
        print(f"  #{p.ticket} {p.symbol} {d} lot={p.volume} pnl={pnl:+.2f}USD SL={p.sl} TP={p.tp}")
        
        # Fix missing SL
        if p.sl == 0:
            tk = mt5.symbol_info_tick(p.symbol)
            if tk:
                if p.type == 0:  # BUY
                    new_sl = round(p.price_open - 5.0, 2)  # $5 SL
                    new_tp = round(p.price_open + 3.0, 2) if p.tp == 0 else p.tp
                else:  # SELL
                    new_sl = round(p.price_open + 5.0, 2)
                    new_tp = round(p.price_open - 3.0, 2) if p.tp == 0 else p.tp
                
                mod = mt5.order_send({
                    "action": mt5.TRADE_ACTION_SLTP,
                    "symbol": p.symbol,
                    "position": p.ticket,
                    "sl": new_sl,
                    "tp": new_tp,
                })
                if mod and mod.retcode == mt5.TRADE_RETCODE_DONE:
                    print(f"    FIXED: SL={new_sl} TP={new_tp}")
                else:
                    print(f"    FIX FAIL: {mod.retcode if mod else 'None'} {mod.comment if mod else 'None'}")

# ──────────── STEP 2: Analyze and trade ────────────
print("\n=== ANALYSIS & TRADE ===")

def analyze_symbol(sym, timeframe=mt5.TIMEFRAME_M5, bars=200):
    """Multi-indicator analysis returning signal strength."""
    rates = mt5.copy_rates_from_pos(sym, timeframe, 0, bars)
    if rates is None or len(rates) < 100:
        return None, 0
    
    c = np.array([r[4] for r in rates])  # close
    h = np.array([r[2] for r in rates])  # high
    l = np.array([r[3] for r in rates])  # low
    
    # EMAs
    def ema(data, period):
        alpha = 2.0 / (period + 1)
        result = np.zeros_like(data, dtype=float)
        result[0] = data[0]
        for i in range(1, len(data)):
            result[i] = alpha * data[i] + (1 - alpha) * result[i-1]
        return result
    
    ema9 = ema(c, 9)
    ema21 = ema(c, 21)
    ema50 = ema(c, 50)
    
    # RSI
    deltas = np.diff(c)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-14:])
    avg_loss = np.mean(losses[-14:])
    rs = avg_gain / max(avg_loss, 1e-10)
    rsi = 100 - (100 / (1 + rs))
    
    # ATR
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = np.mean(tr[-14:])
    
    # Bollinger Bands
    bb_mid = np.mean(c[-20:])
    bb_std = np.std(c[-20:])
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    
    # Score
    score = 0
    current = c[-1]
    
    # Trend alignment
    if ema9[-1] > ema21[-1] > ema50[-1]: score += 3  # strong uptrend
    elif ema9[-1] < ema21[-1] < ema50[-1]: score -= 3  # strong downtrend
    elif ema9[-1] > ema21[-1]: score += 1
    elif ema9[-1] < ema21[-1]: score -= 1
    
    # RSI
    if rsi < 30: score += 2  # oversold = buy
    elif rsi > 70: score -= 2  # overbought = sell
    elif rsi < 45: score += 1
    elif rsi > 55: score -= 1
    
    # BB position
    if current <= bb_lower: score += 2  # at lower band = buy
    elif current >= bb_upper: score -= 2  # at upper band = sell
    
    # Momentum (last 5 bars)
    mom = (c[-1] - c[-6]) / c[-6] * 100
    if mom > 0.1: score += 1
    elif mom < -0.1: score -= 1
    
    signal = "BUY" if score > 0 else "SELL" if score < 0 else "NEUTRAL"
    
    return {
        "signal": signal,
        "score": abs(score),
        "price": current,
        "atr": atr,
        "rsi": rsi,
        "ema9": ema9[-1],
        "ema21": ema21[-1],
        "ema50": ema50[-1],
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
    }, score

# Risk per trade: 2% of equity
risk_usd = equity * 0.02

trades_placed = []

for sym, contract_size in [("XAUUSDc", 100), ("XAGUSDc", 5000), ("BTCUSDc", 1)]:
    tk = mt5.symbol_info_tick(sym)
    si = mt5.symbol_info(sym)
    if not tk or not si:
        print(f"{sym}: NOT AVAILABLE")
        continue
    
    analysis, raw_score = analyze_symbol(sym)
    if analysis is None:
        print(f"{sym}: NO DATA")
        continue
    
    spread = tk.ask - tk.bid
    print(f"\n{sym}: signal={analysis['signal']} score={analysis['score']} RSI={analysis['rsi']:.0f} ATR={analysis['atr']:.4f}")
    print(f"  EMA9={analysis['ema9']:.3f} EMA21={analysis['ema21']:.3f} EMA50={analysis['ema50']:.3f}")
    
    if analysis["signal"] == "NEUTRAL" or analysis["score"] < 2:
        print(f"  SKIP: score too low ({analysis['score']})")
        continue
    
    direction = analysis["signal"]
    atr = analysis["atr"]
    
    # SL = 1.5 ATR, TP = 1.0 ATR (tight for WR)
    sl_dist = atr * 1.5
    tp_dist = atr * 1.0
    
    # Lot sizing based on risk
    if "XAU" in sym:
        lot = max(si.volume_min, round(risk_usd / (sl_dist * contract_size) * 100, 2))  # cent
        lot = min(lot, 0.10)  # cap at 0.10
    elif "XAG" in sym:
        lot = max(si.volume_min, round(risk_usd / (sl_dist * contract_size) * 100, 2))
        lot = min(lot, 0.20)
    else:
        lot = si.volume_min
    
    # Round to step
    lot = round(lot / si.volume_step) * si.volume_step
    lot = max(si.volume_min, min(lot, si.volume_max))
    
    if direction == "BUY":
        price = tk.ask
        sl = round(price - sl_dist, si.digits)
        tp = round(price + tp_dist, si.digits)
        order_type = mt5.ORDER_TYPE_BUY
    else:
        price = tk.bid
        sl = round(price + sl_dist, si.digits)
        tp = round(price - tp_dist, si.digits)
        order_type = mt5.ORDER_TYPE_SELL
    
    print(f"  PLACING: {direction} lot={lot} SL={sl} TP={tp}")
    
    r = mt5.order_send({
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": sym,
        "volume": lot,
        "type": order_type,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 30,
        "magic": 888888,
        "comment": f"AG_{direction}_London",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    })
    
    if r and r.retcode == mt5.TRADE_RETCODE_DONE:
        print(f"  SUCCESS ticket={r.order}")
        trades_placed.append(f"{sym} {direction} lot={lot} ticket={r.order}")
    else:
        rc = r.retcode if r else "None"
        cm = r.comment if r else "None"
        print(f"  FAIL: {rc} {cm}")
        trades_placed.append(f"{sym} FAILED: {cm}")

# Final summary
print("\n" + "="*50)
print("TRADE SUMMARY")
print("="*50)
for t in trades_placed:
    print(f"  {t}")

info2 = mt5.account_info()
print(f"\nAccount after: bal=${info2.balance/100:.2f} eq=${info2.equity/100:.2f}")
print(f"Positions: {mt5.positions_total()}")

pos2 = mt5.positions_get()
if pos2:
    print("\n--- All Positions ---")
    total_pnl = 0
    for p in pos2:
        d = "BUY" if p.type == 0 else "SELL"
        pnl = p.profit / 100
        total_pnl += pnl
        sl_ok = "OK" if p.sl > 0 else "NO SL!"
        print(f"  #{p.ticket} {p.symbol} {d} lot={p.volume} pnl={pnl:+.2f}USD SL={sl_ok}")
    print(f"  TOTAL PnL: {total_pnl:+.2f}USD")

mt5.shutdown()
print("\nDone.")
