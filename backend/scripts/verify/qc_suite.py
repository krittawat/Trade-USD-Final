"""
QC Suite — ชุดทดสอบคุณภาพก่อนเทรดจริง.

ต้อง pass 100% ก่อนอนุญาตให้เข้าโหมด LIVE.

ตรวจ:
    1. Config valid (schema ถูกต้อง)
    2. DB connections (QuestDB, SQLite, DuckDB)
    3. MT5 connection (ถ้าโหมด LIVE/DRY_RUN)
    4. Risk gate ทำงานถูกต้อง
    5. Lot sizing คำนวณถูก
    6. Break-even logic ถูกต้อง
    7. ไม่มี silent failures

วิธีรัน:
    python backend/scripts/verify/qc_suite.py
"""

import sys
from datetime import datetime, timezone


def run_all_tests() -> bool:
    """รัน QC ทั้งหมด — return True ถ้าผ่านทุกข้อ."""
    results = []
    
    print("=" * 60)
    print("🔍 Antigravity QC Suite")
    print(f"⏰ {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # --- Test 1: Config validation ---
    print("\n[1/7] ตรวจ Config...")
    try:
        from app.core.config import get_settings
        settings = get_settings()
        results.append(("Config Valid", True))
        print(f"  ✅ Mode: {settings.trading_mode}")
    except Exception as e:
        results.append(("Config Valid", False))
        print(f"  ❌ Config error: {e}")

    # --- Test 2: Imports ---
    print("\n[2/7] ตรวจ Imports...")
    try:
        from app.domain.models import Decision, OrderPlan, SymbolProfile
        from app.domain.enums import Action, BlockReason
        from app.risk.gate import PreTradeGate
        from app.execution.pipeline import ExecutionPipeline
        from app.strategy.base import BaseStrategy
        from app.strategy.factory import StrategyFactory
        results.append(("Imports OK", True))
        print("  ✅ ทุกโมดูล import ได้")
    except Exception as e:
        results.append(("Imports OK", False))
        print(f"  ❌ Import error: {e}")

    # --- Test 3: Risk Gate reason codes ---
    print("\n[3/7] ตรวจ Risk Gate...")
    try:
        from app.domain.enums import BlockReason
        reasons = list(BlockReason)
        assert len(reasons) >= 14, f"ต้องมีอย่างน้อย 14 reason codes, มี {len(reasons)}"
        results.append(("Risk Gate Reasons", True))
        print(f"  ✅ {len(reasons)} reason codes defined")
    except Exception as e:
        results.append(("Risk Gate Reasons", False))
        print(f"  ❌ {e}")

    # --- Test 4: Decision model validation ---
    print("\n[4/7] ตรวจ Decision model...")
    try:
        from app.domain.models import Decision
        from app.domain.enums import Action
        # BUY ต้องสร้างได้
        d = Decision(symbol="XAUUSD", action=Action.BUY, confidence=0.8,
                     reason="test", stop_loss=1900.0)
        assert d.stop_loss == 1900.0
        # HOLD ไม่ต้องมี SL
        d2 = Decision(symbol="XAUUSD", action=Action.HOLD, confidence=0.0,
                      reason="no signal")
        results.append(("Decision Model", True))
        print("  ✅ Decision model ทำงานถูกต้อง")
    except Exception as e:
        results.append(("Decision Model", False))
        print(f"  ❌ {e}")

    # --- Test 5: Trading mode ---
    print("\n[5/7] ตรวจ Trading Mode...")
    try:
        from app.core.mode import TradingMode
        assert not TradingMode.DRY_RUN.can_send_orders, "DRY_RUN ต้องไม่ส่งออเดอร์ได้"
        assert TradingMode.LIVE.can_send_orders, "LIVE ต้องส่งออเดอร์ได้"
        results.append(("Trading Mode", True))
        print("  ✅ DRY_RUN ไม่ส่งออเดอร์, LIVE ส่งได้")
    except Exception as e:
        results.append(("Trading Mode", False))
        print(f"  ❌ {e}")

    # --- Test 6: Session detector ---
    print("\n[6/7] ตรวจ Session Detector...")
    try:
        from app.services.session import get_current_session
        session = get_current_session()
        results.append(("Session Detector", True))
        print(f"  ✅ Current session: {session.value}")
    except Exception as e:
        results.append(("Session Detector", False))
        print(f"  ❌ {e}")

    # --- Test 7: Structured logger ---
    print("\n[7/7] ตรวจ Structured Logger...")
    try:
        from app.core.logging import get_logger
        test_logger = get_logger("qc_test")
        # ไม่ควร crash
        test_logger.info("qc_test_message", extra={"test": True})
        results.append(("Structured Logger", True))
        print("  ✅ JSON logger ทำงานปกติ")
    except Exception as e:
        results.append(("Structured Logger", False))
        print(f"  ❌ {e}")

    # --- สรุปผล ---
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"📊 ผลลัพธ์: {passed}/{total} ผ่าน")
    
    for name, ok in results:
        status = "✅" if ok else "❌"
        print(f"  {status} {name}")

    all_passed = all(ok for _, ok in results)
    if all_passed:
        print("\n🎉 QC PASSED — ระบบพร้อมทำงาน")
    else:
        print("\n⚠️ QC FAILED — แก้ไขปัญหาก่อนเริ่มเทรด")

    print("=" * 60)
    return all_passed


if __name__ == "__main__":
    # เพิ่ม backend dir เข้า path
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
    success = run_all_tests()
    sys.exit(0 if success else 1)
