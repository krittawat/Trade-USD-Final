"""
Telegram Notifier — แจ้งเตือนผ่าน Telegram เมื่อเปิด/ปิดออเดอร์.

ใช้ httpx (async) ยิง Telegram Bot API:
    - notify_trade_open(): เมื่อเปิดออเดอร์สำเร็จ
    - notify_trade_close(): เมื่อปิดออเดอร์ (SL/TP hit, manual, emergency)
    - notify_error(): เมื่อเกิด error สำคัญ

กฎ:
    - Fire-and-forget: ถ้า Telegram ล่ม → log error ไม่กระทบ trading
    - ถ้า token/chat_id ว่าง → disable อัตโนมัติ (ไม่ error)
    - Rate limit aware: Telegram API limit = 30 msg/sec
"""

import asyncio
from datetime import datetime, timezone

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# Telegram Bot API base URL
_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"

# HTTP timeout (seconds) — ไม่ให้ block trading loop นาน
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class TelegramNotifier:
    """
    Async Telegram notifier — ส่งข้อความแจ้งเตือนเทรด.

    ถ้า token หรือ chat_id ว่าง → disabled (silent, no error).
    ทุก method เป็น fire-and-forget — ห้าม raise exception ออกมา.
    """

    def __init__(self, bot_token: str = "", chat_id: str = "", enabled: bool = True) -> None:
        self.bot_token = bot_token.strip()
        self.chat_id = chat_id.strip()
        self.enabled = enabled and bool(self.bot_token) and bool(self.chat_id)
        self._client: httpx.AsyncClient | None = None

        if self.enabled:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT)
            self._url = _API_BASE.format(token=self.bot_token)
            logger.info("telegram_notifier_enabled", extra={"chat_id": self.chat_id})
        else:
            logger.info("telegram_notifier_disabled", extra={
                "reason": "token/chat_id empty or disabled" if not self.enabled else "disabled by config",
            })

    # ────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────

    async def notify_trade_open(
        self,
        symbol: str,
        action: str,
        lot_size: float,
        entry_price: float,
        stop_loss: float,
        take_profit: float | None,
        risk_usd: float,
        risk_pct: float,
        strategy_name: str = "",
        ticket: int | None = None,
        mode: str = "DRY_RUN",
    ) -> None:
        """แจ้งเตือนเมื่อเปิดออเดอร์สำเร็จ."""
        if not self.enabled:
            return

        emoji = "🟢" if action == "BUY" else "🔴"
        mode_tag = f"[{mode}] " if mode != "LIVE" else ""

        msg = (
            f"{emoji} {mode_tag}**{action} {symbol}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 Strategy: `{strategy_name}`\n"
            f"💰 Lot: `{lot_size}`\n"
            f"📍 Entry: `{entry_price:.5g}`\n"
            f"🛑 SL: `{stop_loss:.5g}`\n"
        )

        if take_profit and take_profit > 0:
            msg += f"🎯 TP: `{take_profit:.5g}`\n"

        msg += (
            f"⚠️ Risk: `${risk_usd:.2f}` ({risk_pct:.1f}%)\n"
        )

        if ticket:
            msg += f"🎫 Ticket: `{ticket}`\n"

        msg += f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"

        await self._send(msg)

    async def notify_trade_close(
        self,
        symbol: str,
        ticket: int,
        action: str,
        lot_size: float,
        profit: float,
        entry_price: float = 0.0,
        close_price: float = 0.0,
        reason: str = "",
        mode: str = "LIVE",
    ) -> None:
        """แจ้งเตือนเมื่อปิดออเดอร์."""
        if not self.enabled:
            return

        emoji = "✅" if profit >= 0 else "❌"
        mode_tag = f"[{mode}] " if mode != "LIVE" else ""

        msg = (
            f"{emoji} {mode_tag}**CLOSED {symbol}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 {action} | Lot: `{lot_size}`\n"
            f"🎫 Ticket: `{ticket}`\n"
        )

        if entry_price > 0:
            msg += f"📍 Entry: `{entry_price:.5g}`\n"
        if close_price > 0:
            msg += f"📍 Close: `{close_price:.5g}`\n"

        profit_emoji = "💰" if profit >= 0 else "💸"
        msg += f"{profit_emoji} P/L: `${profit:+.2f}`\n"

        if reason:
            msg += f"📝 Reason: {reason}\n"

        msg += f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"

        await self._send(msg)

    async def notify_error(self, title: str, details: str = "") -> None:
        """แจ้งเตือน error สำคัญ."""
        if not self.enabled:
            return

        msg = f"🚨 **{title}**\n"
        if details:
            msg += f"{details}\n"
        msg += f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"

        await self._send(msg)

    async def close(self) -> None:
        """ปิด HTTP client — เรียกตอน shutdown."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ────────────────────────────────────────────────────────────────
    # Internal
    # ────────────────────────────────────────────────────────────────

    async def _send(self, text: str) -> None:
        """ส่งข้อความไป Telegram — fire-and-forget, ห้าม raise."""
        if not self._client:
            return

        try:
            resp = await self._client.post(
                self._url,
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )

            if resp.status_code != 200:
                logger.warning("telegram_send_failed", extra={
                    "status": resp.status_code,
                    "body": resp.text[:200],
                })
            else:
                logger.debug("telegram_sent", extra={"chars": len(text)})

        except httpx.TimeoutException:
            logger.warning("telegram_timeout")
        except Exception as e:
            logger.warning("telegram_error", extra={"error": str(e)})
