"""
Symbols Route — ข้อมูลสัญลักษณ์จริงจาก MT5 + last decisions.

แสดง: profile, session, spread, positions, last decision + BLOCKED reason.
"""

from fastapi import APIRouter

from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("/symbols")
async def list_symbols():
    """ดูรายการสัญลักษณ์ทั้งหมดจาก MT5 + runtime state."""
    try:
        import MetaTrader5 as mt5
        from run_bot import master_loop

        symbols_data = []

        # ดึง symbols จาก master_loop state
        if master_loop and master_loop.last_decisions:
            for symbol, last_dec in master_loop.last_decisions.items():
                info = mt5.symbol_info(symbol)
                symbols_data.append({
                    "symbol": symbol,
                    "spread": info.spread if info else 0,
                    "bid": info.bid if info else 0,
                    "ask": info.ask if info else 0,
                    "last_decision": last_dec,
                })

        # เสริมจาก MT5 symbols ที่ยังไม่มี
        from app.master_loop import DEFAULT_SYMBOLS
        for sym in DEFAULT_SYMBOLS:
            if not any(s["symbol"] == sym for s in symbols_data):
                info = mt5.symbol_info(sym)
                symbols_data.append({
                    "symbol": sym,
                    "spread": info.spread if info else 0,
                    "bid": info.bid if info else 0,
                    "ask": info.ask if info else 0,
                    "last_decision": None,
                })

        return {"symbols": symbols_data, "count": len(symbols_data)}
    except Exception as e:
        logger.error("symbols_error", extra={"error": str(e)})
        return {"symbols": [], "count": 0, "error": str(e)}


@router.get("/symbols/{symbol}")
async def get_symbol_detail(symbol: str):
    """ดูรายละเอียดสัญลักษณ์เฉพาะ — profile, positions, last decision."""
    try:
        import MetaTrader5 as mt5
        from run_bot import master_loop

        info = mt5.symbol_info(symbol)
        positions = mt5.positions_get(symbol=symbol) or []

        last_decision = None
        if master_loop and symbol in master_loop.last_decisions:
            last_decision = master_loop.last_decisions[symbol]

        from app.services.session import get_current_session
        session = get_current_session()

        return {
            "symbol": symbol,
            "profile": {
                "digits": info.digits if info else 0,
                "point": info.point if info else 0,
                "contract_size": info.trade_contract_size if info else 0,
                "volume_min": info.volume_min if info else 0,
                "volume_max": info.volume_max if info else 0,
                "spread": info.spread if info else 0,
                "bid": info.bid if info else 0,
                "ask": info.ask if info else 0,
            } if info else None,
            "open_positions": len(positions),
            "max_positions": 2,
            "position_details": [
                {
                    "ticket": p.ticket,
                    "type": "BUY" if p.type == 0 else "SELL",
                    "volume": p.volume,
                    "price_open": p.price_open,
                    "price_current": p.price_current,
                    "profit": p.profit,
                    "sl": p.sl,
                    "tp": p.tp,
                }
                for p in positions
            ],
            "last_decision": last_decision,
            "session": session.value,
        }
    except Exception as e:
        logger.error("symbol_detail_error", extra={"error": str(e)})
        return {"symbol": symbol, "error": str(e)}
