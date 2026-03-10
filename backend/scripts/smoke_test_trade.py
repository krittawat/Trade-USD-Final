"""
Smoke Test: ทดสอบว่าระบบเทรดได้จริง
=====================================
ทดสอบ:
  1. MT5 connection
  2. pullback_v2 strategy analysis
  3. Order placement (REAL micro trade 0.01 lot → ปิดทันที)
  4. Position management

Usage:
    cd d:\\VibeCode\\Trade\\backend
    $env:PYTHONPATH="."; python scripts/smoke_test_trade.py
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

def main():
    print("=" * 80)
    print("🔥 SMOKE TEST — ทดสอบระบบเทรดจริง")
    print("=" * 80)

    # ── 1. MT5 Connection ──
    print("\n[1/5] MT5 Connection...", end=" ", flush=True)
    if not mt5.initialize():
        print("❌ FAILED"); return
    info = mt5.account_info()
    if not info:
        print("❌ No account"); mt5.shutdown(); return
    print(f"✅ #{info.login} | Balance: ${info.balance:.2f} | Equity: ${info.equity:.2f}")

    # ── 2. Symbol Check ──
    symbol = "XAUUSDc"
    print(f"\n[2/5] Symbol {symbol}...", end=" ", flush=True)
    si = mt5.symbol_info(symbol)
    if not si:
        print("❌ Not found"); mt5.shutdown(); return
    if not si.visible:
        mt5.symbol_select(symbol, True)
        time.sleep(0.5)
        si = mt5.symbol_info(symbol)
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        print("❌ No tick"); mt5.shutdown(); return
    print(f"✅ Bid={tick.bid} Ask={tick.ask} Spread={si.spread}")

    # ── 3. Strategy Analysis ──
    print(f"\n[3/5] Pullback V2 Strategy Analysis...", end=" ", flush=True)
    from datetime import datetime, timezone, timedelta
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M5, 0, 300)
    if rates is None or len(rates) < 120:
        print(f"❌ Only {len(rates) if rates else 0} bars"); mt5.shutdown(); return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")

    from app.strategy.templates.pullback_v2 import PullbackV2Strategy
    from app.domain.models import SymbolProfile
    from app.domain.enums import RegimeType

    strategy = PullbackV2Strategy()
    profile = SymbolProfile(
        symbol=symbol, contract_size=si.trade_contract_size,
        point=si.point, digits=si.digits,
        volume_min=si.volume_min, volume_max=si.volume_max,
        volume_step=si.volume_step,
    )
    decision = strategy.analyze(df, profile, RegimeType.TRENDING_DOWN)
    print(f"✅ {decision.action.value} | conf={decision.confidence:.2f} | reason={decision.reason}")

    # ── 4. Test Order (micro buy → close immediately) ──
    print(f"\n[4/5] Test Trade (0.01 lot BUY → close immediately)...", flush=True)

    lot = si.volume_min  # smallest possible lot
    price = tick.ask
    sl = round(price - 2000 * si.point, si.digits)  # 2000 points SL (wide - temporary)
    tp = round(price + 2000 * si.point, si.digits)

    print(f"   Placing BUY {lot} @ {price} SL={sl} TP={tp}...", end=" ", flush=True)
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 999999,
        "comment": "SMOKE_TEST",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result is None:
        print(f"❌ order_send returned None — {mt5.last_error()}")
        mt5.shutdown(); return

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"❌ retcode={result.retcode} comment={result.comment}")
        mt5.shutdown(); return

    ticket = result.order
    print(f"✅ Opened! Ticket={ticket}")
    time.sleep(1)

    # ── 5. Close immediately ──
    print(f"\n[5/5] Closing test trade #{ticket}...", end=" ", flush=True)

    # Get current position
    positions = mt5.positions_get(ticket=ticket)
    if not positions:
        # Try by symbol + magic
        positions = mt5.positions_get(symbol=symbol)
        positions = [p for p in (positions or []) if p.magic == 999999]

    if not positions:
        print("⚠️ Position not found (may have been closed by SL/TP)")
    else:
        pos = positions[0]
        close_price = tick.bid  # close BUY at bid
        close_request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL,
            "position": pos.ticket,
            "price": close_price,
            "deviation": 20,
            "magic": 999999,
            "comment": "SMOKE_TEST_CLOSE",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        close_result = mt5.order_send(close_request)
        if close_result and close_result.retcode == mt5.TRADE_RETCODE_DONE:
            pnl = (close_price - pos.price_open) * pos.volume * si.trade_contract_size
            print(f"✅ Closed! P&L=${pnl:+.2f}")
        else:
            rc = close_result.retcode if close_result else "None"
            cm = close_result.comment if close_result else mt5.last_error()
            print(f"❌ Close failed: retcode={rc} comment={cm}")

    mt5.shutdown()

    print("\n" + "=" * 80)
    print("🏆 SMOKE TEST COMPLETE — ระบบเทรดได้จริง!")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        try: mt5.shutdown()
        except: pass
