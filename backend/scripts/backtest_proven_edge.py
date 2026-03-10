"""
Walk-Forward Backtest — ทดสอบ Gold Proven Edge อย่างเข้มงวด.

ทำไมต้อง Walk-Forward:
    - In-Sample (IS): 67% แรก → ใช้หาพารามิเตอร์
    - Out-of-Sample (OOS): 33% หลัง → ใช้ตรวจว่ากำไรจริงไหม
    - ถ้า OOS ใกล้เคียง IS = กลยุทธ์มี edge จริง
    - ถ้า OOS แย่กว่า IS มาก = curve-fitted → ใช้ไม่ได้

Backtester ใช้แบบ standalone (ไม่ผ่าน safety modules):
    - ทดสอบ edge ของ strategy อย่างบริสุทธิ์
    - Safety modules (regime, cooldown, session) เป็นเรื่องของ live pipeline
    - ข้อดี: เห็นว่า strategy signal มี edge จริงหรือไม่

ใช้งาน:
    cd d:\\VibeCode\\Trade\\backend
    python scripts/backtest_proven_edge.py
"""
print("Gold Proven Edge -- Walk-Forward Backtest starting...", flush=True)

import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, field

# ปิด log ที่ไม่จำเป็น
logging.disable(logging.WARNING)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

# ═════════════════════════════════════════════
# ตั้งค่า
# ═════════════════════════════════════════════

SYMBOL = "XAUUSDc"          # Gold cent
CONTRACT_SIZE = 100.0        # contract size ของ Gold
POINT = 0.01                 # minimum price step
INITIAL_EQUITY = 10000.0     # ทุนเริ่มต้น
RISK_PER_TRADE = 0.02        # 2% risk ต่อเทรด
COMMISSION_PER_LOT = 5.0     # ค่า commission $5/lot
SLIPPAGE_DOLLAR = 0.10       # slippage $0.10

DAYS_TOTAL = 365             # ดึงข้อมูล 1 ปี
IS_RATIO = 0.67              # In-Sample = 67%
TIMEFRAME_STR = "M5"         # ใช้ M5
MIN_OOS_TRADES = 15          # OOS ต้องมีอย่างน้อย 15 เทรด


# ═════════════════════════════════════════════
# Standalone Backtester — ทดสอบ edge บริสุทธิ์
# ═════════════════════════════════════════════

@dataclass
class Trade:
    """เทรด 1 รายการ."""
    action: str          # BUY / SELL
    entry_price: float
    entry_time: str
    sl: float
    tp: float
    lot: float
    exit_price: float = 0.0
    exit_time: str = ""
    exit_reason: str = ""  # SL / TP / END
    profit_usd: float = 0.0
    bars_held: int = 0


@dataclass
class BacktestOutput:
    """ผลลัพธ์ backtest."""
    label: str
    total_trades: int = 0
    winning: int = 0
    losing: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    max_dd_pct: float = 0.0
    expectancy: float = 0.0
    avg_rr: float = 0.0
    start_date: str = ""
    end_date: str = ""
    bars: int = 0
    trades: list = field(default_factory=list)


def run_pure_backtest(candles: pd.DataFrame, label: str) -> BacktestOutput:
    """
    Backtest แบบบริสุทธิ์ — ไม่มี safety modules.

    กลไก:
        1. วนแท่งเทียนทีละแท่ง
        2. ถ้ามี open trade → ตรวจ SL/TP hit
        3. ถ้าไม่มี open trade → ถาม strategy
        4. ถ้า strategy ให้ BUY/SELL → เปิดเทรดด้วย risk-based lot
        5. คำนวณผลลัพธ์

    Args:
        candles: DataFrame ที่มี OHLCV + time
        label: ชื่อ segment

    Returns:
        BacktestOutput
    """
    from app.strategy.templates.gold_proven_edge import GoldProvenEdge
    from app.domain.models import SymbolProfile
    from app.domain.enums import Action

    strategy = GoldProvenEdge()
    profile = SymbolProfile(
        symbol=SYMBOL, contract_size=CONTRACT_SIZE, point=POINT,
        digits=2, volume_min=0.01, volume_max=100.0, volume_step=0.01,
    )

    # ─── State ───
    equity = INITIAL_EQUITY
    peak_equity = equity
    max_dd_pct = 0.0

    open_trade: Trade | None = None
    closed_trades: list[Trade] = []

    warmup = 50
    total_bars = len(candles)

    for i in range(warmup, total_bars):
        bar = candles.iloc[i]
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])
        bar_close = float(bar["close"])
        bar_time = str(bar.get("time", i))

        # ─── ตรวจ open trade ───
        if open_trade is not None:
            open_trade.bars_held += 1
            exited = False

            if open_trade.action == "BUY":
                # SL hit ก่อน TP (worst case)
                if open_trade.sl > 0 and bar_low <= open_trade.sl:
                    open_trade.exit_price = open_trade.sl
                    open_trade.exit_reason = "SL"
                    exited = True
                elif open_trade.tp > 0 and bar_high >= open_trade.tp:
                    open_trade.exit_price = open_trade.tp
                    open_trade.exit_reason = "TP"
                    exited = True
            else:  # SELL
                if open_trade.sl > 0 and bar_high >= open_trade.sl:
                    open_trade.exit_price = open_trade.sl
                    open_trade.exit_reason = "SL"
                    exited = True
                elif open_trade.tp > 0 and bar_low <= open_trade.tp:
                    open_trade.exit_price = open_trade.tp
                    open_trade.exit_reason = "TP"
                    exited = True

            if exited:
                open_trade.exit_time = bar_time
                # คำนวณกำไร
                if open_trade.action == "BUY":
                    raw_pnl = (open_trade.exit_price - open_trade.entry_price) * open_trade.lot * CONTRACT_SIZE
                else:
                    raw_pnl = (open_trade.entry_price - open_trade.exit_price) * open_trade.lot * CONTRACT_SIZE

                # หัก commission
                commission = COMMISSION_PER_LOT * open_trade.lot
                open_trade.profit_usd = raw_pnl - commission
                equity += open_trade.profit_usd
                closed_trades.append(open_trade)
                open_trade = None

        # ─── Drawdown tracking ───
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
        if dd > max_dd_pct:
            max_dd_pct = dd

        # ─── ถาม strategy (เฉพาะตอนไม่มี position) ───
        if open_trade is None:
            window_start = max(0, i - 299)
            history = candles.iloc[window_start:i + 1]

            if len(history) < 50:
                continue

            try:
                decision = strategy.analyze(history, profile)
            except Exception:
                continue

            if decision.action in (Action.BUY, Action.SELL):
                action_str = decision.action.value

                # ─── Risk-based lot sizing ───
                sl_dist = abs(bar_close - decision.stop_loss) if decision.stop_loss else 0
                if sl_dist <= 0:
                    continue

                risk_usd = equity * RISK_PER_TRADE
                lot = risk_usd / (sl_dist * CONTRACT_SIZE)
                lot = max(0.01, min(lot, 10.0))
                lot = round(lot, 2)

                # ─── Slippage ───
                entry = bar_close + (SLIPPAGE_DOLLAR if action_str == "BUY" else -SLIPPAGE_DOLLAR)

                open_trade = Trade(
                    action=action_str,
                    entry_price=entry,
                    entry_time=bar_time,
                    sl=decision.stop_loss,
                    tp=decision.take_profit or 0.0,
                    lot=lot,
                )

    # ─── ปิดเทรดค้าง ───
    if open_trade is not None:
        last_close = float(candles.iloc[-1]["close"])
        if open_trade.action == "BUY":
            raw_pnl = (last_close - open_trade.entry_price) * open_trade.lot * CONTRACT_SIZE
        else:
            raw_pnl = (open_trade.entry_price - last_close) * open_trade.lot * CONTRACT_SIZE
        commission = COMMISSION_PER_LOT * open_trade.lot
        open_trade.profit_usd = raw_pnl - commission
        open_trade.exit_price = last_close
        open_trade.exit_reason = "END"
        open_trade.exit_time = str(candles.iloc[-1].get("time", total_bars))
        equity += open_trade.profit_usd
        closed_trades.append(open_trade)

    # ─── คำนวณสถิติ ───
    result = BacktestOutput(label=label)
    result.total_trades = len(closed_trades)
    result.bars = total_bars

    if result.total_trades > 0:
        result.start_date = closed_trades[0].entry_time[:10] if closed_trades else ""
        result.end_date = closed_trades[-1].exit_time[:10] if closed_trades else ""

        gross_profit = sum(t.profit_usd for t in closed_trades if t.profit_usd > 0)
        gross_loss = abs(sum(t.profit_usd for t in closed_trades if t.profit_usd < 0))

        result.winning = sum(1 for t in closed_trades if t.profit_usd > 0)
        result.losing = sum(1 for t in closed_trades if t.profit_usd <= 0)
        result.win_rate = round(result.winning / result.total_trades * 100, 1)
        result.profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0
        result.total_pnl = round(sum(t.profit_usd for t in closed_trades), 2)
        result.max_dd_pct = round(max_dd_pct, 1)
        result.expectancy = round(result.total_pnl / result.total_trades, 2)
        result.trades = closed_trades

        # เฉลี่ย RR
        rrs = []
        for t in closed_trades:
            sl_d = abs(t.entry_price - t.sl) if t.sl > 0 else 1.0
            actual_move = abs(t.exit_price - t.entry_price)
            rrs.append(round(actual_move / sl_d, 2) if sl_d > 0 else 0.0)
        result.avg_rr = round(sum(rrs) / len(rrs), 2) if rrs else 0.0
    else:
        result.start_date = str(candles["time"].iloc[warmup])[:10]
        result.end_date = str(candles["time"].iloc[-1])[:10]

    return result


def print_result(r: BacktestOutput, emoji: str = ">>"):
    """แสดงผลลัพธ์ในรูปแบบตาราง (ASCII only สำหรับ Windows)."""
    print(f"\n{emoji} {r.label}")
    print(f"   Period: {r.start_date} -> {r.end_date} ({r.bars:,} bars)")
    print(f"   +---------------------------------------------+")
    print(f"   | Trades       : {r.total_trades:>6}                        |")
    print(f"   | Win / Lose   : {r.winning:>4} / {r.losing:<4}                    |")
    print(f"   | Win Rate     : {r.win_rate:>6.1f}%                     |")
    print(f"   | Profit Factor: {r.profit_factor:>6.2f}                      |")
    print(f"   | P&L          : ${r.total_pnl:>+10.2f}                |")
    print(f"   | Max Drawdown : {r.max_dd_pct:>6.1f}%                     |")
    print(f"   | Expectancy   : ${r.expectancy:>+8.2f}/trade             |")
    print(f"   | Avg R:R      : {r.avg_rr:>6.2f}                      |")
    print(f"   +---------------------------------------------+")

    # แสดงรายการเทรด
    if r.trades:
        print(f"\n   Trade Log:")
        print(f"   {'#':>3} | {'Action':>4} | {'Entry':>10} | {'Exit':>10} | {'SL':>10} | {'TP':>10} | {'P&L':>10} | {'Bars':>4} | {'Exit':>5}")
        print(f"   {'-'*3}-+-{'-'*4}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*4}-+-{'-'*5}")
        for idx, t in enumerate(r.trades[:30], 1):  # แสดงแค่ 30 เทรดแรก
            print(f"   {idx:>3} | {t.action:>4} | {t.entry_price:>10.2f} | {t.exit_price:>10.2f} | {t.sl:>10.2f} | {t.tp:>10.2f} | ${t.profit_usd:>+8.2f} | {t.bars_held:>4} | {t.exit_reason:>5}")
        if len(r.trades) > 30:
            print(f"   ... +{len(r.trades) - 30} more trades")


def evaluate_pass_fail(oos: BacktestOutput) -> bool:
    """ตรวจเกณฑ์ผ่านหรือไม่."""
    checks = {
        "PF >= 1.3": oos.profit_factor >= 1.3,
        "WR >= 45%": oos.win_rate >= 45.0,
        "DD <= 15%": oos.max_dd_pct <= 15.0,
        f"Trades >= {MIN_OOS_TRADES}": oos.total_trades >= MIN_OOS_TRADES,
        "Profit > 0": oos.total_pnl > 0,
    }

    print("\n" + "=" * 60)
    print("CHECK: Out-of-Sample Criteria")
    print("=" * 60)

    all_pass = True
    for check_name, passed in checks.items():
        icon = "PASS" if passed else "FAIL"
        print(f"   [{icon}] {check_name}")
        if not passed:
            all_pass = False

    print("=" * 60)
    if all_pass:
        print("[WINNER] Strategy has proven edge -- ready for DRY_RUN!")
    else:
        print("[NEEDS WORK] Not yet profitable -- tune parameters")
    print("=" * 60)

    return all_pass


def main():
    print("=" * 70)
    print("GOLD PROVEN EDGE -- Walk-Forward Backtest")
    print(f"   Strategy: Asian Range Breakout (London window)")
    print(f"   Symbol: {SYMBOL} | TF: {TIMEFRAME_STR}")
    print(f"   Data: {DAYS_TOTAL} days | IS/OOS: {IS_RATIO*100:.0f}%/{(1-IS_RATIO)*100:.0f}%")
    print(f"   Capital: ${INITIAL_EQUITY:,.0f} | Risk/trade: {RISK_PER_TRADE*100:.0f}%")
    print(f"   Commission: ${COMMISSION_PER_LOT}/lot | Slippage: ${SLIPPAGE_DOLLAR}")
    print("=" * 70)

    # --- MT5 Connect ---
    if not mt5.initialize():
        print("ERROR: MT5 init failed")
        return

    print("OK: MT5 connected")

    # --- Fetch Data ---
    tf_map = {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15, "H1": mt5.TIMEFRAME_H1,
    }
    mt5_tf = tf_map.get(TIMEFRAME_STR, mt5.TIMEFRAME_M5)

    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=DAYS_TOTAL)

    print(f"Fetching {SYMBOL} {TIMEFRAME_STR} for {DAYS_TOTAL} days...")
    rates = mt5.copy_rates_range(SYMBOL, mt5_tf, utc_from, utc_to)

    if rates is None or len(rates) == 0:
        print(f"ERROR: No data for {SYMBOL}")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    print(f"OK: {len(df):,} bars ({df['time'].iloc[0]} -> {df['time'].iloc[-1]})")

    # --- Split IS / OOS ---
    split_idx = int(len(df) * IS_RATIO)
    df_is = df.iloc[:split_idx].copy().reset_index(drop=True)
    df_oos = df.iloc[split_idx:].copy().reset_index(drop=True)

    print(f"\nData Split:")
    print(f"   IS  : {len(df_is):,} bars ({df_is['time'].iloc[0].date()} -> {df_is['time'].iloc[-1].date()})")
    print(f"   OOS : {len(df_oos):,} bars ({df_oos['time'].iloc[0].date()} -> {df_oos['time'].iloc[-1].date()})")

    # --- Run In-Sample ---
    print(f"\nRunning In-Sample ({len(df_is):,} bars)...")
    t0 = time.monotonic()
    is_result = run_pure_backtest(df_is, "IN-SAMPLE (training data)")
    print(f"   Done in {time.monotonic() - t0:.1f}s")
    print_result(is_result, "IS")

    # --- Run Out-of-Sample ---
    print(f"\nRunning Out-of-Sample ({len(df_oos):,} bars)...")
    t0 = time.monotonic()
    oos_result = run_pure_backtest(df_oos, "OUT-OF-SAMPLE (unseen data)")
    print(f"   Done in {time.monotonic() - t0:.1f}s")
    print_result(oos_result, "OOS")

    # --- Run Full Period ---
    print(f"\nRunning Full Period ({len(df):,} bars)...")
    t0 = time.monotonic()
    full_result = run_pure_backtest(df, "FULL PERIOD")
    print(f"   Done in {time.monotonic() - t0:.1f}s")
    print_result(full_result, "FULL")

    # --- Compare ---
    print("\n" + "=" * 70)
    print("COMPARE: In-Sample vs Out-of-Sample")
    print("=" * 70)
    print(f"   {'Metric':<20s} | {'IS':>10s} | {'OOS':>10s} | {'Diff':>10s}")
    print(f"   {'-'*20}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}")

    metrics = [
        ("Trades", is_result.total_trades, oos_result.total_trades),
        ("Win Rate %", is_result.win_rate, oos_result.win_rate),
        ("Profit Factor", is_result.profit_factor, oos_result.profit_factor),
        ("P&L $", is_result.total_pnl, oos_result.total_pnl),
        ("Max DD %", is_result.max_dd_pct, oos_result.max_dd_pct),
        ("Expectancy $", is_result.expectancy, oos_result.expectancy),
    ]
    for name, is_val, oos_val in metrics:
        diff = oos_val - is_val
        print(f"   {name:<20s} | {is_val:>10.2f} | {oos_val:>10.2f} | {diff:>+10.2f}")

    # --- Pass/Fail ---
    passed = evaluate_pass_fail(oos_result)

    mt5.shutdown()

    # --- Summary ---
    print("\n" + "=" * 70)
    if passed:
        print("RESULT: Gold Proven Edge PASSED Walk-Forward Validation!")
        print("   Next steps:")
        print("   1. Run DRY_RUN for 200+ trades")
        print("   2. Compare DRY_RUN vs backtest results")
        print("   3. If consistent -> Enable LIVE with minimum lot")
    else:
        print("RESULT: Strategy needs improvement")
        print("   Suggestions:")
        print("   1. Adjust BREAKOUT_BUFFER_DOLLAR")
        print("   2. Try RR_TARGET = 2.0")
        print("   3. Reduce MIN_BODY_RATIO")
        print("   4. Extend London window")
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        print("\nERROR: See traceback above")
