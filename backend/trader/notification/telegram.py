# -*- coding: utf-8 -*-
"""
Telegram Notifier — Trade Signals & Balance Updates
Sends notifications when trades are opened/blocked with account balance.
"""
import os
import logging
import requests
from dotenv import load_dotenv

load_dotenv("d:/VibeCode/Trade/.env")

logger = logging.getLogger("telegram")
import time

# Internal cache to prevent spamming the same notification
# Key: (type_id, identifier), Value: timestamp
_notification_cache = {}

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ENABLED = os.getenv("TELEGRAM_ENABLED", "FALSE").upper() == "TRUE"

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram MTProto."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_message(text: str) -> bool:
    """Send a message to Telegram. Returns True on success."""
    if not ENABLED or not BOT_TOKEN or not CHAT_ID:
        logger.debug("Telegram disabled or not configured")
        return False
    try:
        resp = requests.post(API_URL, json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=5)
        if resp.status_code == 200:
            return True
        else:
            logger.warning(f"Telegram API error: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


def notify_trade_signal(signal: dict, equity: float, daily_pnl: float,
                         lot: float = 0.0, blocked: bool = False,
                         block_reasons: list = None,
                         vol_warning: bool = False) -> bool:
    """
    Notify Telegram about a trade signal.
    """
    symbol = escape_html(signal.get('symbol', '?'))
    side = escape_html(signal.get('side', '?'))
    model = escape_html(signal.get('model', '?'))
    entry = signal.get('entry_price', 0)
    sl = signal.get('sl', 0)
    tp1 = signal.get('tp1', 0)
    conf = signal.get('confidence', 0)
    rationale = [escape_html(r) for r in signal.get('rationale', [])]

    if blocked:
        emoji = "🚫"
        status = "BLOCKED"
        reasons_str = "\n".join(f"  ⛔ {escape_html(r)}" for r in (block_reasons or []))
        
        # ── Throttling Logic for BLOCKED signals ──
        # We only notify once per 2 hours for the same symbol + reason to avoid M1-H4 spam
        msg_id = f"{symbol}_{reasons_str}"
        now = time.time()
        last_sent = _notification_cache.get(("BLOCKED", msg_id), 0)
        if (now - last_sent) < 7200: # 2 hour cooldown for blocked notifications
            logger.debug(f"Throttling BLOCKED notification for {symbol}")
            return False
        _notification_cache[("BLOCKED", msg_id)] = now

        msg = (
            f"{emoji} <b>{status}: {symbol} {side}</b>\n"
            f"📊 Strategy: {model}\n"
            f"💰 Entry: {entry:.2f} | SL: {sl:.2f} | TP1: {tp1:.2f}\n"
            f"❌ Reasons:\n{reasons_str}\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"💵 Balance: <b>${equity:.2f}</b>\n"
            f"📈 Daily P&L: ${daily_pnl:.2f}"
        )
    else:
        # Clear blocked cache for this symbol if a real trade is allowed
        # (This ensures if it gets blocked again later for a different reason, we might notify)
        keys_to_del = [k for k in _notification_cache.keys() if k[0] == "BLOCKED" and k[1].startswith(symbol)]
        for k in keys_to_del: _notification_cache.pop(k, None)

        emoji = "🟢" if side == "BUY" else "🔴"
        warning_msg = "⚠️ <b>Low Volume: Lot Reduced</b>\n" if vol_warning else ""
        msg = (
            f"{emoji} <b>TRADE: {symbol} {side}</b>\n"
            f"{warning_msg}"
            f"📊 Strategy: {model}\n"
            f"💰 Entry: {entry:.2f}\n"
            f"🛡️ SL: {sl:.2f} | 🎯 TP1: {tp1:.2f}\n"
            f"📏 Lot: {lot:.2f}\n"
            f"🎯 Confidence: {conf:.0%}\n"
        )
        if rationale:
            msg += f"📋 {', '.join(rationale[:3])}\n"
        msg += (
            f"━━━━━━━━━━━━━━━━\n"
            f"💵 Balance: <b>${equity:.2f}</b>\n"
            f"📈 Daily P&L: ${daily_pnl:.2f}"
        )

    return send_message(msg)


def notify_trade_close(symbol: str, side: str, profit: float, lot: float,
                       equity: float, daily_pnl: float, comment: str = "") -> bool:
    """
    Notify Telegram about a closed trade.
    """
    symbol = escape_html(symbol)
    side = escape_html(side)
    comment = escape_html(comment)
    
    emoji = "💰" if profit >= 0 else "💀"
    status = "PROFIT" if profit >= 0 else "LOSS"
    
    msg = (
        f"{emoji} <b>CLOSED: {symbol} {side}</b>\n"
        f"📊 Result: <b>{status} ${profit:+.2f}</b>\n"
        f"📏 Lot: {lot:.2f}\n"
    )
    if comment:
        msg += f"💬 {comment}\n"
        
    msg += (
        f"━━━━━━━━━━━━━━━━\n"
        f"💵 Balance: <b>${equity:.2f}</b>\n"
        f"📈 Daily P&L: ${daily_pnl:.2f}"
    )
    return send_message(msg)


def notify_status(text: str) -> bool:
    """Send a simple status message."""
    return send_message(f"ℹ️ {text}")
