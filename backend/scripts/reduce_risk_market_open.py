"""
🛡️ Market Open Risk Reduction Script
=====================================
เป้าหมาย:
1. ลด lot XAUUSDc จาก 0.5 → ≤5% risk/equity
2. เลื่อน SL เพื่อล็อคกำไร (positions ที่กำไรอยู่)

⚠️ SAFETY:
- จะแสดง PREVIEW ก่อน (ไม่ execute อะไรเลย)
- ต้องพิมพ์ YES เพื่อยืนยัน
- Partial close ใช้ IOC filling

Usage:
    python scripts/reduce_risk_market_open.py            # Preview only
    python scripts/reduce_risk_market_open.py --execute   # Execute after confirmation
"""

import sys
import os
import math
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5

# ─── Configuration ─────────────────────────────────────────────
MAX_RISK_PCT = 5.0          # Target: ≤5% risk per position
PROFIT_LOCK_BUFFER_R = 0.1  # Lock profit at entry + buffer (move SL to ~BE+)
# ────────────────────────────────────────────────────────────────


def get_position_details(pos, sym_info):
    """Calculate detailed metrics for a position."""
    is_buy = (pos.type == mt5.ORDER_TYPE_BUY)
    point = sym_info.point if sym_info else 0.01
    digits = sym_info.digits if sym_info else 2
    tick_value = sym_info.trade_tick_value if sym_info else 1.0
    tick_size = sym_info.trade_tick_size if sym_info else point
    volume_step = sym_info.volume_step if sym_info else 0.01
    volume_min = sym_info.volume_min if sym_info else 0.01

    if is_buy:
        sl_dist = pos.price_open - pos.sl if pos.sl > 0 else 0
        profit_dist = pos.price_current - pos.price_open
    else:
        sl_dist = pos.sl - pos.price_open if pos.sl > 0 else 0
        profit_dist = pos.price_open - pos.price_current

    current_r = profit_dist / sl_dist if sl_dist > 0 else 0
    risk_usd = (sl_dist / tick_size) * tick_value * pos.volume if tick_size > 0 and sl_dist > 0 else 0

    return {
        "ticket": pos.ticket,
        "symbol": pos.symbol,
        "is_buy": is_buy,
        "volume": pos.volume,
        "price_open": pos.price_open,
        "price_current": pos.price_current,
        "sl": pos.sl,
        "tp": pos.tp,
        "profit": pos.profit,
        "sl_dist": sl_dist,
        "profit_dist": profit_dist,
        "current_r": current_r,
        "risk_usd": risk_usd,
        "point": point,
        "digits": digits,
        "tick_value": tick_value,
        "tick_size": tick_size,
        "volume_step": volume_step,
        "volume_min": volume_min,
        "magic": pos.magic,
    }


def calculate_lot_reduction(pos_info, equity, max_risk_pct):
    """Calculate how many lots to close to bring risk ≤ max_risk_pct."""
    max_risk_usd = equity * (max_risk_pct / 100)
    current_risk = pos_info["risk_usd"]

    if current_risk <= max_risk_usd:
        return 0.0, pos_info["volume"]  # No reduction needed

    # Calculate safe lot size
    risk_per_lot = current_risk / pos_info["volume"] if pos_info["volume"] > 0 else 0
    if risk_per_lot <= 0:
        return 0.0, pos_info["volume"]

    safe_lots = max_risk_usd / risk_per_lot
    # Round down to volume_step
    step = pos_info["volume_step"]
    safe_lots = math.floor(safe_lots / step) * step
    safe_lots = max(pos_info["volume_min"], safe_lots)

    lots_to_close = pos_info["volume"] - safe_lots
    # Round to step
    lots_to_close = math.floor(lots_to_close / step) * step

    if lots_to_close < pos_info["volume_min"]:
        lots_to_close = 0  # Can't close less than min lot

    return round(lots_to_close, 8), round(safe_lots, 8)


def calculate_profit_lock_sl(pos_info):
    """Calculate new SL to lock profit (move SL tighter)."""
    if pos_info["current_r"] <= 0:
        return None, ""  # Not in profit, don't touch

    entry = pos_info["price_open"]
    current_sl = pos_info["sl"]
    point = pos_info["point"]
    digits = pos_info["digits"]
    sl_dist = pos_info["sl_dist"]
    current_r = pos_info["current_r"]

    # Strategy based on R-multiple
    if current_r >= 1.5:
        # Lock +1R profit
        if pos_info["is_buy"]:
            new_sl = entry + sl_dist * 1.0
        else:
            new_sl = entry - sl_dist * 1.0
        reason = "Lock +1.0R profit (trailing)"
    elif current_r >= 1.0:
        # Move to Break-Even + buffer
        buffer = 50 * point  # Small buffer above/below entry
        if pos_info["is_buy"]:
            new_sl = entry + buffer
        else:
            new_sl = entry - buffer
        reason = "Move to BE + buffer (lock BE)"
    elif current_r >= 0.3:
        # Tighten SL — move halfway to entry
        if pos_info["is_buy"]:
            new_sl = entry - (sl_dist * 0.5)  # Cut risk in half
        else:
            new_sl = entry + (sl_dist * 0.5)
        reason = "Tighten SL (halve risk distance)"
    elif current_r > 0:
        # Slightly in profit but < 0.3R — tighten to 70% SL distance
        if pos_info["is_buy"]:
            new_sl = entry - (sl_dist * 0.7)
        else:
            new_sl = entry + (sl_dist * 0.7)
        reason = "Tighten SL (70% original risk)"
    else:
        return None, ""

    new_sl = round(new_sl, digits)

    # Safety: don't move SL away from current SL (SL must only tighten)
    if pos_info["is_buy"]:
        if new_sl <= current_sl:
            return None, "SL already tighter"
    else:
        if new_sl >= current_sl and current_sl > 0:
            return None, "SL already tighter"

    # Safety: SL must not cross current price
    if pos_info["is_buy"] and new_sl >= pos_info["price_current"]:
        return None, "New SL would be above current price"
    if not pos_info["is_buy"] and new_sl <= pos_info["price_current"]:
        return None, "New SL would be below current price"

    return new_sl, reason


def partial_close_position(ticket, volume_to_close, pos):
    """Execute partial close on MT5."""
    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        print(f"  ❌ Cannot get tick for {pos.symbol}")
        return False

    if pos.type == mt5.ORDER_TYPE_BUY:
        close_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        close_type = mt5.ORDER_TYPE_BUY
        price = tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": ticket,
        "symbol": pos.symbol,
        "volume": volume_to_close,
        "type": close_type,
        "price": price,
        "deviation": 20,
        "magic": pos.magic,
        "comment": "AG|RiskReduce",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        print(f"  ❌ order_send returned None: {mt5.last_error()}")
        return False
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"  ❌ Partial close failed: retcode={result.retcode}, comment={result.comment}")
        return False

    print(f"  ✅ Partial close OK: {volume_to_close} lots closed, order={result.order}")
    return True


def modify_sl(ticket, new_sl, current_tp):
    """Modify SL on MT5."""
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": new_sl,
        "tp": current_tp,
    }

    result = mt5.order_send(request)
    if result is None:
        print(f"  ❌ modify_sl returned None: {mt5.last_error()}")
        return False
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"  ❌ Modify SL failed: retcode={result.retcode}, comment={result.comment}")
        return False

    print(f"  ✅ SL modified: ticket={ticket}, new_sl={new_sl}")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Execute changes (default: preview only)")
    args = parser.parse_args()

    if not mt5.initialize():
        print("❌ MT5 initialize failed:", mt5.last_error())
        return

    acc = mt5.account_info()
    if not acc:
        print("❌ Cannot get account info")
        mt5.shutdown()
        return

    equity = acc.equity
    positions = mt5.positions_get()
    if not positions or len(positions) == 0:
        print("📭 No open positions.")
        mt5.shutdown()
        return

    # ─── Analyze all positions ───
    actions = []  # list of (action_type, details)

    print("=" * 70)
    print(f"🛡️  RISK REDUCTION PLAN")
    print(f"   Equity: ${equity:,.2f}  |  Max Risk/Position: {MAX_RISK_PCT}%")
    print(f"   Mode: {'🔴 EXECUTE' if args.execute else '🟡 PREVIEW ONLY'}")
    print("=" * 70)

    for pos in positions:
        sym_info = mt5.symbol_info(pos.symbol)
        info = get_position_details(pos, sym_info)

        risk_pct = (info["risk_usd"] / equity * 100) if equity > 0 else 0

        print(f"\n{'─' * 70}")
        print(f"  {'🟢 BUY' if info['is_buy'] else '🔴 SELL'}  {info['symbol']}  |  Ticket: {info['ticket']}")
        print(f"  Volume: {info['volume']}  |  Profit: ${info['profit']:,.2f}  |  R: {info['current_r']:+.2f}R")
        print(f"  Risk: ${info['risk_usd']:,.2f} ({risk_pct:.1f}%)")
        print(f"  SL: {info['sl']:.{info['digits']}f}  →  Entry: {info['price_open']:.{info['digits']}f}  →  Current: {info['price_current']:.{info['digits']}f}")

        # --- Action 1: Lot Reduction ---
        if risk_pct > MAX_RISK_PCT:
            lots_to_close, remaining_lots = calculate_lot_reduction(info, equity, MAX_RISK_PCT)
            if lots_to_close > 0:
                new_risk_usd = info["risk_usd"] * (remaining_lots / info["volume"])
                new_risk_pct = (new_risk_usd / equity * 100)
                print(f"\n  📉 ACTION: PARTIAL CLOSE")
                print(f"     Close: {lots_to_close} lots  |  Keep: {remaining_lots} lots")
                print(f"     Risk: {risk_pct:.1f}% → {new_risk_pct:.1f}%  (${info['risk_usd']:,.0f} → ${new_risk_usd:,.0f})")
                actions.append(("partial_close", {
                    "ticket": info["ticket"],
                    "volume_to_close": lots_to_close,
                    "remaining": remaining_lots,
                    "pos": pos,
                    "symbol": info["symbol"],
                }))
            else:
                print(f"  ⚠️ Cannot reduce further (min lot constraint)")

        # --- Action 2: Tighten SL for profit positions ---
        new_sl, reason = calculate_profit_lock_sl(info)
        if new_sl is not None:
            print(f"\n  🔒 ACTION: TIGHTEN SL")
            print(f"     SL: {info['sl']:.{info['digits']}f} → {new_sl:.{info['digits']}f}")
            print(f"     Reason: {reason}")
            actions.append(("modify_sl", {
                "ticket": info["ticket"],
                "new_sl": new_sl,
                "tp": info["tp"],
                "symbol": info["symbol"],
            }))
        elif reason:
            print(f"  ℹ️  SL: {reason}")

    # ─── Summary ───
    print(f"\n{'=' * 70}")
    print(f"📋 PLAN SUMMARY: {len(actions)} actions")
    print(f"{'=' * 70}")

    partial_closes = [a for a in actions if a[0] == "partial_close"]
    sl_mods = [a for a in actions if a[0] == "modify_sl"]

    if partial_closes:
        print(f"\n  📉 Partial Closes: {len(partial_closes)}")
        for _, d in partial_closes:
            print(f"     {d['symbol']} #{d['ticket']}: close {d['volume_to_close']} lots → keep {d['remaining']}")

    if sl_mods:
        print(f"\n  🔒 SL Modifications: {len(sl_mods)}")
        for _, d in sl_mods:
            print(f"     {d['symbol']} #{d['ticket']}: SL → {d['new_sl']}")

    if not actions:
        print("  ✅ No actions needed — all positions within risk limits!")
        mt5.shutdown()
        return

    # ─── Execute ───
    if not args.execute:
        print(f"\n{'=' * 70}")
        print("🟡 PREVIEW MODE — Nothing executed.")
        print("   To execute, run: python scripts/reduce_risk_market_open.py --execute")
        print(f"{'=' * 70}")
        mt5.shutdown()
        return

    # Confirmation
    print(f"\n{'=' * 70}")
    print("⚠️  CONFIRM EXECUTION")
    print(f"{'=' * 70}")
    print(f"   This will execute {len(actions)} actions on LIVE account.")
    print(f"   Account: #{acc.login}  |  Balance: ${acc.balance:,.2f}")
    confirm = input("\n   Type YES to execute: ")

    if confirm.strip() != "YES":
        print("❌ Cancelled.")
        mt5.shutdown()
        return

    print(f"\n{'=' * 70}")
    print("🔴 EXECUTING...")
    print(f"{'=' * 70}")

    success_count = 0
    fail_count = 0

    # Execute partial closes FIRST (order matters)
    for action_type, details in actions:
        if action_type == "partial_close":
            print(f"\n  📉 Partial Close: {details['symbol']} #{details['ticket']}, {details['volume_to_close']} lots")
            if partial_close_position(details["ticket"], details["volume_to_close"], details["pos"]):
                success_count += 1
            else:
                fail_count += 1

    # Then modify SLs
    for action_type, details in actions:
        if action_type == "modify_sl":
            print(f"\n  🔒 Modify SL: {details['symbol']} #{details['ticket']}, SL → {details['new_sl']}")
            if modify_sl(details["ticket"], details["new_sl"], details["tp"]):
                success_count += 1
            else:
                fail_count += 1

    print(f"\n{'=' * 70}")
    print(f"📊 EXECUTION RESULT")
    print(f"   ✅ Success: {success_count}  |  ❌ Failed: {fail_count}")
    print(f"{'=' * 70}")

    mt5.shutdown()


if __name__ == "__main__":
    main()
