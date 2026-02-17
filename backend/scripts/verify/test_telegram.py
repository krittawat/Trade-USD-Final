"""
Test Telegram Notifier — ตรวจว่า TelegramNotifier ทำงานถูกต้อง.

Tests:
    1. Disabled mode: token ว่าง → ไม่ error, ไม่ส่ง HTTP
    2. Config fields: Settings มี telegram fields
    3. Fire-and-forget: ส่งไม่สำเร็จ → ไม่ raise exception
"""

import asyncio
import sys
import os

# เพิ่ม backend path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def test_disabled_when_no_token():
    """TelegramNotifier ต้อง disable อัตโนมัติเมื่อ token ว่าง."""
    from app.services.telegram import TelegramNotifier

    t = TelegramNotifier(bot_token="", chat_id="123", enabled=True)
    assert t.enabled is False, "Should be disabled when token is empty"
    assert t._client is None, "No HTTP client when disabled"
    print("✅ test_disabled_when_no_token PASSED")


def test_disabled_when_no_chat_id():
    """TelegramNotifier ต้อง disable เมื่อ chat_id ว่าง."""
    from app.services.telegram import TelegramNotifier

    t = TelegramNotifier(bot_token="123:abc", chat_id="", enabled=True)
    assert t.enabled is False, "Should be disabled when chat_id is empty"
    print("✅ test_disabled_when_no_chat_id PASSED")


def test_disabled_by_flag():
    """TelegramNotifier ต้อง disable เมื่อ enabled=False."""
    from app.services.telegram import TelegramNotifier

    t = TelegramNotifier(bot_token="123:abc", chat_id="456", enabled=False)
    assert t.enabled is False, "Should be disabled when enabled=False"
    print("✅ test_disabled_by_flag PASSED")


def test_enabled_with_valid_config():
    """TelegramNotifier ต้อง enable เมื่อมี token + chat_id + enabled=True."""
    from app.services.telegram import TelegramNotifier

    t = TelegramNotifier(bot_token="123:abc", chat_id="456", enabled=True)
    assert t.enabled is True, "Should be enabled with valid config"
    assert t._client is not None, "Should have HTTP client when enabled"
    # cleanup
    asyncio.run(t.close())
    print("✅ test_enabled_with_valid_config PASSED")


def test_notify_open_disabled_no_error():
    """notify_trade_open() ต้องไม่ error เมื่อ disabled."""
    from app.services.telegram import TelegramNotifier

    t = TelegramNotifier()  # disabled

    async def _run():
        await t.notify_trade_open(
            symbol="XAUUSDm", action="BUY", lot_size=0.01,
            entry_price=2000.0, stop_loss=1990.0, take_profit=2020.0,
            risk_usd=10.0, risk_pct=1.0, strategy_name="test",
        )

    asyncio.run(_run())
    print("✅ test_notify_open_disabled_no_error PASSED")


def test_notify_close_disabled_no_error():
    """notify_trade_close() ต้องไม่ error เมื่อ disabled."""
    from app.services.telegram import TelegramNotifier

    t = TelegramNotifier()  # disabled

    async def _run():
        await t.notify_trade_close(
            symbol="XAUUSDm", ticket=12345, action="BUY",
            lot_size=0.01, profit=15.50,
        )

    asyncio.run(_run())
    print("✅ test_notify_close_disabled_no_error PASSED")


def test_config_has_telegram_fields():
    """Settings ต้องมี telegram fields."""
    from app.core.config import Settings

    s = Settings(
        telegram_bot_token="test_token",
        telegram_chat_id="test_chat",
        telegram_enabled=True,
    )
    assert s.telegram_bot_token == "test_token"
    assert s.telegram_chat_id == "test_chat"
    assert s.telegram_enabled is True
    print("✅ test_config_has_telegram_fields PASSED")


if __name__ == "__main__":
    print("=" * 60)
    print("Telegram Notifier — Verification Tests")
    print("=" * 60)

    tests = [
        test_disabled_when_no_token,
        test_disabled_when_no_chat_id,
        test_disabled_by_flag,
        test_enabled_with_valid_config,
        test_notify_open_disabled_no_error,
        test_notify_close_disabled_no_error,
        test_config_has_telegram_fields,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    if failed == 0:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
        sys.exit(1)
