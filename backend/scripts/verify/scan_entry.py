"""
Quick Market Scanner — Find entry opportunities for Gold & Silver.
Analyzes: EMA trend, RSI, MACD, ATR volatility, price action.
"""
import sys, os
sys.path.insert(0, os.path.abspath('.'))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def scan_symbol(symbol, timeframe=mt5.TIMEFRAME_M5, bars=200):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None or len(rates) < 50:
        print(f"[{symbol}] ❌ ไม่มีข้อมูลแท่งเทียน")
        return
    
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    
    close = df['close']
    high = df['high']
    low = df['low']
    
    # --- Indicators ---
    df['ema9'] = ema(close, 9)
    df['ema21'] = ema(close, 21)
    df['ema50'] = ema(close, 50)
    df['rsi'] = rsi(close, 14)
    
    # MACD
    ema12 = ema(close, 12)
    ema26 = ema(close, 26)
    df['macd'] = ema12 - ema26
    df['macd_signal'] = ema(df['macd'], 9)
    df['macd_hist'] = df['macd'] - df['macd_signal']
    
    # ATR
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    df['atr14'] = tr.rolling(14).mean()
    
    # Volume
    df['vol_avg'] = df['tick_volume'].rolling(20).mean()
    df['vol_ratio'] = df['tick_volume'] / df['vol_avg']
    
    # --- Latest values ---
    last = df.iloc[-1]
    prev = df.iloc[-2]
    price = last['close']
    
    print(f"\n{'='*60}")
    print(f"  📊 {symbol} — M5 Analysis @ {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")
    print(f"  Price:     {price:.3f}")
    print(f"  EMA9:      {last['ema9']:.3f}  EMA21: {last['ema21']:.3f}  EMA50: {last['ema50']:.3f}")
    print(f"  RSI(14):   {last['rsi']:.1f}")
    print(f"  MACD Hist: {last['macd_hist']:.4f}  (prev: {prev['macd_hist']:.4f})")
    print(f"  ATR(14):   {last['atr14']:.4f}")
    print(f"  Vol Ratio: {last['vol_ratio']:.2f}x")
    
    # --- Signal Scoring ---
    score = 0
    reasons = []
    direction = None
    
    # 1. EMA Alignment
    if last['ema9'] > last['ema21'] > last['ema50']:
        score += 2
        reasons.append("✅ EMA Bullish alignment (9>21>50)")
        direction = "BUY"
    elif last['ema9'] < last['ema21'] < last['ema50']:
        score += 2
        reasons.append("✅ EMA Bearish alignment (9<21<50)")
        direction = "SELL"
    else:
        reasons.append("⚠️ EMA mixed — no clear trend")
    
    # 2. RSI
    if 30 < last['rsi'] < 45 and direction == "BUY":
        score += 2
        reasons.append(f"✅ RSI pullback zone ({last['rsi']:.1f}) — BUY opportunity")
    elif 55 < last['rsi'] < 70 and direction == "SELL":
        score += 2
        reasons.append(f"✅ RSI overbought zone ({last['rsi']:.1f}) — SELL opportunity")
    elif last['rsi'] < 30:
        score += 1
        reasons.append(f"🔥 RSI oversold ({last['rsi']:.1f}) — potential BUY reversal")
        if direction is None: direction = "BUY"
    elif last['rsi'] > 70:
        score += 1
        reasons.append(f"🔥 RSI overbought ({last['rsi']:.1f}) — potential SELL reversal")
        if direction is None: direction = "SELL"
    else:
        reasons.append(f"⚪ RSI neutral ({last['rsi']:.1f})")
    
    # 3. MACD Cross
    if prev['macd_hist'] < 0 and last['macd_hist'] > 0:
        score += 2
        reasons.append("✅ MACD bullish crossover!")
        if direction is None: direction = "BUY"
    elif prev['macd_hist'] > 0 and last['macd_hist'] < 0:
        score += 2
        reasons.append("✅ MACD bearish crossover!")
        if direction is None: direction = "SELL"
    elif last['macd_hist'] > 0 and last['macd_hist'] > prev['macd_hist']:
        score += 1
        reasons.append("📈 MACD histogram expanding (bullish momentum)")
    elif last['macd_hist'] < 0 and last['macd_hist'] < prev['macd_hist']:
        score += 1
        reasons.append("📉 MACD histogram expanding (bearish momentum)")
    
    # 4. Volume confirmation
    if last['vol_ratio'] > 1.3:
        score += 1
        reasons.append(f"✅ Volume surge ({last['vol_ratio']:.1f}x avg)")
    
    # 5. Price vs EMA (pullback)
    if direction == "BUY" and price <= last['ema21'] * 1.001:
        score += 1
        reasons.append("✅ Price near EMA21 — pullback entry zone")
    elif direction == "SELL" and price >= last['ema21'] * 0.999:
        score += 1
        reasons.append("✅ Price near EMA21 — pullback entry zone")
    
    # --- Recommendation ---
    atr = last['atr14']
    print(f"\n  📋 Signal Analysis:")
    for r in reasons:
        print(f"     {r}")
    
    print(f"\n  🎯 Score: {score}/8")
    
    if score >= 4 and direction:
        sl_dist = atr * 2.0
        tp_dist = atr * 3.0
        if direction == "BUY":
            sl = round(price - sl_dist, 3)
            tp = round(price + tp_dist, 3)
        else:
            sl = round(price + sl_dist, 3)
            tp = round(price - tp_dist, 3)
        
        print(f"\n  🟢 RECOMMENDATION: {direction} {symbol}")
        print(f"     Entry:  {price:.3f}")
        print(f"     SL:     {sl:.3f}  (ATR×2 = {sl_dist:.3f})")
        print(f"     TP:     {tp:.3f}  (ATR×3 = {tp_dist:.3f})")
        print(f"     R:R =   1:{tp_dist/sl_dist:.1f}")
    elif score >= 3:
        print(f"\n  🟡 NEAR SETUP: {direction or 'WAIT'} — ใกล้จะมีจังหวะ รอ confirm เพิ่ม")
    else:
        print(f"\n  🔴 NO TRADE: ยังไม่มีจังหวะที่ชัดเจน — HOLD")


def main():
    if not mt5.initialize():
        print("❌ MT5 connection failed")
        return
    
    info = mt5.account_info()
    print(f"📡 Connected: {info.login} | Balance: {info.balance} {info.currency} | Equity: {info.equity}")
    
    scan_symbol("XAUUSDc")
    scan_symbol("XAGUSDc")
    
    mt5.shutdown()
    print(f"\n{'='*60}")
    print(f"  Scan complete @ {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'='*60}")

if __name__ == "__main__":
    main()
