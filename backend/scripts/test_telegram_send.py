"""ทดสอบส่งข้อความ Telegram จริง."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.services.telegram import TelegramNotifier


async def main():
    s = get_settings()
    print(f"Token: {s.telegram_bot_token[:10]}..." if s.telegram_bot_token else "Token: EMPTY!")
    print(f"Chat ID: {s.telegram_chat_id}")
    print(f"Enabled: {s.telegram_enabled}")

    t = TelegramNotifier(s.telegram_bot_token, s.telegram_chat_id, s.telegram_enabled)
    print(f"Notifier enabled: {t.enabled}")

    if not t.enabled:
        print("❌ Telegram disabled — ตรวจ TELEGRAM_BOT_TOKEN และ TELEGRAM_CHAT_ID ใน .env")
        return

    print("\nส่งข้อความทดสอบ...")
    await t.notify_bot_start(
        mode="TEST",
        symbols=["XAUUSDc", "EURUSDc", "GBPUSDc"],
        strategies_count=15,
        equity=100.0,
    )
    print("✅ ส่งสำเร็จ! ตรวจ Telegram ของคุณครับ")
    await t.close()


if __name__ == "__main__":
    asyncio.run(main())
