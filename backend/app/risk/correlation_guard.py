"""
Correlation Guard — ลด risk เมื่อเปิด position ในสินทรัพย์ที่ correlate กัน.

กฎ:
    - ถ้ามี position เปิดอยู่ใน correlated symbol → ลด lot 40%
    - ป้องกัน double exposure (Gold + Silver = correlation 90%+)
    - ป้องกัน currency risk (EURUSD + GBPUSD = USD exposure ซ้อน)

ใช้ใน:
    - gate.py หรือ sizing.py — ตรวจก่อนส่ง order
    - API: ดู correlation status
"""

from app.core.logging import get_logger

logger = get_logger("CorrelationGuard")


# =============================================
# Correlated Pairs Configuration
# =============================================

# key: base symbol → list of correlated symbols
# ใช้ชื่อ MT5 suffix ทั้ง m (micro) และปกติ
CORRELATION_MAP: dict[str, list[str]] = {
    # Precious Metals (correlation ~90%)
    "XAUUSD":  ["XAGUSD", "XAUUSDm", "XAGUSDm"],
    "XAUUSDm": ["XAGUSDm", "XAUUSD", "XAGUSD"],
    "XAGUSD":  ["XAUUSD", "XAUUSDm", "XAGUSDm"],
    "XAGUSDm": ["XAUUSDm", "XAUUSD", "XAGUSD"],

    # EUR Group (correlation ~85%)
    "EURUSD":  ["GBPUSD", "EURUSDm", "GBPUSDm"],
    "EURUSDm": ["GBPUSDm", "EURUSD", "GBPUSD"],
    "GBPUSD":  ["EURUSD", "EURUSDm", "GBPUSDm"],
    "GBPUSDm": ["EURUSDm", "EURUSD", "GBPUSD"],

    # JPY Group (inverse correlation ~80%)
    "USDJPY":  ["USDJPYm"],
    "USDJPYm": ["USDJPY"],

    # Crypto
    "BTCUSD":  ["BTCUSDm"],
    "BTCUSDm": ["BTCUSD"],
}

# ลด lot ลงเท่าไหร่ถ้ามี correlated position
CORRELATION_LOT_REDUCTION = 0.40  # ลด 40%


def get_correlated_symbols(symbol: str) -> list[str]:
    """
    หา symbols ที่ correlate กับ symbol ที่ให้มา.

    Returns:
        list[str]: รายชื่อ correlated symbols
    """
    return CORRELATION_MAP.get(symbol, [])


def check_correlation_exposure(
    symbol: str,
    open_positions: list[dict],
) -> dict:
    """
    ตรวจว่ามี correlated positions เปิดอยู่หรือไม่.

    Args:
        symbol: symbol ที่จะเปิด position ใหม่
        open_positions: list[{symbol, type, volume, ...}] — positions ที่เปิดอยู่

    Returns:
        dict: {
            "has_correlation": bool,
            "correlated_positions": [{symbol, type, volume}],
            "lot_multiplier": float (1.0 = ปกติ, 0.6 = ลด 40%),
            "reason": str,
        }
    """
    correlated = get_correlated_symbols(symbol)
    if not correlated:
        return {
            "has_correlation": False,
            "correlated_positions": [],
            "lot_multiplier": 1.0,
            "reason": "",
        }

    # หา positions ที่เปิดอยู่ใน correlated symbols
    corr_positions = []
    for pos in open_positions:
        pos_symbol = pos.get("symbol", "")
        if pos_symbol in correlated:
            corr_positions.append({
                "symbol": pos_symbol,
                "type": pos.get("type", ""),
                "volume": pos.get("volume", 0),
            })

    if not corr_positions:
        return {
            "has_correlation": False,
            "correlated_positions": [],
            "lot_multiplier": 1.0,
            "reason": "",
        }

    # มี correlation → ลด lot
    multiplier = 1.0 - CORRELATION_LOT_REDUCTION  # 0.60

    # ถ้ามีหลาย correlated positions → ลดมากขึ้น
    if len(corr_positions) >= 2:
        multiplier *= 0.8  # เหลือ ~0.48

    corr_syms = [p["symbol"] for p in corr_positions]
    reason = f"Correlated with {', '.join(corr_syms)} → lot ×{multiplier:.2f}"

    logger.info("correlation_detected", extra={
        "symbol": symbol,
        "correlated": corr_syms,
        "multiplier": round(multiplier, 2),
    })

    return {
        "has_correlation": True,
        "correlated_positions": corr_positions,
        "lot_multiplier": round(multiplier, 2),
        "reason": reason,
    }


def adjust_lot_for_correlation(
    symbol: str,
    lot_size: float,
    open_positions: list[dict],
) -> tuple[float, str]:
    """
    ปรับ lot size ตาม correlation exposure.

    Args:
        symbol: symbol ที่จะเปิด
        lot_size: lot size เดิม
        open_positions: positions เปิดอยู่

    Returns:
        (adjusted_lot, reason_string)
    """
    result = check_correlation_exposure(symbol, open_positions)

    if not result["has_correlation"]:
        return lot_size, ""

    adjusted = round(lot_size * result["lot_multiplier"], 2)
    adjusted = max(adjusted, 0.01)  # ไม่ต่ำกว่า min lot

    logger.info("lot_adjusted_correlation", extra={
        "symbol": symbol,
        "original_lot": lot_size,
        "adjusted_lot": adjusted,
        "multiplier": result["lot_multiplier"],
    })

    return adjusted, result["reason"]
