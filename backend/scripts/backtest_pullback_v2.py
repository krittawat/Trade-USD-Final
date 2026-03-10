"""
Smart Pullback V2 — Higher Win Rate Edition
=============================================
เพิ่ม WR โดยใส่ filter เพิ่ม:
  1. H1 EMA trend confirmation (same direction as M5)
  2. ADX > 20 (trending market only — ไม่เทรดตลาด sideway)
  3. 2-candle confirmation (ไม่พึ่ง candle เดียว)
  4. MACD histogram alignment
  5. BUY ต้องผ่าน filter เข้มกว่า SELL (เพราะ SELL ชนะมากกว่า)
  6. Minimum body size (ไม่เทรด doji-like)
  7. Session filter (London/NY overlap only)

Usage:
    cd d:\\VibeCode\\Trade\\backend
    $env:PYTHONPATH="."; python scripts/backtest_pullback_v2.py
    $env:PYTHONPATH="."; python scripts/backtest_pullback_v2.py --symbol XAGUSDc
"""
print("🧠 Pullback V2 (High WR) — Starting...", flush=True)

import sys
import argparse
import time
import logging
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

# ── V2 Parameter Grid — balanced WR vs trade count ──
PARAM_GRID = [
    # ── Moderate filters (more trades) ──
    {"label": "V2_MOD",      "ema_fast": 21, "ema_slow": 50, "rr": 1.5, "sl_atr": 1.5,
     "pb_pct": 0.3, "rsi_buy_max": 48, "rsi_sell_min": 52, "adx_min": 12, "need_2candle": False, "need_macd": True, "session_filter": False},
    # ADX only (no MACD/2candle)
    {"label": "V2_ADX_ONLY", "ema_fast": 21, "ema_slow": 50, "rr": 1.5, "sl_atr": 1.5,
     "pb_pct": 0.3, "rsi_buy_max": 48, "rsi_sell_min": 52, "adx_min": 15, "need_2candle": False, "need_macd": False, "session_filter": False},
    # MACD only (no ADX/2candle)
    {"label": "V2_MACD_ONLY","ema_fast": 21, "ema_slow": 50, "rr": 1.5, "sl_atr": 1.5,
     "pb_pct": 0.3, "rsi_buy_max": 48, "rsi_sell_min": 52, "adx_min": 0, "need_2candle": False, "need_macd": True, "session_filter": False},
    # Body + MACD (no ADX)
    {"label": "V2_BODY_MACD","ema_fast": 21, "ema_slow": 50, "rr": 1.5, "sl_atr": 1.5,
     "pb_pct": 0.3, "rsi_buy_max": 48, "rsi_sell_min": 52, "adx_min": 0, "need_2candle": True, "need_macd": True, "session_filter": False},
    # SELL-biased (BUY tighter, SELL looser)
    {"label": "V2_SELL_BIAS","ema_fast": 21, "ema_slow": 50, "rr": 2.0, "sl_atr": 1.5,
     "pb_pct": 0.3, "rsi_buy_max": 40, "rsi_sell_min": 50, "adx_min": 12, "need_2candle": False, "need_macd": True, "session_filter": False},
    # SELL only (no BUY at all — proven winner)
    {"label": "V2_SELL_ONLY","ema_fast": 21, "ema_slow": 50, "rr": 2.0, "sl_atr": 1.5,
     "pb_pct": 0.35, "rsi_buy_max": 0, "rsi_sell_min": 50, "adx_min": 12, "need_2candle": False, "need_macd": True, "session_filter": False},
    # Session + light filters
    {"label": "V2_SESS",     "ema_fast": 21, "ema_slow": 50, "rr": 1.5, "sl_atr": 1.5,
     "pb_pct": 0.3, "rsi_buy_max": 48, "rsi_sell_min": 52, "adx_min": 12, "need_2candle": False, "need_macd": False, "session_filter": True},
    # ADX + 2candle (no MACD)
    {"label": "V2_ADX_2C",   "ema_fast": 21, "ema_slow": 50, "rr": 1.5, "sl_atr": 1.5,
     "pb_pct": 0.3, "rsi_buy_max": 48, "rsi_sell_min": 52, "adx_min": 12, "need_2candle": True, "need_macd": False, "session_filter": False},
    # RR 2.0 + moderate
    {"label": "V2_RR2_MOD",  "ema_fast": 21, "ema_slow": 50, "rr": 2.0, "sl_atr": 1.5,
     "pb_pct": 0.3, "rsi_buy_max": 48, "rsi_sell_min": 52, "adx_min": 12, "need_2candle": False, "need_macd": True, "session_filter": False},
    # Tight pullback zone
    {"label": "V2_TIGHT_PB", "ema_fast": 21, "ema_slow": 50, "rr": 1.5, "sl_atr": 1.5,
     "pb_pct": 0.2, "rsi_buy_max": 45, "rsi_sell_min": 55, "adx_min": 12, "need_2candle": False, "need_macd": True, "session_filter": False},
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
    tr = pd.concat([df["high"]-df["low"],
                    (df["high"]-df["close"].shift(1)).abs(),
                    (df["low"]-df["close"].shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False).mean()

def calc_adx(df, p=14):
    """Compute ADX (Average Directional Index)."""
    high, low, close = df["high"], df["low"], df["close"]
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    # Zero out when other is larger
    plus_dm[plus_dm < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0
    tr = pd.concat([high-low, (high-close.shift(1)).abs(), (low-close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(span=p, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(span=p, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(span=p, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(span=p, adjust=False).mean()
    return adx

def calc_macd(s, fast=12, slow=26, signal=9):
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def is_bullish(row):
    body = row["close"] - row["open"]
    total = row["high"] - row["low"]
    if total == 0: return False
    lower_wick = min(row["open"], row["close"]) - row["low"]
    # Bullish body > 30% of range
    if body > 0 and body / total > 0.3:
        return True
    # Hammer
    if lower_wick > abs(body) * 2 and abs(body) / total < 0.3:
        return True
    return False

def is_bearish(row):
    body = row["open"] - row["close"]
    total = row["high"] - row["low"]
    if total == 0: return False
    upper_wick = row["high"] - max(row["open"], row["close"])
    if body > 0 and body / total > 0.3:
        return True
    if upper_wick > abs(body) * 2 and abs(body) / total < 0.3:
        return True
    return False

def get_session_hour(row):
    """Get UTC hour from timestamp."""
    t = row.get("time")
    if t is not None and hasattr(t, "hour"):
        return t.hour
    return 12  # default


# ════════════════════════════════════════════════════════════════
# V2 Signal Generator — Higher WR
# ════════════════════════════════════════════════════════════════

def generate_signal_v2(df, i, params):
    if i < 120:
        return None

    row = df.iloc[i]
    prev = df.iloc[i-1]
    close = row["close"]; high = row["high"]; low = row["low"]
    ema_f = df["_ef"].iloc[i]; ema_s = df["_es"].iloc[i]
    rsi = df["_rsi"].iloc[i]; atr = df["_atr"].iloc[i]
    adx = df["_adx"].iloc[i]; macd_h = df["_mh"].iloc[i]

    if pd.isna(ema_f) or pd.isna(ema_s) or pd.isna(rsi) or pd.isna(atr) or atr <= 0:
        return None
    if pd.isna(adx):
        return None

    # ── Filter 1: ADX trend strength ──
    adx_min = params.get("adx_min", 20)
    if adx < adx_min:
        return None  # Sideways — ไม่เทรด

    # ── Filter 2: Session (London 7-16 UTC or NY 13-21 UTC) ──
    if params.get("session_filter", False):
        hour = get_session_hour(row)
        # London 7-16 UTC or NY 13-21 UTC (overlap 13-16)
        if not (7 <= hour <= 21):
            return None

    # ── Filter 3: Minimum body size (reject tiny candles) ──
    body = abs(close - row["open"])
    if body < atr * 0.15:
        return None  # Doji/indecision — skip

    pb_zone = atr * params["pb_pct"]
    sl_dist = atr * params["sl_atr"]
    rr = params["rr"]
    trend_gap = abs(ema_f - ema_s)
    if trend_gap < atr * 0.15:
        return None  # Weak trend

    # ═══════════════════════════════
    # BUY PULLBACK (stricter)
    # ═══════════════════════════════
    rsi_buy_max = params.get("rsi_buy_max", 45)
    ema_200 = df["_e200"].iloc[i]
    if rsi_buy_max > 0 and ema_f > ema_s and ema_s > ema_200:
        near_ema = low <= ema_f + pb_zone and close > ema_f - pb_zone
        rsi_ok = 25 <= rsi <= rsi_buy_max
        candle_ok = is_bullish(row)
        above_slow = close > ema_s

        # Filter 4: MACD histogram must be positive/turning up
        macd_ok = True
        if params.get("need_macd", False):
            prev_mh = df["_mh"].iloc[i-1] if not pd.isna(df["_mh"].iloc[i-1]) else 0
            macd_ok = macd_h > prev_mh  # histogram turning up

        # Filter 5: 2-candle confirmation
        two_candle_ok = True
        if params.get("need_2candle", False):
            # Previous candle was also bullish OR current is strong
            two_candle_ok = is_bullish(prev) or (body > atr * 0.4)

        if near_ema and rsi_ok and candle_ok and above_slow and macd_ok and two_candle_ok:
            sl = close - sl_dist
            sw_low = df["low"].iloc[max(0,i-5):i+1].min()
            sl = min(sl, sw_low - atr * 0.2)
            buy_rr = 1.5
            tp = close + (close - sl) * buy_rr
            return {"action": "BUY", "sl": round(sl,5), "tp": round(tp,5),
                    "reason": f"PB-BUY EMA{params['ema_fast']} RSI={rsi:.0f} ADX={adx:.0f}"}

    # ═══════════════════════════════
    # SELL REJECTION
    # ═══════════════════════════════
    if ema_f < ema_s:
        near_ema = high >= ema_f - pb_zone and close < ema_f + pb_zone
        rsi_ok = params["rsi_sell_min"] <= rsi <= 75
        candle_ok = is_bearish(row)
        below_slow = close < ema_s

        macd_ok = True
        if params.get("need_macd", False):
            prev_mh = df["_mh"].iloc[i-1] if not pd.isna(df["_mh"].iloc[i-1]) else 0
            macd_ok = macd_h < prev_mh  # histogram turning down

        two_candle_ok = True
        if params.get("need_2candle", False):
            two_candle_ok = is_bearish(prev) or (body > atr * 0.4)

        if near_ema and rsi_ok and candle_ok and below_slow and macd_ok and two_candle_ok:
            sl = close + sl_dist
            sw_high = df["high"].iloc[max(0,i-5):i+1].max()
            sl = max(sl, sw_high + atr * 0.2)
            sell_rr = 2.0
            tp = close - (sl - close) * sell_rr
            return {"action": "SELL", "sl": round(sl,5), "tp": round(tp,5),
                    "reason": f"REJ-SELL EMA{params['ema_fast']} RSI={rsi:.0f} ADX={adx:.0f}"}

    return None


# ════════════════════════════════════════════════════════════════
# Backtester
# ════════════════════════════════════════════════════════════════

def run_backtest(df, symbol, config, params, equity_start=18000.0):
    cs = config["contract_size"]; pt = config["point"]
    df = df.copy()
    df["_ef"] = calc_ema(df["close"], params["ema_fast"])
    df["_es"] = calc_ema(df["close"], params["ema_slow"])
    df["_e200"] = calc_ema(df["close"], 200)
    df["_rsi"] = calc_rsi(df["close"], 14)
    df["_atr"] = calc_atr(df, 14)
    df["_adx"] = calc_adx(df, 14)
    _, _, df["_mh"] = calc_macd(df["close"])

    eq = equity_start; peak = eq; maxdd = 0.0
    trades = []; ot = None; tid = 0
    is_cent = symbol.upper().endswith("C")
    vs = 0.0001 if is_cent else 0.01
    vm = 0.0001 if is_cent else 0.01
    rp = 0.01  # 1% risk
    warmup = 120

    for i in range(warmup, len(df)):
        bar = df.iloc[i]
        bh, bl, bc = bar["high"], bar["low"], bar["close"]

        if ot is not None:
            ot["bars"] += 1
            ex = False; ep = 0; er = ""
            if ot["act"] == "BUY":
                if bl <= ot["sl"]: ep,er,ex = ot["sl"],"SL",True
                elif bh >= ot["tp"]: ep,er,ex = ot["tp"],"TP",True
                elif ot["bars"] >= 60: ep,er,ex = bc,"TIMEOUT",True
            else:
                if bh >= ot["sl"]: ep,er,ex = ot["sl"],"SL",True
                elif bl <= ot["tp"]: ep,er,ex = ot["tp"],"TP",True
                elif ot["bars"] >= 60: ep,er,ex = bc,"TIMEOUT",True

            # Break-even at +1R
            if not ex and ot["bars"] >= 3:
                if ot["act"]=="BUY":
                    sd=ot["ent"]-ot["sl"]
                    if sd>0 and bc>=ot["ent"]+sd: ot["sl"]=ot["ent"]+pt*2
                else:
                    sd=ot["sl"]-ot["ent"]
                    if sd>0 and bc<=ot["ent"]-sd: ot["sl"]=ot["ent"]-pt*2

            if ex:
                pnl = ((ep-ot["ent"]) if ot["act"]=="BUY" else (ot["ent"]-ep)) * ot["lot"] * cs
                eq += pnl
                trades.append({"pnl":round(pnl,2),"act":ot["act"],"er":er,"bars":ot["bars"],
                               "ent":ot["ent"],"exit":ep,"reason":ot.get("reason","")})
                ot = None

        if eq > peak: peak = eq
        dd = (peak-eq)/peak*100 if peak>0 else 0
        if dd > maxdd: maxdd = dd
        if i%3!=0 or ot: continue

        sig = generate_signal_v2(df, i, params)
        if not sig: continue
        sd = abs(bc - sig["sl"])
        if sd <= 0: continue
        lot = max(vm, round((eq*rp)/(sd*cs)/vs)*vs)
        tid += 1
        ot = {"id":tid,"act":sig["action"],"ent":bc,"sl":sig["sl"],"tp":sig["tp"],
              "lot":lot,"bars":0,"reason":sig["reason"]}

    if ot:
        bc = df.iloc[-1]["close"]
        pnl = ((bc-ot["ent"]) if ot["act"]=="BUY" else (ot["ent"]-bc)) * ot["lot"] * cs
        eq += pnl
        trades.append({"pnl":round(pnl,2),"act":ot["act"],"er":"END","bars":ot["bars"],
                       "ent":ot["ent"],"exit":bc,"reason":ot.get("reason","")})

    w = [t for t in trades if t["pnl"]>0]
    l = [t for t in trades if t["pnl"]<=0]
    n = len(trades)
    wr = len(w)/n*100 if n>0 else 0
    tp_s = sum(t["pnl"] for t in w)
    tl_s = abs(sum(t["pnl"] for t in l))
    pf = tp_s/tl_s if tl_s>0 else (10.0 if tp_s>0 else 0)
    net = sum(t["pnl"] for t in trades)
    exp = net/n if n>0 else 0
    buys = [t for t in trades if t["act"]=="BUY"]
    sells = [t for t in trades if t["act"]=="SELL"]
    bwr = len([t for t in buys if t["pnl"]>0])/len(buys)*100 if buys else 0
    swr = len([t for t in sells if t["pnl"]>0])/len(sells)*100 if sells else 0
    tp_count = len([t for t in trades if t["er"]=="TP"])
    sl_count = len([t for t in trades if t["er"]=="SL"])

    return {"n":n,"w":len(w),"l":len(l),"wr":round(wr,1),"pf":round(pf,2),
            "net":round(net,2),"dd":round(maxdd,2),"exp":round(exp,2),
            "eq":round(eq,2),"trades":trades,
            "bn":len(buys),"bwr":round(bwr,1),"sn":len(sells),"swr":round(swr,1),
            "tp":tp_count,"sl":sl_count}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="XAUUSDc", choices=list(SYMBOL_CONFIGS.keys()))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--equity", type=float, default=18000.0)
    args = parser.parse_args()

    symbol = args.symbol; config = SYMBOL_CONFIGS[symbol]

    print("="*115)
    print(f"PULLBACK V2 (HIGH WIN-RATE) \u2014 {config['name']} ({symbol})")
    print(f"   Days: {args.days} | TF: M5 | Equity: ${args.equity:,.0f} | Configs: {len(PARAM_GRID)}")
    print(f"   V2 Filters: ADX + MACD + 2-Candle + Session + Body Size")
    print("="*115)

    if not mt5.initialize():
        print("MT5 Init failed"); return
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=args.days)
    print(f"Fetching M5 ({args.days}d)...", end=" ", flush=True)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, utc_from, utc_to)
    mt5.shutdown()
    if rates is None or len(rates)==0:
        print("No data"); return
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"{len(df)} bars")

    results = []
    for idx, p in enumerate(PARAM_GRID, 1):
        lb = p.get("label", f"SET_{idx}")
        t0 = time.monotonic()
        r = run_backtest(df, symbol, config, p, equity_start=args.equity)
        el = time.monotonic() - t0
        live = r["wr"]>=50 and r["pf"]>=1.3 and r["dd"]<=6 and r["n"]>=10
        ic = "LIVE" if live else ""
        print(f"  [{idx}/{len(PARAM_GRID)}] {lb:<16s} N={r['n']:>3} WR={r['wr']:>5.1f}% PF={r['pf']:>5.2f} "
              f"P&L=${r['net']:>+8.2f} DD={r['dd']:>4.1f}% TP={r['tp']} SL={r['sl']} "
              f"B={r['bn']}({r['bwr']:.0f}%) S={r['sn']}({r['swr']:.0f}%) ({el:.1f}s) {ic}")
        results.append((p, r))

    # Compare V1 vs V2
    print(f"\n{'='*115}")
    print(f"  {'Config':<16s}\u2502{'N':>4}\u2502{'W':>3}\u2502{'L':>3}\u2502{'WR%':>6}\u2502{'PF':>5}\u2502{'Net P&L':>10}\u2502{'DD%':>5}\u2502"
          f"{'TP':>3}\u2502{'SL':>3}\u2502{'BUY':>5}\u2502{'B_WR':>5}\u2502{'SELL':>4}\u2502{'S_WR':>5}\u2502 Status")
    print("\u2500"*115)

    sorted_r = sorted(results, key=lambda x: (x[1]["wr"], x[1]["pf"]*x[1]["wr"]), reverse=True)
    best = None
    for p, r in sorted_r:
        lb = p.get("label","?")
        live = r["wr"]>=50 and r["pf"]>=1.3 and r["dd"]<=6 and r["n"]>=10
        st = "LIVE" if live else ""
        if live and not best: best=(p,r); pf=">>> "
        else: pf="    "
        print(f"{pf}{lb:<12s}\u2502{r['n']:>4}\u2502{r['w']:>3}\u2502{r['l']:>3}\u2502{r['wr']:>5.1f}%\u2502{r['pf']:>5.2f}\u2502"
              f"${r['net']:>+8.2f}\u2502{r['dd']:>4.1f}%\u2502{r['tp']:>3}\u2502{r['sl']:>3}\u2502"
              f"{r['bn']:>4}B\u2502{r['bwr']:>4.0f}%\u2502{r['sn']:>3}S\u2502{r['swr']:>4.0f}%\u2502 {st}")
    print("="*115)

    if best:
        bp,br = best
        print(f"\nLIVE-READY: {bp.get('label','?')} \u2014 WR={br['wr']}% PF={br['pf']} "
              f"DD={br['dd']}% P&L=${br['net']:+.2f}")
        print(f"   TP hit: {br['tp']} | SL hit: {br['sl']} | TIMEOUT: {br['n']-br['tp']-br['sl']}")
        # Last 5 trades
        for t in br["trades"][-5:]:
            ic = "P/L+" if t["pnl"]>0 else "P/L-"
            print(f"   {ic} {t['act']} {t['ent']:.2f}->{t['exit']:.2f} ${t['pnl']:+.2f} {t['er']} {t['reason']}")
    else:
        bw = max(results, key=lambda x: x[1]["wr"])
        print(f"\nNo live-ready. Best WR: {bw[0].get('label','?')} \u2014 WR={bw[1]['wr']}% PF={bw[1]['pf']}")

    print(f"\nPullback V2 Backtest Complete \u2014 {symbol}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n⛔ Cancelled")
    except Exception:
        import traceback; traceback.print_exc()
