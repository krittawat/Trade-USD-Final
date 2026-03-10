# -*- coding: utf-8 -*-
"""
Close ALL open Gold (XAUUSD / XAUUSDm) positions immediately.
ปิดทุกออเดอร์ทองก่อนเปลี่ยนไปเน้น BTC เต็มตัว
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

import MetaTrader5 as mt5
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
logger = logging.getLogger("close_gold")

GOLD_SYMBOLS = ["XAUUSD", "XAUUSDm", "XAGUSD", "XAGUSDm"]


def close_all_gold():
    if not mt5.initialize():
        logger.error("❌ ไม่สามารถเชื่อมต่อ MT5")
        return

    logger.info("🔌 เชื่อมต่อ MT5 สำเร็จ")

    total_closed = 0
    total_pnl = 0.0

    for sym in GOLD_SYMBOLS:
        positions = mt5.positions_get(symbol=sym)
        if not positions:
            continue

        logger.info(f"🥇 พบ {len(positions)} ออเดอร์ {sym}")

        for pos in positions:
            # Determine close order type (opposite of position)
            close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
            price = mt5.symbol_info_tick(pos.symbol).bid if close_type == mt5.ORDER_TYPE_SELL else mt5.symbol_info_tick(pos.symbol).ask

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "position": pos.ticket,
                "price": price,
                "deviation": 20,
                "magic": 999999,
                "comment": "BTC_FOCUS: Close gold",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }

            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                total_closed += 1
                total_pnl += pos.profit
                logger.info(
                    f"  ✅ ปิดสำเร็จ #{pos.ticket} | {pos.volume} lot | "
                    f"PnL ${pos.profit:+.2f}"
                )
            else:
                err = result.comment if result else "Unknown"
                logger.error(f"  ❌ ปิดไม่สำเร็จ #{pos.ticket}: {err}")

    if total_closed == 0:
        logger.info("✨ ไม่มีออเดอร์ทอง/เงิน เปิดอยู่ — พร้อมเน้น BTC เต็มตัว!")
    else:
        pnl_color = "🟢" if total_pnl >= 0 else "🔴"
        logger.info(
            f"\n{'='*50}\n"
            f"📊 สรุป: ปิด {total_closed} ออเดอร์ | {pnl_color} PnL รวม ${total_pnl:+.2f}\n"
            f"{'='*50}"
        )

    mt5.shutdown()
    logger.info("🚀 พร้อมปั้นพอตด้วย BTC แล้ว!")


if __name__ == "__main__":
    close_all_gold()
