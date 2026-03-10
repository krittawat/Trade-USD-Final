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

def _fmt_price(p: float) -> str:
    """Format price appropriately. Use up to 5 decimals without trailing zeros, and commas for readability."""
    if p == 0:
        return "0"
    s = f"{p:,.5f}".rstrip('0').rstrip('.')
    return s if s else "0"



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

        # Format lot size: use up to 4 decimal places, strip trailing zeros
        lot_str = f"{lot_size:.4f}".rstrip('0').rstrip('.')

        msg = (
            f"{emoji} {mode_tag}**เปิดออเดอร์ {action} {symbol}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 กลยุทธ์: `{strategy_name}`\n"
            f"💰 ขนาด: `{lot_str} lot`\n"
            f"📍 ราคาเข้า: `{_fmt_price(entry_price)}`\n"
            f"🛑 SL: `{_fmt_price(stop_loss)}`\n"
        )

        if take_profit and take_profit > 0:
            msg += f"🎯 TP: `{_fmt_price(take_profit)}`\n"

        # Format risk: show more precision for small values
        risk_str = f"${risk_usd:.2f}" if risk_usd >= 0.01 else f"${risk_usd:.4f}"
        msg += (
            f"⚠️ ความเสี่ยง: `{risk_str}` ({risk_pct:.1f}%)\n"
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
            f"{emoji} {mode_tag}**ปิดออเดอร์ {symbol}**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 {action} | ขนาด: `{lot_size} lot`\n"
            f"🎫 Ticket: `{ticket}`\n"
        )

        if entry_price > 0:
            msg += f"📍 เข้า: `{_fmt_price(entry_price)}`\n"
        if close_price > 0:
            msg += f"📍 ออก: `{_fmt_price(close_price)}`\n"

        profit_emoji = "💰" if profit >= 0 else "💸"
        msg += f"{profit_emoji} กำไร/ขาดทุน: `${profit:+.2f}`\n"

        if reason:
            msg += f"📝 เหตุผล: {reason}\n"

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

    async def notify_bot_start(
        self,
        mode: str = "DRY_RUN",
        symbols: list[str] | None = None,
        strategies_count: int = 0,
        equity: float = 0.0,
        currency: str = "USD",
    ) -> None:
        """แจ้งเตือนเมื่อ bot เริ่มทำงาน."""
        if not self.enabled:
            return

        symbols_str = ", ".join(symbols) if symbols else "N/A"

        msg = (
            f"🚀 **ระบบ Antigravity เริ่มทำงาน**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 โหมด: `{mode}`\n"
            f"💱 คู่เงิน: `{symbols_str}`\n"
            f"🧠 กลยุทธ์: `{strategies_count}`\n"
        )

        if equity > 0:
            msg += f"💰 พอร์ต: `{equity:,.2f} {currency}`\n"

        msg += f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"

        await self._send(msg)

    async def notify_positions_summary(
        self,
        balance: float,
        equity: float,
        positions: list[dict],
        mode: str = "LIVE",
        currency: str = "USD",
    ) -> None:
        """แจ้งเตือนสรุปสถานะพอร์ตและออเดอร์ที่เปิดอยู่."""
        if not self.enabled:
            return

        mode_tag = f"[{mode}] " if mode != "LIVE" else ""
        count = len(positions)
        prof_color = "🟢" if equity >= balance else "🔴"
        
        msg = (
            f"📊 {mode_tag}**สรุปสถานะพอร์ต**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: `{balance:,.2f} {currency}`\n"
            f"{prof_color} Equity: `{equity:,.2f} {currency}`\n"
            f"📝 Positions: `{count}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )

        if not positions:
            msg += "✅ *ไม่มีออเดอร์ค้าง*\n"
        else:
            for i, p in enumerate(positions, 1):
                symbol = p.get('symbol', 'Unknown')
                type_ = p.get('type', 'OP').upper()
                lot = p.get('volume', 0.0)
                price = p.get('price_open', 0.0)
                current = p.get('price_current', 0.0)
                profit = p.get('profit', 0.0)
                sl = p.get('sl', 0.0)
                tp = p.get('tp', 0.0)
                comment = p.get('comment', '')
                
                emoji = "🟢" if type_ == "BUY" else "🔴"
                pl_emoji = "💵" if profit >= 0 else "💸"
                
                msg += (
                    f"{i}. {emoji} **{type_} {symbol}**\n"
                    f"   📦 `{lot} lot` @ `{_fmt_price(price)}`\n"
                    f"   {pl_emoji} P/L: `${profit:+.2f}`\n"
                )
                if sl > 0 or tp > 0:
                    msg += f"   🛡️ SL: `{_fmt_price(sl)}` | 🎯 TP: `{_fmt_price(tp)}`\n"
                if comment:
                    msg += f"   📝 `{comment}`\n"
                msg += "\n"

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
