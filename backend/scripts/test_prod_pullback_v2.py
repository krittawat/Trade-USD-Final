"""
Test Production Strategy directly on historical data.
"""
import sys, time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

logging.disable(logging.CRITICAL)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
from app.strategy.templates.pullback_v2 import PullbackV2Strategy
from app.domain.models import SymbolProfile, Decision
from app.domain.enums import RegimeType

def main():
    if not mt5.initialize():
        print("❌ MT5 Init failed"); return

    strategy = PullbackV2Strategy()
    symbols = ["XAUUSDc", "XAGUSDc"]
    days = 30
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)

    print(f"============================================================")
    print(f"🧠 TESTING PRODUCTION PULLBACK V2 ({days} Days M5)")
    print(f"============================================================")

    for symbol in symbols:
        si = mt5.symbol_info(symbol)
        if not si: continue
        profile = SymbolProfile(
            symbol=symbol, contract_size=si.trade_contract_size,
            point=si.point, digits=si.digits,
            volume_min=si.volume_min, volume_max=si.volume_max,
            volume_step=si.volume_step
        )

        print(f"   [Data] Fetching {symbol} ...", flush=True)
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, utc_from, utc_to)
        if rates is None or len(rates) == 0: continue
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        print(f"   [Data] Downloaded {len(df)} bars. Processing...", flush=True)

        # Simulate backtest
        trades = []
        open_trade = None
        eq = 18000.0
        is_cent = symbol.upper().endswith("C")
        vs = 0.0001 if is_cent else 0.01

        for i in range(120, len(df)):
            if i % 10000 == 0:
                print(f"   ... processed {i:,} bars ...", flush=True)
            # Use a rolling 300-bar view instead of copying the whole dataframe
            window = df.iloc[max(0, i-300):i+1]
            c = window.iloc[-1]["close"]
            h = window.iloc[-1]["high"]
            l = window.iloc[-1]["low"]

            # Manage open trade
            if open_trade:
                open_trade["bars"] += 1
                ex=False; ep=0; er=""
                if open_trade["act"] == "BUY":
                    if l <= open_trade["sl"]: ep,er,ex = open_trade["sl"], "SL", True
                    elif h >= open_trade["tp"]: ep,er,ex = open_trade["tp"], "TP", True
                    elif open_trade["bars"] >= 60: ep,er,ex = c, "TIMEOUT", True
                else:
                    if h >= open_trade["sl"]: ep,er,ex = open_trade["sl"], "SL", True
                    elif l <= open_trade["tp"]: ep,er,ex = open_trade["tp"], "TP", True
                    elif open_trade["bars"] >= 60: ep,er,ex = c, "TIMEOUT", True
                
                # BE
                if not ex and open_trade["bars"] >= 3:
                    if open_trade["act"] == "BUY":
                        sd = open_trade["ent"] - open_trade["sl"]
                        if sd > 0 and c >= open_trade["ent"] + sd:
                            open_trade["sl"] = open_trade["ent"] + profile.point * 2
                    else:
                        sd = open_trade["sl"] - open_trade["ent"]
                        if sd > 0 and c <= open_trade["ent"] - sd:
                            open_trade["sl"] = open_trade["ent"] - profile.point * 2
                
                if ex:
                    pnl = ((ep-open_trade["ent"]) if open_trade["act"]=="BUY" else (open_trade["ent"]-ep)) * open_trade["lot"] * profile.contract_size
                    eq += pnl
                    trades.append({"pnl":pnl, "act":open_trade["act"], "er":er})
                    open_trade = None
            
            # Find new trade
            if i % 3 != 0 or open_trade: continue

            # Speed up: only call strategy if basic ADX allows it
            # Strategy internal uses EMA and ADX, we call it directly
            decision = strategy.analyze(window, profile, RegimeType.UNKNOWN)
            if decision and decision.action in ["BUY", "SELL"]:
                sd = abs(c - decision.stop_loss)
                if sd > 0:
                    lot = max(vs, round((eq*0.01)/(sd*profile.contract_size)/vs)*vs)
                    open_trade = {
                        "act": decision.action, "ent": c, "sl": decision.stop_loss, 
                        "tp": decision.take_profit, "lot": lot, "bars": 0
                    }
        
        # Close last Open Trade
        if open_trade:
            c = df.iloc[-1]["close"]
            pnl = ((c-open_trade["ent"]) if open_trade["act"]=="BUY" else (open_trade["ent"]-c)) * open_trade["lot"] * profile.contract_size
            eq += pnl
            trades.append({"pnl":pnl, "act":open_trade["act"], "er":"END"})

        n = len(trades)
        w = [t for t in trades if t["pnl"] > 0]
        l = [t for t in trades if t["pnl"] <= 0]
        wr = len(w)/n*100 if n > 0 else 0
        pf = sum(t["pnl"] for t in w)/abs(sum(t["pnl"] for t in l)) if sum(t["pnl"] for t in l) != 0 else 10.0
        net = sum(t["pnl"] for t in trades)
        
        buys = [t for t in trades if t["act"] == "BUY"]
        sells = [t for t in trades if t["act"] == "SELL"]
        b_wr = len([t for t in buys if t["pnl"]>0])/len(buys)*100 if buys else 0
        s_wr = len([t for t in sells if t["pnl"]>0])/len(sells)*100 if sells else 0

        print(f"📊 {symbol:10s} | N={n:3d} | WR={wr:5.1f}% | PF={pf:5.2f} | P&L=${net:+8.2f} | B={len(buys)}({b_wr:.0f}%) S={len(sells)}({s_wr:.0f}%)")

    mt5.shutdown()
    print(f"============================================================")

if __name__ == "__main__":
    main()
