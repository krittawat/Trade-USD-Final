# -*- coding: utf-8 -*-
"""
self_improve_loop.py — AI Self-Improvement Daemon (Autonomous Backtest & Evolution)

ทำงาน:
    1. รอบแรก: รัน Training Session ทันที (ทุก Symbol)
    2. รอ 6 ชั่วโมง (หรือตาม --interval ที่กำหนด)
    3. รัน Training Session อีกครั้ง → Repeat ตลอด

แต่ละ Training Session:
    ├─ Tournament: ทุก strategy แข่งกันบน 80% ข้อมูล
    ├─ Evolve: Genetic Algorithm ปรับ params ของ Top 5
    ├─ Validate: ทดสอบ params ใหม่บน 20% ที่เหลือ
    └─ Save: บันทึก best params ลง SQLite Memory

Symbols: BTCUSD, XAUUSD, XAGUSD, USOILm, US30m, USTECm
Timeframe: M5 (2000 bars ≈ 7 วัน)

Usage:
    python -m scripts.self_improve_loop
    python -m scripts.self_improve_loop --interval 3  (ทุก 3 ชั่วโมง)
    python -m scripts.self_improve_loop --once        (รันครั้งเดียวแล้วหยุด)
"""

import asyncio
import argparse
import time
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.core.config import get_settings
from app.db.sqlite import SQLiteStore
from app.brain.memory_store import MemoryStore
from app.brain.training_orchestrator import TrainingOrchestrator
from app.strategy.factory import StrategyFactory
from app.mt5.client import MT5Client

logger = get_logger("self_improve_loop")

# ─── Default Config ──────────────────────────────────────────────────────────
DEFAULT_INTERVAL_HOURS = 6
SYMBOLS = ["BTCUSD", "XAUUSD", "XAGUSD", "USOILm", "US30m", "USTECm"]


# ─── Banner ──────────────────────────────────────────────────────────────────
def _print_banner(interval_hours: float):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║   🧠 ANTIGRAVITY AI — SELF-IMPROVEMENT DAEMON                ║
║   Symbols : {", ".join(SYMBOLS)}
║   Interval: Every {interval_hours}h  (Tournament → Evolve → Validate)       ║
║   Started : {ts}                              ║
╚══════════════════════════════════════════════════════════════╝
""")


# ─── Init Components ─────────────────────────────────────────────────────────
def _init_components():
    """สร้าง components ที่จำเป็น"""
    settings = get_settings()
    mt5 = MT5Client(settings=settings)

    db = SQLiteStore(settings=settings)
    db.connect()

    memory = MemoryStore()
    memory.connect()

    factory = StrategyFactory()
    factory.auto_register(db=db)

    orchestrator = TrainingOrchestrator(
        factory=factory,
        memory_store=memory,
        mt5_client=mt5,
        settings=settings,
    )
    return orchestrator, mt5, settings


# ─── Single Training Run ──────────────────────────────────────────────────────
async def _run_training_session(orchestrator: TrainingOrchestrator, session_num: int):
    """รัน 1 รอบ Training Session พร้อม log สรุป"""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info("self_improve_session_start", extra={
        "session": session_num,
        "ts": ts,
    })
    print(f"\n{'='*60}")
    print(f"  📚 Training Session #{session_num} | {ts}")
    print(f"{'='*60}")

    try:
        report = await orchestrator.run_training_session()

        # ─── Summary ──────────────────────────────────────────────
        duration_min = round(report.duration_seconds / 60, 1)
        symbols_done = len(report.symbols_trained)
        strategies_tested = report.strategies_tested
        improvements = {k: v for k, v in report.improvements.items() if v}

        print(f"  ✅ Session #{session_num} Complete!")
        print(f"  ⏱️  Duration   : {duration_min} min")
        print(f"  📊 Symbols    : {symbols_done}/{len(SYMBOLS)} trained")
        print(f"  🎯 Strategies : {strategies_tested} tested")
        print(f"  🧬 Evolved    : {len(improvements)} symbols improved")

        if improvements:
            print("\n  📈 Improvements:")
            for sym, imp in improvements.items():
                print(f"     {sym:12s} → score +{imp:.4f}")

        if report.best_performers:
            best = report.best_performers[0]
            print(f"\n  🏆 Best Performer: [{best.strategy_name}] on {best.symbol}")
            print(f"     WR={best.win_rate:.1%} | PF={best.profit_factor:.2f} | MDD={best.max_drawdown:.1f}%")

        logger.info("self_improve_session_complete", extra={
            "session": session_num,
            "symbols": symbols_done,
            "strategies_tested": strategies_tested,
            "improvements": len(improvements),
            "duration_min": duration_min,
        })

        # ─── AI Self-Correction Check (Blueprint Update 2) ────────
        if report.best_performers:
            best = report.best_performers[0]
            if best.max_drawdown > 20.0:
                logger.warning("ai_self_correction_triggered", extra={
                    "symbol": best.symbol,
                    "strategy": best.strategy_name,
                    "max_dd": best.max_drawdown,
                })
                print(f"\n  ⚠️  AI SELF-CORRECTION: {best.symbol} MDD={best.max_drawdown:.1f}% > 20%")
                print("     → Suggestions logged. Evolver will prioritize tighter params next cycle.")

        return report

    except Exception as e:
        logger.error("self_improve_session_error", extra={
            "session": session_num,
            "error": str(e),
        }, exc_info=True)
        print(f"  ❌ Session #{session_num} failed: {e}")
        return None


# ─── Main Loop ────────────────────────────────────────────────────────────────
async def main(interval_hours: float = DEFAULT_INTERVAL_HOURS, run_once: bool = False):
    _print_banner(interval_hours)

    try:
        orchestrator, mt5, settings = _init_components()
    except Exception as e:
        logger.error("init_failed", extra={"error": str(e)}, exc_info=True)
        print(f"❌ Init failed: {e}")
        return

    # Connect MT5
    try:
        if not mt5.connect():
            print("⚠️  MT5 not connected — training will use cached candles only")
        else:
            print("✅ MT5 connected")
    except Exception as e:
        print(f"⚠️  MT5 connect error: {e}")

    session_num = 0
    interval_seconds = interval_hours * 3600

    while True:
        session_num += 1

        await _run_training_session(orchestrator, session_num)

        if run_once:
            print("\n✅ --once mode: done.")
            break

        # ─── ประกาศเวลารอบถัดไป ───────────────────────────────────
        next_run = time.time() + interval_seconds
        next_ts = datetime.fromtimestamp(next_run).strftime("%H:%M:%S")
        print(f"\n  ⏳ Next training session at {next_ts} (in {interval_hours}h)")
        print(f"     Sleeping... (Ctrl+C to stop)")

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            print("\n🛑 Self-improve loop cancelled.")
            break


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ANTIGRAVITY AI Self-Improvement Daemon"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_HOURS,
        help=f"ชั่วโมงระหว่างรอบ training (default: {DEFAULT_INTERVAL_HOURS}h)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="รันครั้งเดียวแล้วหยุด",
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(interval_hours=args.interval, run_once=args.once))
    except KeyboardInterrupt:
        print("\n\n🛑 Self-Improve Daemon stopped by user.")
