# -*- coding: utf-8 -*-
"""
BTC Strategy Tournament — Test ALL strategies on BTC individually
==================================================================
ทดสอบทุกกลยุทธ์ที่เทรด BTC ได้ แยกทีละตัว เพื่อดูว่าตัวไหนกำไร ตัวไหนขาดทุน

Usage:
  cd D:\VibeCode\Trade
  python backend/scripts/backtest_btc_tournament.py --bars 18640
"""
import sys
sys.path.insert(0, "d:/VibeCode/Trade")

import argparse
import json
import logging
import numpy as np
import pandas as pd
from datetime import datetime

from backend.trader.features.volatility import add_volatility_features
from backend.trader.features.structure import add_structure_features, detect_displacement
from backend.trader.features.institutional import add_institutional_features
from backend.trader.features.divergence import detect_rsi_divergence
from backend.trader.features.candle_patterns import detect_candle_patterns
from backend.trader.data.time_utils import time_utils
from backend.trader.regime.classifier import classify_regime
from backend.trader.liquidity.detector import detect_liquidity_events

# Import ALL strategies
from backend.trader.strategy.momentum_scalper_v2 import signal_momentum_scalper_v2
from backend.trader.strategy.momentum_scalper import signal_momentum_scalper
from backend.trader.strategy.predicta_v4 import signal_predicta_v4
from backend.trader.strategy.antigravity_alpha import signal_antigravity_alpha
from backend.trader.strategy.btc_whale import signal_btc_whale
from backend.trader.strategy.micro_scalper import signal_micro_scalper
from backend.trader.strategy.ai_brain_strategy import signal_ai_brain
from backend.trader.strategy.trend_killer import signal_trend_killer
from backend.trader.strategy.momentum_rider import signal_momentum_rider
from backend.trader.strategy.fvg_logic import signal_fvg_logic
from backend.trader.strategy.easy_trend import signal_easy_trend
from backend.trader.strategy.sniper_pro import signal_sniper_pro
from backend.trader.strategy.btc_mean_rev import signal_btc_mean_rev
from backend.trader.strategy.btc_stop_hunt_v2 import signal_btc_stop_hunt_v2
from backend.trader.strategy.btc_elite_v2 import signal_btc_elite_v2

logging.basicConfig(level=logging.WARNING)

with open("d:/VibeCode/Trade/backend/trader/config/settings.json") as f:
    CONFIG = json.load(f)

# BTC contract specs (Exness Standard)
BTC_SPECS = {"spread_points": 800, "contract_size": 1}

STRATEGIES = [
    ("MOMENTUM_SCALPER_V2", signal_momentum_scalper_v2),
    ("MOMENTUM_SCALPER_V1", signal_momentum_scalper),
    ("PREDICTA_V4",         signal_predicta_v4),
    ("ANTIGRAVITY_ALPHA",   signal_antigravity_alpha),
    ("BTC_WHALE",           signal_btc_whale),
    ("MICRO_SCALPER",       signal_micro_scalper),
    ("AI_BRAIN",            signal_ai_brain),
    ("TREND_KILLER",        signal_trend_killer),
    ("MOMENTUM_RIDER",      signal_momentum_rider),
    ("FVG_LOGIC",           signal_fvg_logic),
    ("EASY_TREND",          signal_easy_trend),
    ("SNIPER_PRO",          signal_sniper_pro),
    ("BTC_MEAN_REV",        signal_btc_mean_rev),
    ("BTC_STOP_HUNT_V2",    signal_btc_stop_hunt_v2),
    ("BTC_ELITE_V2",        signal_btc_elite_v2),
]


def load_mt5_data(symbol: str, bars: int, timeframe: str = "M5") -> pd.DataFrame:
    import MetaTrader5 as mt5
    from backend.trader.data.mapper import mapper
    
    # Mapping string TF to MT5 constants
    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M2": mt5.TIMEFRAME_M2,
        "M3": mt5.TIMEFRAME_M3,
        "M4": mt5.TIMEFRAME_M4,
        "M5": mt5.TIMEFRAME_M5,
        "M6": mt5.TIMEFRAME_M6,
        "M10": mt5.TIMEFRAME_M10,
        "M12": mt5.TIMEFRAME_M12,
        "M15": mt5.TIMEFRAME_M15,
        "M20": mt5.TIMEFRAME_M20,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H2": mt5.TIMEFRAME_H2,
        "H3": mt5.TIMEFRAME_H3,
        "H4": mt5.TIMEFRAME_H4,
        "H6": mt5.TIMEFRAME_H6,
        "H8": mt5.TIMEFRAME_H8,
        "H12": mt5.TIMEFRAME_H12,
        "D1": mt5.TIMEFRAME_D1
    }

    if not mt5.initialize():
        print("MT5 Init Failed")
        return None
        
    broker_sym = mapper.to_broker(symbol)
    mt5.symbol_select(broker_sym, True)
    
    # Get real spread
    info = mt5.symbol_info(broker_sym)
    if info:
        BTC_SPECS["spread_points"] = min(info.spread, 800)
        print(f"  [MT5] Spread for {broker_sym}: {BTC_SPECS['spread_points']} pts")

    tf_const = tf_map.get(timeframe, mt5.TIMEFRAME_M5)
    rates = mt5.copy_rates_from_pos(broker_sym, tf_const, 0, bars)
    
    if rates is None or len(rates) == 0:
        print(f"Data fetch failed for {broker_sym} on {timeframe}")
        return None
    
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"  [DATA] {len(df)} {timeframe} bars loaded")
    return df


def prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    df = time_utils.add_session_features(df)
    df = add_volatility_features(df)
    df = add_structure_features(df)
    df = detect_displacement(df)
    df = add_institutional_features(df)
    df = detect_rsi_divergence(df)
    df = detect_candle_patterns(df)
    return df


def test_all_strategies(df, symbol, strategies, lookback=250, hold=50, equity=100.0):
    spread_cost = BTC_SPECS["spread_points"] * 0.01
    contract_size = BTC_SPECS["contract_size"]
    regime_cfg = CONFIG.get("regime", {})
    liq_cfg = CONFIG.get("liquidity", {})
    lot = 0.01

    states = {}
    for name, _ in strategies:
        states[name] = {"eq": equity, "peak": equity, "max_dd": 0.0, "trades": [], "cooldown_until": 0}

    loop_start = lookback
    loop_end = len(df) - hold
    total_iters = loop_end - loop_start

    import sys
    for idx, i in enumerate(range(loop_start, loop_end)):
        if idx % 1000 == 0:
            print(f"\r  ▶ Processing {idx}/{total_iters} bars...", end="", flush=True)

        active_strats = [(name, fn) for name, fn in strategies if i >= states[name]["cooldown_until"]]
        if not active_strats:
            continue

        window = df.iloc[i - lookback: i + 1]
        regime_res = classify_regime(window, regime_cfg)
        events = detect_liquidity_events(window, liq_cfg)
        context = {"symbol": symbol, "regime_result": regime_res}

        future = df.iloc[i + 1: i + 1 + hold]
        if len(future) == 0:
            break
            
        future_highs = future["high"].values
        future_lows = future["low"].values
        future_closes = future["close"].values
        
        for name, fn in active_strats:
            try:
                sig = fn(window, context)
            except Exception as e:
                continue

            if sig is None:
                continue

            entry = sig["entry_price"]
            sl = sig["sl"]
            tp1 = sig["tp1"]
            side = sig["side"]
            
            outcome = None
            if side == "BUY":
                eff = entry + spread_cost / 2
                for j in range(len(future_highs)):
                    if future_lows[j] <= sl:
                        outcome = {"result": "SL", "pnl": sl - eff}
                        break
                    elif future_highs[j] >= tp1:
                        outcome = {"result": "TP", "pnl": tp1 - eff}
                        break
                if outcome is None:
                    outcome = {"result": "TIMEOUT", "pnl": future_closes[-1] - eff}
            else:
                eff = entry - spread_cost / 2
                for j in range(len(future_highs)):
                    if future_highs[j] >= sl:
                        outcome = {"result": "SL", "pnl": eff - sl}
                        break
                    elif future_lows[j] <= tp1:
                        outcome = {"result": "TP", "pnl": eff - tp1}
                        break
                if outcome is None:
                    outcome = {"result": "TIMEOUT", "pnl": eff - future_closes[-1]}

            pnl_usd = outcome["pnl"] * lot * contract_size
            st = states[name]
            st["eq"] += pnl_usd
            if st["eq"] > st["peak"]:
                st["peak"] = st["eq"]
            dd = (st["peak"] - st["eq"]) / st["peak"] * 100 if st["peak"] > 0 else 0
            if dd > st["max_dd"]:
                st["max_dd"] = dd

            st["trades"].append({
                "result": outcome["result"], "pnl": round(pnl_usd, 4), "side": sig["side"]
            })
            st["cooldown_until"] = i + 6

    print(f"\r  ▶ Processing {total_iters}/{total_iters} bars... Done!              \n")

    results = []
    for name, _ in strategies:
        st = states[name]
        tr = st["trades"]
        if not tr:
            results.append({"name": name, "trades": 0, "wins": 0, "losses": 0,
                            "wr": 0, "pf": 0, "max_dd": 0, "net": 0, "eq": equity})
            print(f"  {name}: ⚪ No trades")
            continue

        wins = [t for t in tr if t["pnl"] > 0]
        losses = [t for t in tr if t["pnl"] <= 0]
        gp = sum(t["pnl"] for t in wins) if wins else 0
        gl = abs(sum(t["pnl"] for t in losses)) if losses else 1e-9

        r = {
            "name": name,
            "trades": len(tr), "wins": len(wins), "losses": len(losses),
            "wr": round(len(wins) / len(tr) * 100, 1),
            "pf": round(gp / gl, 2), "max_dd": round(st["max_dd"], 2),
            "net": round(sum(t["pnl"] for t in tr), 4), "eq": round(st["eq"], 2),
            "buys": len([t for t in tr if t["side"] == "BUY"]),
            "sells": len([t for t in tr if t["side"] == "SELL"]),
            "tp": len([t for t in tr if t["result"] == "TP"]),
            "sl": len([t for t in tr if t["result"] == "SL"]),
        }
        results.append(r)
        emoji = "✅" if r["net"] > 0 else "❌"
        print(f"  {emoji} {name}: {r['trades']} trades | WR={r['wr']}% | PF={r['pf']} | Net=${r['net']}")

    return results

def main():
    parser = argparse.ArgumentParser(description="BTC Strategy Tournament")
    parser.add_argument("--symbol", default="BTCUSDm")
    parser.add_argument("--bars", type=int, default=18640)
    parser.add_argument("--tf", default="M5")
    parser.add_argument("--equity", type=float, default=100.0)
    args = parser.parse_args()

    print(f"\n{'#'*70}")
    print(f"  🏆 BTC STRATEGY TOURNAMENT")
    print(f"  Symbol: {args.symbol} | TF: {args.tf} | Bars: {args.bars}")
    print(f"{'#'*70}")

    df = load_mt5_data(args.symbol, args.bars, args.tf)
    print(f"  Computing features...")
    df = prepare_features(df)
    print(f"  Features ready. Testing all strategies...\n")

    results = test_all_strategies(df, args.symbol, STRATEGIES, equity=args.equity)

    # Sort by net PnL
    results.sort(key=lambda x: x.get("net", 0), reverse=True)

    # Print tournament table
    print(f"\n{'='*80}")
    print(f"  🏆 BTC TOURNAMENT RESULTS ({args.symbol}, ~{args.bars//288} days, M5)")
    print(f"{'='*80}")
    print(f"  {'#':<3} {'Strategy':<25} {'Trades':>6} {'WR%':>6} {'PF':>6} {'MaxDD':>7} {'SL':>4} {'Net PnL':>10} {'Verdict':>8}")
    print(f"  {'-'*3} {'-'*25} {'-'*6} {'-'*6} {'-'*6} {'-'*7} {'-'*4} {'-'*10} {'-'*8}")

    for i, r in enumerate(results):
        if r["trades"] == 0:
            verdict = "⚪ SKIP"
        elif r["net"] > 0 and r["wr"] >= 50 and r["pf"] >= 1.3:
            verdict = "🏆 BEST"
        elif r["net"] > 0:
            verdict = "✅ OK"
        elif r["max_dd"] > 10:
            verdict = "💀 KILL"
        else:
            verdict = "❌ BAD"

        print(f"  {i+1:<3} {r['name']:<25} {r['trades']:>6} {r.get('wr',0):>5.1f}% {r.get('pf',0):>5.2f} {r.get('max_dd',0):>6.2f}% {r.get('sl',0):>4} {r.get('net',0):>+9.2f}$ {verdict}")

    # Summary
    profitable = [r for r in results if r.get("net", 0) > 0 and r["trades"] > 0]
    losing = [r for r in results if r.get("net", 0) <= 0 and r["trades"] > 0]
    inactive = [r for r in results if r["trades"] == 0]

    print(f"\n  {'='*80}")
    print(f"  📊 SUMMARY:")
    print(f"    กำไร: {len(profitable)} กลยุทธ์ ({', '.join(r['name'] for r in profitable)})")
    print(f"    ขาดทุน: {len(losing)} กลยุทธ์ ({', '.join(r['name'] for r in losing)})")
    print(f"    ไม่มีสัญญาณ: {len(inactive)} กลยุทธ์")

    if profitable:
        best = profitable[0]
        print(f"\n  🏆 CHAMPION: {best['name']} → Net ${best['net']} | WR={best['wr']}% | PF={best['pf']}")

    if losing:
        print(f"\n  ⚠️ RECOMMEND DISABLE:")
        for r in losing:
            print(f"    ❌ {r['name']}: Net ${r['net']} | SL hits={r.get('sl', '?')}")

    print(f"  {'='*80}\n")

    # Save
    out_path = f"d:/VibeCode/Trade/backend/trader/data/btc_tournament_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(out_path, "w") as f:
        json.dump({"symbol": args.symbol, "bars": args.bars, "results": results,
                   "timestamp": datetime.now().isoformat()}, f, indent=2, default=str)
    print(f"  💾 Saved: {out_path}")


if __name__ == "__main__":
    main()
