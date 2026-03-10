"""
Pullback V3 (BUY Focus) — High Win-Rate BUY strategy
======================================================
เพื่อเพิ่ม WR ฝั่ง BUY ให้เกิน 50% เราต้องเปลี่ยนแนวคิด:
  1. Trend ความแข็งแรง: EMA10 > EMA21 > EMA50 (3-MA alignment)
  2. Momentum: RSI ซื้อตอนย่อ (35-45) แต่ไม่โอเวอร์โซลด์(oversold)เกินไปจนเสียเทรนด์
  3. MACD: Histogram > 0 (แรงซื้อเหนือศูนย์)
  4. ADX: > 15 (มีเทรนด์)
  5. Session: เทรดเฉพาะ London & NY overlap (มีวอลุ่มแท้จริงดันราคา)
  6. RR Ratio: ลดเป้ากำไรลงเพื่อให้ถึงเป้าง่ายขึ้น (เช่น RR 1.2 ถึง 1.5)

Usage:
    cd d:\\VibeCode\\Trade\\backend
    $env:PYTHONPATH="."; python scripts/backtest_pullback_v3_buy.py --symbol XAUUSDc
"""
import sys, argparse, time, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

logging.disable(logging.CRITICAL)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

SYMBOL_CONFIGS = {
    "XAUUSDc": {"contract_size": 100.0, "point": 0.01, "digits": 2, "name": "Gold"},
    "XAGUSDc": {"contract_size": 5000.0, "point": 0.001, "digits": 3, "name": "Silver"},
}

PARAM_GRID = [
    # ── DEEP PULLBACK (Winning Configs) ──
    {"label": "DB_PB_21_50_RR1.5", "ema1": 21, "ema2": 50, "ema3": 100, "rr": 1.5, "sl_atr": 2.0, "pb_pct": 0.5, "rsi_min": 25, "rsi_max": 45, "adx_min": 15, "session_only": False},
    {"label": "DB_PB_21_50_RR1.2", "ema1": 21, "ema2": 50, "ema3": 100, "rr": 1.2, "sl_atr": 1.5, "pb_pct": 0.5, "rsi_min": 25, "rsi_max": 45, "adx_min": 15, "session_only": False},
    
    # ── MOMENTUM PULLBACK ──
    {"label": "MOM_10_21_RR1.2",   "ema1": 10, "ema2": 21, "ema3": 50, "rr": 1.2, "sl_atr": 1.5, "pb_pct": 0.5, "rsi_min": 30, "rsi_max": 50, "adx_min": 15, "session_only": False},
    {"label": "MOM_10_21_RR1.5",   "ema1": 10, "ema2": 21, "ema3": 50, "rr": 1.5, "sl_atr": 1.5, "pb_pct": 0.5, "rsi_min": 30, "rsi_max": 50, "adx_min": 15, "session_only": False},

    # ── REVERSAL (Oversold Base) ──
    {"label": "REVERSAL_RR2.0",    "ema1": 10, "ema2": 21, "ema3": 50, "rr": 2.0, "sl_atr": 1.5, "pb_pct": 2.0, "rsi_min": 15, "rsi_max": 35, "adx_min": 0, "session_only": False},
    {"label": "REVERSAL_RR1.5",    "ema1": 10, "ema2": 21, "ema3": 50, "rr": 1.5, "sl_atr": 1.5, "pb_pct": 2.0, "rsi_min": 15, "rsi_max": 35, "adx_min": 0, "session_only": False},
    
    # ── CURRENT PULLBACK V2 (Modified) ──
    {"label": "V2_BUY_MOD_RR1.5",  "ema1": 21, "ema2": 50, "ema3": 200, "rr": 1.5, "sl_atr": 1.5, "pb_pct": 0.3, "rsi_min": 25, "rsi_max": 45, "adx_min": 12, "session_only": False},
    {"label": "V2_BUY_MOD_RR2.0",  "ema1": 21, "ema2": 50, "ema3": 200, "rr": 2.0, "sl_atr": 1.5, "pb_pct": 0.3, "rsi_min": 25, "rsi_max": 45, "adx_min": 12, "session_only": False},
]

# ════════════════════════════════════════════════════════════════
# Indicators
# ════════════════════════════════════════════════════════════════
def calc_ema(s, p): return s.ewm(span=p, adjust=False).mean()
def calc_rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(span=p, adjust=False).mean()
    l = (-d).clip(lower=0).ewm(span=p, adjust=False).mean()
    return 100 - (100 / (1 + g / l.replace(0, np.nan)))
def calc_atr(df, p=14):
    tr = pd.concat([df["high"]-df["low"], (df["high"]-df["close"].shift(1)).abs(), (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False).mean()
def calc_adx(df, p=14):
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm, minus_dm = high.diff().clip(lower=0), (-low.diff()).clip(lower=0)
    plus_dm[plus_dm < minus_dm] = 0; minus_dm[minus_dm < plus_dm] = 0
    tr = pd.concat([high-low, (high-close.shift(1)).abs(), (low-close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=p, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=p, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(span=p, adjust=False).mean() / atr.replace(0, np.nan)
    return (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).ewm(span=p, adjust=False).mean()
def calc_macd(s, fast=12, slow=26, signal=9):
    macd = calc_ema(s, fast) - calc_ema(s, slow)
    sig = calc_ema(macd, signal)
    return macd, sig, macd - sig

# ════════════════════════════════════════════════════════════════
# Signal
# ════════════════════════════════════════════════════════════════
def generate_buy_signal(df, i, p):
    if i < 120: return None
    row = df.iloc[i]; c=row["close"]; h=row["high"]; l=row["low"]; o=row["open"]
    e1=df[f"_e{p['ema1']}"].iloc[i]; e2=df[f"_e{p['ema2']}"].iloc[i]; e3=df[f"_e{p['ema3']}"].iloc[i]
    if pd.isna(e3): return None
    
    rsi = df["_rsi"].iloc[i]; atr = df["_atr"].iloc[i]; adx = df["_adx"].iloc[i]
    mh = df["_macd_h"].iloc[i]
    mh_prev = df["_macd_h"].iloc[i-1]

    # Filters
    if adx < p["adx_min"]: return None
    if p["session_only"]:
        hour = row["time"].hour
        if not (7 <= hour <= 21): return None # London/NY
    
    body = abs(c - o); total = h - l
    if total > 0 and body / total < 0.2: return None # skip doji

    pb_zone = atr * p["pb_pct"]
    rsi_ok = p["rsi_min"] <= rsi <= p["rsi_max"]
    candle_bullish = c > o

    # Mode 1: Reversal (Oversold)
    if "REVERSAL" in p["label"]:
        if rsi_ok and candle_bullish and mh > mh_prev:
            sl = c - atr * p["sl_atr"]
            recent_lows = df["low"].iloc[max(0,i-10):i+1]
            sl = min(sl, recent_lows.min() - atr*0.2)
            tp = c + (c - sl) * p["rr"]
            return {"action": "BUY", "sl": round(sl,5), "tp": round(tp,5), "reason": f"BUY_V3_REV"}
        return None

    # Mode 2: Trend Alignment (e1 > e2) 
    if not (e1 > e2): return None
    
    near_ema = l <= e2 + pb_zone and c > e2 - pb_zone
    macd_ok = mh > mh_prev # MACD turning up is more important than > 0
    
    if near_ema and rsi_ok and macd_ok and candle_bullish:
        sl = c - atr * p["sl_atr"]
        sl = min(sl, df["low"].iloc[max(0,i-5):i+1].min() - atr*0.2)
        tp = c + (c - sl) * p["rr"]
        return {"action": "BUY", "sl": round(sl,5), "tp": round(tp,5), "reason": f"BUY_V3_ALIGN"}
    
    return None

# ════════════════════════════════════════════════════════════════
# Run
# ════════════════════════════════════════════════════════════════
def run_backtest(df, symbol, config, params, eq_start=18000.0):
    cs = config["contract_size"]; pt = config["point"]
    df = df.copy()
    df[f"_e{params['ema1']}"] = calc_ema(df["close"], params["ema1"])
    df[f"_e{params['ema2']}"] = calc_ema(df["close"], params["ema2"])
    df[f"_e{params['ema3']}"] = calc_ema(df["close"], params["ema3"])
    df["_rsi"] = calc_rsi(df["close"], 14)
    df["_atr"] = calc_atr(df, 14)
    df["_adx"] = calc_adx(df, 14)
    _, _, df["_macd_h"] = calc_macd(df["close"])

    eq = eq_start; peak = eq; maxdd = 0.0
    trades = []; ot = None; tid = 0
    vs = 0.0001 if symbol.endswith("c") else 0.01

    for i in range(120, len(df)):
        bar = df.iloc[i]; h, l, c = bar["high"], bar["low"], bar["close"]

        if ot:
            ot["bars"] += 1; ex=False; ep=0; er=""
            if l <= ot["sl"]: ep,er,ex=ot["sl"],"SL",True
            elif h >= ot["tp"]: ep,er,ex=ot["tp"],"TP",True
            elif ot["bars"] >= 80: ep,er,ex=c,"TIMEOUT",True

            # BE +1R
            if not ex and ot["bars"] >= 3:
                sd=ot["ent"]-ot["sl"]
                if sd>0 and c>=ot["ent"]+sd: ot["sl"]=ot["ent"]+pt*2
            
            if ex:
                pnl = (ep-ot["ent"])*ot["lot"]*cs
                eq += pnl; trades.append({"pnl":pnl,"er":er,"bars":ot["bars"]})
                ot = None

        if eq > peak: peak = eq
        dd = (peak-eq)/peak*100 if peak>0 else 0
        if dd > maxdd: maxdd = dd
        if i%3!=0 or ot: continue

        sig = generate_buy_signal(df, i, params)
        if sig:
            sd = abs(c - sig["sl"])
            if sd <= 0: continue
            lot = max(vs, round((eq*0.01)/(sd*cs)/vs)*vs)
            tid += 1
            ot = {"id":tid,"act":"BUY","ent":c,"sl":sig["sl"],"tp":sig["tp"],"lot":lot,"bars":0}

    if ot:
        c = df.iloc[-1]["close"]; pnl = (c-ot["ent"])*ot["lot"]*cs
        eq += pnl; trades.append({"pnl":pnl,"er":"END","bars":ot["bars"]})

    w = [t for t in trades if t["pnl"]>0]
    n = len(trades)
    wr = len(w)/n*100 if n>0 else 0
    pf = sum(t["pnl"] for t in w)/abs(sum(t["pnl"] for t in trades if t["pnl"]<=0)) if sum(t["pnl"] for t in trades if t["pnl"]<=0)!=0 else 10.0
    net = sum(t["pnl"] for t in trades)
    return {"n":n,"w":len(w),"wr":wr,"pf":pf,"net":net,"dd":maxdd}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="XAUUSDc", choices=list(SYMBOL_CONFIGS.keys()))
    args = parser.parse_args()
    sym = args.symbol
    print(f"📊 Fetching 30d M5 for {sym}...", flush=True)
    if not mt5.initialize(): return
    rates = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 6000)
    mt5.shutdown()
    df = pd.DataFrame(rates); df["time"] = pd.to_datetime(df["time"], unit="s")
    
    print(f"\n{'Config':<16}│{'N':>4}│{'WR%':>6}│{'PF':>5}│{'Net':>10}│{'DD%':>5}")
    print("─"*53)
    best_wr = 0
    for p in PARAM_GRID:
        r = run_backtest(df, sym, SYMBOL_CONFIGS[sym], p)
        flag = "🟢" if r["wr"]>=50 and r["pf"]>=1.3 and r["n"]>=5 else ""
        print(f"{p['label']:<16}│{r['n']:>4}│{r['wr']:>5.1f}%│{r['pf']:>5.2f}│${r['net']:>9.2f}│{r['dd']:>4.1f}% {flag}")
        if r["wr"] > best_wr and r["n"] >= 3: best_wr = r["wr"]

if __name__ == "__main__": main()
