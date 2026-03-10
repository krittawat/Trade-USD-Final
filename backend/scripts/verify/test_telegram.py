import asyncio
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))

from app.core.config import get_settings
from app.services.telegram import TelegramNotifier

async def main():
    settings = get_settings()
    print(f"Testing Telegram Notification...")
    print(f"Token: {settings.telegram_bot_token[:5]}...{settings.telegram_bot_token[-5:] if settings.telegram_bot_token else 'NONE'}")
    print(f"Chat ID: {settings.telegram_chat_id}")
    print(f"Enabled: {settings.telegram_enabled}")

    notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        enabled=settings.telegram_enabled
    )

    if not notifier.enabled:
        print("❌ Telegram is disabled or config is missing.")
        return

    print("Sending localized test messages...", end=" ", flush=True)
    try:
        # Test 1: Bot Start
        await notifier.notify_bot_start(
            mode="LIVE",
            symbols=["XAUUSD", "EURUSD"],
            strategies_count=5,
            equity=10000.0
        )
        
        # Test 2: Trade Open
        await notifier.notify_trade_open(
            symbol="XAUUSD",
            action="BUY",
            lot_size=0.1,
            entry_price=2000.50,
            stop_loss=1990.00,
            take_profit=2020.00,
            risk_usd=105.00,
            risk_pct=1.0,
            strategy_name="GoldScalpPro",
            ticket=12345678,
            mode="LIVE"
        )
        
        # Test 3: Trade Close
        await notifier.notify_trade_close(
            symbol="XAUUSD",
            ticket=12345678,
            action="BUY",
            lot_size=0.1,
            profit=200.00,
            entry_price=2000.50,
            close_price=2020.50,
            reason="TP Hit",
            mode="LIVE"
        )

        print("✅ Messages sent (check your Telegram for Thai text).")
    except Exception as e:
        print(f"❌ Failed: {e}")
    finally:
        await notifier.close()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
