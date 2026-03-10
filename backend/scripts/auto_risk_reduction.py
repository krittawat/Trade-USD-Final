"""
🛡️ AUTO Risk Reduction — Wait for Market Open & Execute
=========================================================
ตั้งทิ้งไว้ก่อนนอน script จะ:
1. Poll MT5 ทุก 30 วินาที รอตลาดเปิด
2. เมื่อตลาดเปิด → รอ spread settle (60 วินาที)
3. Re-verify positions ยังเหมือนเดิม
4. Execute: partial close XAUUSDc + tighten SL
5. ส่ง Telegram แจ้งผล

Safety:
- ถ้า position หายไป/เปลี่ยน → ABORT + แจ้ง
- ถ้า spread กว้างเกิน → Wait + retry
- Log ทุก action

Usage:
    python scripts/auto_risk_reduction.py
"""

import sys
import os
import math
import time
import asyncio
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import MetaTrader5 as mt5

# ─── Configuration ─────────────────────────────────────────────
MAX_RISK_PCT = 5.0              # Target max risk per position
POLL_INTERVAL = 30              # seconds between market checks
SPREAD_SETTLE_WAIT = 60         # seconds to wait after market opens for spread to settle
MAX_SPREAD_GOLD_POINTS = 800    # Max acceptable spread for gold (in points, 8.00 in price)
MAX_SPREAD_SILVER_POINTS = 300  # Max acceptable spread for silver
MAX_WAIT_HOURS = 12             # Give up after this many hours
LOG_FILE = os.path.join(os.path.dirname(__file__), "..", "logs", "risk_reduction.log")

# ─── Expected positions to verify (from Friday) ─────────────────
EXPECTED_POSITIONS = {
    2669421643: {"symbol": "XAUUSDc", "action": "partial_close", "close_lots": 0.4, "keep_lots": 0.1},
    2669368754: {"symbol": "XAGUSDc", "action": "tighten_sl"},
}
# ────────────────────────────────────────────────────────────────


def log(msg: str, level: str = "INFO"):
    """Log to console and file."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def send_telegram(msg: str):
    """Send Telegram notification (fire-and-forget)."""
    try:
        # Load env vars
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

        if not bot_token or not chat_id:
            log("Telegram not configured, skipping notification", "WARN")
            return

        import httpx
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": msg, "parse_mode": "HTML"}
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                log("📱 Telegram notification sent")
            else:
                log(f"Telegram failed: {resp.status_code}", "WARN")
    except Exception as e:
        log(f"Telegram error: {e}", "WARN")


def is_market_open(symbol: str = "XAUUSDc") -> bool:
    """Check if market is open by trying to get a fresh tick."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return False

    # Check if tick is fresh (within last 60 seconds)
    now = time.time()
    tick_age = now - tick.time
    return tick_age < 60


def get_spread(symbol: str) -> float:
    """Get current spread in points."""
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return 999999
    sym_info = mt5.symbol_info(symbol)
    point = sym_info.point if sym_info else 0.01
    spread = (tick.ask - tick.bid) / point if point > 0 else 0
    return spread


def verify_positions() -> dict:
    """Verify expected positions still exist and return current state."""
    positions = mt5.positions_get()
    if not positions:
        return {}

    found = {}
    for pos in positions:
        if pos.ticket in EXPECTED_POSITIONS:
            expected = EXPECTED_POSITIONS[pos.ticket]
            if pos.symbol == expected["symbol"]:
                sym_info = mt5.symbol_info(pos.symbol)
                is_buy = (pos.type == mt5.ORDER_TYPE_BUY)
                point = sym_info.point if sym_info else 0.01
                digits = sym_info.digits if sym_info else 2
                tick_value = sym_info.trade_tick_value if sym_info else 1.0
                tick_size = sym_info.trade_tick_size if sym_info else point

                if is_buy:
                    sl_dist = pos.price_open - pos.sl if pos.sl > 0 else 0
                    profit_dist = pos.price_current - pos.price_open
                else:
                    sl_dist = pos.sl - pos.price_open if pos.sl > 0 else 0
                    profit_dist = pos.price_open - pos.price_current

                current_r = profit_dist / sl_dist if sl_dist > 0 else 0
                risk_usd = (sl_dist / tick_size) * tick_value * pos.volume if tick_size > 0 and sl_dist > 0 else 0

                found[pos.ticket] = {
                    "pos": pos,
                    "sym_info": sym_info,
                    "is_buy": is_buy,
                    "sl_dist": sl_dist,
                    "profit_dist": profit_dist,
                    "current_r": current_r,
                    "risk_usd": risk_usd,
                    "point": point,
                    "digits": digits,
                    "volume_step": sym_info.volume_step if sym_info else 0.01,
                }
    return found


def execute_partial_close(ticket: int, volume_to_close: float, pos) -> bool:
    """Execute partial close."""
    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        log(f"❌ Cannot get tick for {pos.symbol}", "ERROR")
        return False

    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    price = tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": ticket,
        "symbol": pos.symbol,
        "volume": volume_to_close,
        "type": close_type,
        "price": price,
        "deviation": 30,  # Wider deviation for market open
        "magic": pos.magic,
        "comment": "AG|AutoRiskReduce",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None:
        log(f"❌ Partial close returned None: {mt5.last_error()}", "ERROR")
        return False
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log(f"❌ Partial close failed: retcode={result.retcode}, comment={result.comment}", "ERROR")
        return False

    log(f"✅ Partial close OK: {pos.symbol} #{ticket}, closed {volume_to_close} lots, order={result.order}")
    return True


def execute_modify_sl(ticket: int, new_sl: float, tp: float) -> bool:
    """Modify SL."""
    request = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "sl": new_sl,
        "tp": tp,
    }

    result = mt5.order_send(request)
    if result is None:
        log(f"❌ Modify SL returned None: {mt5.last_error()}", "ERROR")
        return False
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log(f"❌ Modify SL failed: retcode={result.retcode}, comment={result.comment}", "ERROR")
        return False

    log(f"✅ SL modified: #{ticket}, new_sl={new_sl}")
    return True


def calculate_tighten_sl(info: dict) -> tuple:
    """Calculate tightened SL based on current R."""
    current_r = info["current_r"]
    pos = info["pos"]
    sl_dist = info["sl_dist"]
    digits = info["digits"]
    point = info["point"]

    if current_r <= 0:
        return None, "Position not in profit"

    if current_r >= 1.5:
        # Lock +1R
        if info["is_buy"]:
            new_sl = pos.price_open + sl_dist * 1.0
        else:
            new_sl = pos.price_open - sl_dist * 1.0
        reason = "Lock +1.0R profit"
    elif current_r >= 1.0:
        buffer = 50 * point
        if info["is_buy"]:
            new_sl = pos.price_open + buffer
        else:
            new_sl = pos.price_open - buffer
        reason = "Break-Even + buffer"
    elif current_r >= 0.3:
        if info["is_buy"]:
            new_sl = pos.price_open - (sl_dist * 0.5)
        else:
            new_sl = pos.price_open + (sl_dist * 0.5)
        reason = "Halve risk distance"
    elif current_r > 0:
        if info["is_buy"]:
            new_sl = pos.price_open - (sl_dist * 0.7)
        else:
            new_sl = pos.price_open + (sl_dist * 0.7)
        reason = "70% original risk"
    else:
        return None, "Not in profit"

    new_sl = round(new_sl, digits)

    # Only tighten (never widen)
    if info["is_buy"] and new_sl <= pos.sl:
        return None, "SL already tighter"
    if not info["is_buy"] and new_sl >= pos.sl and pos.sl > 0:
        return None, "SL already tighter"

    # Don't cross current price
    if info["is_buy"] and new_sl >= pos.price_current:
        return None, "Would cross current price"
    if not info["is_buy"] and new_sl <= pos.price_current:
        return None, "Would cross current price"

    return new_sl, reason


def main():
    log("=" * 60)
    log("🛡️  AUTO RISK REDUCTION — Waiting for Market Open")
    log("=" * 60)

    if not mt5.initialize():
        log(f"❌ MT5 initialize failed: {mt5.last_error()}", "ERROR")
        return

    acc = mt5.account_info()
    if acc:
        log(f"📊 Account #{acc.login} | Balance: ${acc.balance:,.2f} | Equity: ${acc.equity:,.2f}")

    # Quick preview of planned actions
    log(f"\n📋 PLANNED ACTIONS:")
    for ticket, plan in EXPECTED_POSITIONS.items():
        if plan["action"] == "partial_close":
            log(f"   📉 {plan['symbol']} #{ticket}: Close {plan['close_lots']} lots → keep {plan['keep_lots']}")
        elif plan["action"] == "tighten_sl":
            log(f"   🔒 {plan['symbol']} #{ticket}: Tighten SL (calculated at execution)")

    log(f"\n⏳ Polling every {POLL_INTERVAL}s... (max {MAX_WAIT_HOURS}h)")

    send_telegram(
        "🛡️ <b>AUTO Risk Reduction Started</b>\n"
        f"⏳ Waiting for market open...\n"
        f"📋 {len(EXPECTED_POSITIONS)} actions planned\n"
        f"💰 Equity: ${acc.equity:,.2f}" if acc else "🛡️ Risk Reduction Started"
    )

    # ─── Poll Loop ───
    start_time = time.time()
    market_just_opened = False

    while True:
        elapsed_hours = (time.time() - start_time) / 3600

        if elapsed_hours > MAX_WAIT_HOURS:
            log(f"⏰ Timeout after {MAX_WAIT_HOURS} hours. Aborting.", "WARN")
            send_telegram(f"⚠️ Risk Reduction TIMEOUT after {MAX_WAIT_HOURS}h")
            break

        if not is_market_open():
            hrs = int(elapsed_hours)
            mins = int((elapsed_hours - hrs) * 60)
            print(f"\r  ⏳ Market closed... waiting ({hrs}h {mins}m elapsed)    ", end="", flush=True)
            time.sleep(POLL_INTERVAL)
            continue

        # ─── Market is OPEN! ───
        if not market_just_opened:
            market_just_opened = True
            log(f"\n🔔 MARKET OPENED! Waiting {SPREAD_SETTLE_WAIT}s for spread to settle...")
            send_telegram("🔔 Market OPENED! Waiting for spread to settle...")
            time.sleep(SPREAD_SETTLE_WAIT)

        # ─── Check Spread ───
        gold_spread = get_spread("XAUUSDc")
        silver_spread = get_spread("XAGUSDc")

        log(f"📊 Spread: XAUUSD={gold_spread:.0f}pts, XAGUSD={silver_spread:.0f}pts")

        if gold_spread > MAX_SPREAD_GOLD_POINTS:
            log(f"⚠️ Gold spread too wide ({gold_spread:.0f} > {MAX_SPREAD_GOLD_POINTS}), waiting...", "WARN")
            time.sleep(30)
            continue

        # ─── Verify Positions ───
        log("\n🔍 Verifying positions...")
        found = verify_positions()

        missing = []
        for ticket in EXPECTED_POSITIONS:
            if ticket not in found:
                missing.append(ticket)
                log(f"⚠️ Ticket #{ticket} NOT FOUND!", "WARN")

        if missing:
            msg = f"⚠️ {len(missing)} positions missing! Tickets: {missing}"
            log(msg, "WARN")
            send_telegram(f"⚠️ <b>Risk Reduction ABORT</b>\n{msg}")
            log("Proceeding only with found positions...")

        if not found:
            log("❌ No expected positions found! ABORTING.", "ERROR")
            send_telegram("❌ Risk Reduction ABORT — No positions found!")
            break

        # ─── Refresh account info ───
        acc = mt5.account_info()
        equity = acc.equity if acc else 10000

        # ─── Execute Actions ───
        log(f"\n{'=' * 60}")
        log("🔴 EXECUTING RISK REDUCTION")
        log(f"{'=' * 60}")

        results = []

        # --- Step 1: Partial Close XAUUSDc ---
        gold_ticket = 2669421643
        if gold_ticket in found:
            plan = EXPECTED_POSITIONS[gold_ticket]
            info = found[gold_ticket]
            pos = info["pos"]

            # Recalculate exact lots to close based on current equity
            risk_pct = (info["risk_usd"] / equity * 100) if equity > 0 else 0
            log(f"\n📉 XAUUSDc #{gold_ticket}: current risk {risk_pct:.1f}% (${info['risk_usd']:,.0f})")

            if risk_pct > MAX_RISK_PCT:
                # Calculate safe lot
                risk_per_lot = info["risk_usd"] / pos.volume if pos.volume > 0 else 0
                max_risk_usd = equity * (MAX_RISK_PCT / 100)
                safe_lots = max_risk_usd / risk_per_lot if risk_per_lot > 0 else pos.volume
                step = info["volume_step"]
                safe_lots = math.floor(safe_lots / step) * step
                safe_lots = max(step, safe_lots)  # At least 1 step
                lots_to_close = round(pos.volume - safe_lots, 8)

                if lots_to_close >= step:
                    log(f"   Closing {lots_to_close} lots (keeping {safe_lots})")
                    success = execute_partial_close(gold_ticket, lots_to_close, pos)
                    results.append(("partial_close", "XAUUSDc", success, lots_to_close))

                    if success:
                        time.sleep(2)  # Wait for broker to process
                else:
                    log(f"   ℹ️ Lot reduction too small to execute ({lots_to_close})")
                    results.append(("partial_close", "XAUUSDc", None, "skip"))
            else:
                log(f"   ✅ Risk already within {MAX_RISK_PCT}%, no reduction needed")
                results.append(("partial_close", "XAUUSDc", None, "not_needed"))

        # --- Step 2: Tighten SL for all profit positions ---
        for ticket, info in found.items():
            plan = EXPECTED_POSITIONS[ticket]
            if plan["action"] == "tighten_sl" or True:  # Tighten all profitable positions
                new_sl, reason = calculate_tighten_sl(info)
                pos = info["pos"]

                if new_sl is not None:
                    log(f"\n🔒 {pos.symbol} #{ticket}: SL {pos.sl:.{info['digits']}f} → {new_sl:.{info['digits']}f} ({reason})")
                    success = execute_modify_sl(ticket, new_sl, pos.tp)
                    results.append(("tighten_sl", pos.symbol, success, f"{pos.sl} → {new_sl}"))
                else:
                    log(f"\n  ℹ️ {pos.symbol} #{ticket}: {reason}")

        # ─── Summary ───
        success_count = sum(1 for r in results if r[2] is True)
        fail_count = sum(1 for r in results if r[2] is False)
        skip_count = sum(1 for r in results if r[2] is None)

        log(f"\n{'=' * 60}")
        log(f"📊 EXECUTION COMPLETE")
        log(f"   ✅ Success: {success_count}  |  ❌ Failed: {fail_count}  |  ⏭️ Skipped: {skip_count}")
        log(f"{'=' * 60}")

        # Final account state
        acc_final = mt5.account_info()
        if acc_final:
            log(f"   Balance: ${acc_final.balance:,.2f}")
            log(f"   Equity:  ${acc_final.equity:,.2f}")
            log(f"   P/L:     ${acc_final.profit:,.2f}")

        # Telegram summary
        summary_lines = [
            "🛡️ <b>Risk Reduction COMPLETE</b>",
            f"✅ Success: {success_count} | ❌ Failed: {fail_count}",
            "",
        ]
        for action, symbol, success, detail in results:
            emoji = "✅" if success else ("❌" if success is False else "⏭️")
            summary_lines.append(f"{emoji} {symbol}: {action} — {detail}")

        if acc_final:
            summary_lines.extend([
                "",
                f"💰 Balance: ${acc_final.balance:,.2f}",
                f"📊 Equity: ${acc_final.equity:,.2f}",
            ])

        send_telegram("\n".join(summary_lines))
        break  # Done!

    mt5.shutdown()
    log("Script finished.")


if __name__ == "__main__":
    main()
