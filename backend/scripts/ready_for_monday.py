# -*- coding: utf-8 -*-
"""
ANTIGRAVITY READY FOR MONDAY AUDIT
Checking position safety and account health before/at market open.
"""
import sys
from pathlib import Path
from datetime import datetime
import MetaTrader5 as mt5

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.trader.data.fetcher import fetcher
from backend.trader.risk.opus_governor import governor
from backend.trader.observability.logger import C

def run_audit():
    print(f"\n{C.CYAN}╔{'═' * 60}╗{C.RST}")
    print(f"{C.CYAN}║{C.RST}  🚀 {C.BOLD}ANTIGRAVITY MONDAY READINESS AUDIT{C.RST} {' ':^20} {C.CYAN}║{C.RST}")
    print(f"{C.CYAN}╚{'═' * 60}╝{C.RST}\n")

    if not fetcher.connect():
        print(f"{C.RED}❌ Failed to connect to MT5!{C.RST}")
        return

    # 1. Account Health
    acct = mt5.account_info()
    if acct:
        print(f"📊 {C.BOLD}ACCOUNT HEALTH:{C.RST}")
        print(f"   Equity:    ${acct.equity:,.2f}")
        print(f"   Balance:   ${acct.balance:,.2f}")
        print(f"   Margin Lv: {acct.margin_level:.0f}%")
        
        # Check Governor
        from backend.trader.main import get_real_account_state
        state = get_real_account_state(mt5)
        status = governor.compute_status(state)
        
        print(f"   Risk Lv:   {status.risk_level}")
        print(f"   Allowed:   {'✅ YES' if status.trade_allowed else '❌ NO'} ({status.block_reason})")
    else:
        print(f"{C.RED}❌ Could not fetch account info.{C.RST}")

    # 2. Position Audit
    print(f"\n📦 {C.BOLD}POSITION AUDIT:{C.RST}")
    positions = mt5.positions_get()
    if not positions:
        print(f"   {C.GREEN}No open positions found. Ready for a fresh start.{C.RST}")
    else:
        for pos in positions:
            side = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
            color = C.GREEN if side == "BUY" else C.RED
            sl_status = f"{C.GREEN}✅ SL SET ({pos.sl:.2f}){C.RST}" if pos.sl != 0 else f"{C.RED}🚨 NO STOP LOSS!{C.RST}"
            xau_alert = f" {C.YELLOW}[XAU VIGILANCE]{C.RST}" if "XAU" in pos.symbol.upper() else ""
            
            print(f"   • {pos.symbol:<10} #{pos.ticket:<10} {color}{side:<4}{C.RST} PnL: ${pos.profit:>8.2f} │ {sl_status}{xau_alert}")

    # 3. Spread Check (Preview)
    print(f"\n📡 {C.BOLD}MARKET SPREAD PREVIEW:{C.RST}")
    symbols = ["XAUUSD", "BTCUSD", "XAGUSD", "USOILm", "US30m", "USTECm"]
    for sym in symbols:
        info = mt5.symbol_info(sym)
        if info:
            # Check config for max spread
            max_spread = 1000 # Default
            from backend.trader.risk.gate import risk_engine
            config_spreads = risk_engine.config.get("max_spread_points", {})
            max_spread = config_spreads.get(sym.rstrip('mcMC.'), 1000)
            
            status = f"{C.GREEN}OK{C.RST}" if info.spread <= max_spread else f"{C.RED}HIGH SPREAD{C.RST}"
            print(f"   • {sym:<10} Spread: {info.spread:<5} (Limit: {max_spread}) | {status}")
        else:
            print(f"   • {sym:<10} {C.GRAY}Symbol not found{C.RST}")

    print(f"\n{C.CYAN}{'═' * 62}{C.RST}")
    print(f"{C.BOLD}AUDIT COMPLETE.{C.RST} มั่นใจในระบบ รักษาทุนเท่ากับรวย!")
    print(f"{C.CYAN}{'═' * 62}{C.RST}\n")

if __name__ == "__main__":
    run_audit()
    mt5.shutdown()
