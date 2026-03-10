"""
Telegram Command Handler — รับคำสั่งจาก Telegram แล้วตอบกลับข้อมูล MT5.

Commands:
    /status     — สถานะพอร์ต (Balance, Equity, Margin, Positions)
    /positions  — รายละเอียดทุก position ที่เปิดอยู่
    /balance    — Balance, Equity, Margin, Free Margin, Margin Level

Security:
    - ตอบเฉพาะ chat_id ที่ตรงกับ config เท่านั้น
    - Fire-and-forget: ถ้า Telegram ล่ม → log error ไม่กระทบ trading

Usage:
    handler = TelegramCommandHandler(telegram, mt5_client, settings)
    task = asyncio.create_task(handler.start_polling())
    # ... shutdown ...
    handler.stop()
    await task
"""

import asyncio
from datetime import datetime, timezone

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# Polling interval (seconds)
_POLL_INTERVAL = 2.0
_POLL_TIMEOUT = 30  # long-polling timeout for getUpdates


class TelegramCommandHandler:
    """
    Async Telegram command handler — poll getUpdates + respond.

    ใช้ long-polling เพื่อรับ commands จากผู้ใช้
    ตอบกลับด้วยข้อมูลจาก MT5Client แบบ real-time
    """

    def __init__(self, telegram_notifier, mt5_client, settings) -> None:
        self._telegram = telegram_notifier
        self._mt5 = mt5_client
        self._settings = settings
        self._running = False
        self._offset = 0  # Telegram update offset

        # Build URLs from bot token
        self._bot_token = settings.telegram_bot_token.strip()
        self._chat_id = settings.telegram_chat_id.strip()
        self._enabled = (
            settings.telegram_enabled
            and bool(self._bot_token)
            and bool(self._chat_id)
        )

        if self._enabled:
            base = f"https://api.telegram.org/bot{self._bot_token}"
            self._updates_url = f"{base}/getUpdates"
            self._send_url = f"{base}/sendMessage"
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=5.0))
            logger.info("telegram_commands_enabled")
        else:
            self._updates_url = ""
            self._send_url = ""
            self._client = None
            logger.info("telegram_commands_disabled")

        # Command registry
        self._commands = {
            "/status": self._cmd_status,
            "/positions": self._cmd_positions,
            "/balance": self._cmd_balance,
            "/help": self._cmd_help,
        }

    # ────────────────────────────────────────────────────────────────
    # Lifecycle
    # ────────────────────────────────────────────────────────────────

    async def start_polling(self) -> None:
        """Start long-polling loop — runs forever until stop() is called."""
        if not self._enabled:
            logger.info("telegram_commands_skip", extra={"reason": "disabled"})
            return

        self._running = True
        logger.info("telegram_commands_polling_start")

        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("telegram_poll_error", extra={"error": str(e)})
                await asyncio.sleep(5.0)  # backoff on error

    def stop(self) -> None:
        """Signal polling loop to stop."""
        self._running = False

    async def close(self) -> None:
        """Clean up HTTP client."""
        self.stop()
        if self._client:
            await self._client.aclose()
            self._client = None

    # ────────────────────────────────────────────────────────────────
    # Polling
    # ────────────────────────────────────────────────────────────────

    async def _poll_once(self) -> None:
        """Fetch updates from Telegram and process commands."""
        if not self._client:
            return

        try:
            resp = await self._client.get(
                self._updates_url,
                params={
                    "offset": self._offset,
                    "timeout": _POLL_TIMEOUT,
                    "allowed_updates": '["message"]',
                },
            )

            if resp.status_code != 200:
                logger.warning("telegram_getUpdates_failed", extra={
                    "status": resp.status_code,
                })
                await asyncio.sleep(_POLL_INTERVAL)
                return

            data = resp.json()
            updates = data.get("result", [])

            for update in updates:
                self._offset = update["update_id"] + 1
                await self._handle_update(update)

        except httpx.TimeoutException:
            pass  # normal for long-polling
        except Exception as e:
            logger.warning("telegram_poll_error", extra={"error": str(e)})
            await asyncio.sleep(_POLL_INTERVAL)

    async def _handle_update(self, update: dict) -> None:
        """Process a single Telegram update."""
        message = update.get("message", {})
        text = message.get("text", "").strip()
        chat_id = str(message.get("chat", {}).get("id", ""))

        if not text or not chat_id:
            return

        # Security: only respond to authorized chat
        if chat_id != self._chat_id:
            logger.warning("telegram_unauthorized", extra={
                "chat_id": chat_id,
                "expected": self._chat_id,
            })
            return

        # Extract command (handle /command@botname format)
        cmd = text.split()[0].lower().split("@")[0]

        handler = self._commands.get(cmd)
        if handler:
            logger.info("telegram_command", extra={"cmd": cmd, "chat_id": chat_id})
            try:
                response_text = await handler()
                await self._reply(chat_id, response_text)
            except Exception as e:
                logger.error("telegram_command_error", extra={
                    "cmd": cmd, "error": str(e),
                })
                await self._reply(chat_id, f"❌ Error: {e}")

    # ────────────────────────────────────────────────────────────────
    # Commands
    # ────────────────────────────────────────────────────────────────

    async def _cmd_status(self) -> str:
        """/status — สถานะพอร์ตรวม"""
        if not self._mt5.is_connected():
            return "⚠️ MT5 ไม่ได้เชื่อมต่อ — ไม่สามารถดึงข้อมูลได้"

        acct = self._mt5.get_account_state()
        positions = self._mt5.get_positions()
        mode = self._settings.trading_mode

        total_profit = sum(p.get("profit", 0.0) for p in positions)
        prof_emoji = "🟢" if acct.real_equity >= acct.real_balance else "🔴"

        # Margin Level calculation
        margin_level = (acct.real_equity / acct.margin * 100) if acct.margin > 0 else 0.0
        margin_level_emoji = "🟢" if margin_level > 200 else ("🟡" if margin_level > 100 else "🔴")

        msg = (
            f"📊 **สถานะพอร์ต** [{mode}]\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: `{acct.real_balance:,.2f} {acct.currency}`\n"
            f"{prof_emoji} Equity: `{acct.real_equity:,.2f} {acct.currency}`\n"
            f"📊 Margin: `${acct.margin:,.2f}` (USD eq)\n"
            f"💵 Free Margin: `${acct.free_margin:,.2f}` (USD eq)\n"
            f"{margin_level_emoji} Margin Level: `{margin_level:.1f}%`\n"
            f"📈 Leverage: `1:{acct.leverage}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📝 Open Positions: `{len(positions)}`\n"
        )

        if positions:
            pl_emoji = "💰" if total_profit >= 0 else "💸"
            msg += f"{pl_emoji} Floating P/L: `{total_profit:+,.2f} {currency}`\n"

            # Summary per symbol
            symbol_summary = {}
            for p in positions:
                sym = p.get("symbol", "?")
                if sym not in symbol_summary:
                    symbol_summary[sym] = {"count": 0, "profit": 0.0}
                symbol_summary[sym]["count"] += 1
                symbol_summary[sym]["profit"] += p.get("profit", 0.0)

            msg += "━━━━━━━━━━━━━━━━━━\n"
            for sym, info in symbol_summary.items():
                s_emoji = "💰" if info["profit"] >= 0 else "💸"
                msg += f"  {s_emoji} {sym}: `{info['count']}` pos → `{info['profit']:+,.2f} {currency}`\n"
        else:
            msg += "✅ ไม่มีออเดอร์เปิดอยู่\n"

        msg += f"\n🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
        return msg

    async def _cmd_positions(self) -> str:
        """/positions — รายละเอียดทุก position"""
        if not self._mt5.is_connected():
            return "⚠️ MT5 ไม่ได้เชื่อมต่อ — ไม่สามารถดึงข้อมูลได้"

        positions = self._mt5.get_positions()
        
        # Helper to get currency formatting
        acct = self._mt5.get_account_state()
        currency = getattr(acct, 'currency', 'USD')

        if not positions:
            return (
                "📋 **รายการ Positions**\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "✅ ไม่มีออเดอร์เปิดอยู่\n"
                f"\n🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
            )

        total_profit = sum(p.get("profit", 0.0) for p in positions)
        msg = (
            f"📋 **รายการ Positions** ({len(positions)} ออเดอร์)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
        )

        for i, p in enumerate(positions, 1):
            symbol = p.get("symbol", "Unknown")
            type_ = p.get("type", "OP").upper()
            lot = p.get("volume", 0.0)
            entry = p.get("price_open", 0.0)
            current = p.get("price_current", 0.0)
            profit = p.get("profit", 0.0)
            sl = p.get("sl", 0.0)
            tp = p.get("tp", 0.0)
            ticket = p.get("ticket", 0)
            comment = p.get("comment", "")
            digits = p.get("digits", 2)

            emoji = "🟢" if type_ == "BUY" else "🔴"
            pl_emoji = "💰" if profit >= 0 else "💸"

            # Calculate pips (approximate)
            price_diff = current - entry if type_ == "BUY" else entry - current

            msg += (
                f"{i}. {emoji} **{type_} {symbol}**\n"
                f"   🎫 `#{ticket}`\n"
                f"   📦 `{lot} lot`\n"
                f"   📍 Entry: `{entry:.{digits}f}`\n"
                f"   📍 Current: `{current:.{digits}f}`\n"
                f"   {pl_emoji} P/L: `{profit:+,.2f} {currency}`\n"
            )

            if sl > 0:
                msg += f"   🛡️ SL: `{sl:.{digits}f}`\n"
            if tp > 0:
                msg += f"   🎯 TP: `{tp:.{digits}f}`\n"
            if comment:
                msg += f"   📝 `{comment}`\n"
            msg += "\n"

        pl_emoji = "💰" if total_profit >= 0 else "💸"
        msg += (
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{pl_emoji} **Total P/L: `{total_profit:+,.2f} {currency}`**\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
        )
        return msg

    async def _cmd_balance(self) -> str:
        """/balance — Balance, Equity, Margin details"""
        if not self._mt5.is_connected():
            return "⚠️ MT5 ไม่ได้เชื่อมต่อ — ไม่สามารถดึงข้อมูลได้"

        acct = self._mt5.get_account_state()

        # Margin Level calculation
        margin_level = (acct.real_equity / acct.margin * 100) if acct.margin > 0 else 0.0
        margin_level_emoji = "🟢" if margin_level > 200 else ("🟡" if margin_level > 100 else "🔴")

        # Floating P/L (Raw account currency from MT5 positions)
        positions = self._mt5.get_positions()
        floating = sum(p.get("profit", 0.0) for p in positions)
        fl_emoji = "💰" if floating >= 0 else "💸"

        # P/L as percentage of balance
        pl_pct = (floating / acct.real_balance * 100) if acct.real_balance > 0 else 0.0
        prof_emoji = "🟢" if acct.real_equity >= acct.real_balance else "🔴"

        msg = (
            f"💰 **Balance & Margin**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: `{acct.real_balance:,.2f} {acct.currency}`\n"
            f"{prof_emoji} Equity: `{acct.real_equity:,.2f} {acct.currency}`\n"
            f"{fl_emoji} Floating P/L: `{floating:+,.2f} {acct.currency}` ({pl_pct:+.2f}%)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 Margin Used: `${acct.margin:,.2f}` (USD eq)\n"
            f"💵 Free Margin: `${acct.free_margin:,.2f}` (USD eq)\n"
            f"{margin_level_emoji} Margin Level: `{margin_level:.1f}%`\n"
            f"📈 Leverage: `1:{acct.leverage}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📝 Open Positions: `{acct.open_positions}`\n"
            f"\n🕐 {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}"
        )
        return msg

    async def _cmd_help(self) -> str:
        """/help — แสดงคำสั่งที่ใช้ได้"""
        return (
            "🤖 **Antigravity Bot Commands**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "/status — สถานะพอร์ตรวม\n"
            "/positions — รายละเอียดทุก position\n"
            "/balance — Balance & Margin\n"
            "/help — แสดงคำสั่งนี้\n"
        )

    # ────────────────────────────────────────────────────────────────
    # Reply helper
    # ────────────────────────────────────────────────────────────────

    async def _reply(self, chat_id: str, text: str) -> None:
        """Send reply to specific chat — fire-and-forget."""
        if not self._client:
            return

        try:
            resp = await self._client.post(
                self._send_url,
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
            if resp.status_code != 200:
                logger.warning("telegram_reply_failed", extra={
                    "status": resp.status_code,
                    "body": resp.text[:200],
                })
        except httpx.TimeoutException:
            logger.warning("telegram_reply_timeout")
        except Exception as e:
            logger.warning("telegram_reply_error", extra={"error": str(e)})
