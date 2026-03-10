import asyncio
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), '../../'))

from app.core.config import get_settings
from app.services.telegram import TelegramNotifier
from app.mt5.client import MT5Client

async def main():
    settings = get_settings()
    print("🚀 Connecting to MT5 and Telegram...")

    # 1. Connect MT5
    mt5 = MT5Client(settings)
    if not mt5.connect():
        print("❌ MT5 Connection Failed!")
        return
    
    # 2. Get Account & Positions
    try:
        account = mt5.get_account_state()
        positions = mt5.get_positions()
        print(f"✅ MT5 Connected. Equity: {account.real_equity:,.2f} {account.currency}, Positions: {len(positions)}")
    except Exception as e:
        print(f"❌ Error fetching data: {e}")
        return
    finally:
         mt5.disconnect() # Close MT5 connection (client side)

    # 3. Send Notification
    notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        enabled=settings.telegram_enabled
    )

    if not notifier.enabled:
        print("❌ Telegram disabled.")
        return

    print("Sending report...", end=" ", flush=True)
    try:
        await notifier.notify_positions_summary(
            balance=account.real_balance,
            equity=account.real_equity,
            positions=positions,
            mode=settings.trading_mode,
            currency=account.currency
        )
        print("✅ Message sent!")
    except Exception as e:
        print(f"❌ Failed to send Telegram: {e}")
    finally:
        await notifier.close()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
