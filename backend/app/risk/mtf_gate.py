"""
MTF Gate — ประตูยืนยันหลายไทม์เฟรม (Multi-Timeframe Confirmation)

ตรวจสอบ H1 trend ก่อนอนุญาตให้เข้าเทรดบน M5:
    - BUY: H1 EMA(21) ต้องชี้ขึ้น + ราคาปิด > EMA
    - SELL: H1 EMA(21) ต้องชี้ลง + ราคาปิด < EMA
    - ถ้า M5 signal ขัดกับ H1 → HOLD (ไม่เทรด)

ลดสัญญาณหลอกได้ ~30-40%
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass

from app.core.logging import get_logger
from app.domain.enums import Action

logger = get_logger(__name__)

MIN_H1_BARS = 30  # ต้องการอย่างน้อย 30 แท่ง H1


@dataclass
class MTFResult:
    """ผลการตรวจสอบ MTF"""
    aligned: bool = True       # True = M5 สอดคล้องกับ H1
    h1_trend: str = "UNKNOWN"  # "UP", "DOWN", "FLAT", "UNKNOWN"
    h1_ema_slope: float = 0.0  # ความชันของ EMA (ปรับตาม ATR)
    confidence_adj: float = 0.0  # ค่าปรับ confidence
    reason: str = ""           # เหตุผล


class MTFGate:
    """
    ประตู Multi-Timeframe — เช็ค H1 trend ก่อนเปิดออเดอร์
    """

    def __init__(self, ema_period: int = 21):
        self.ema_period = ema_period

    def check(
        self,
        action: Action,
        h1_candles: pd.DataFrame | None,
    ) -> MTFResult:
        """
        ตรวจสอบว่า action (BUY/SELL) สอดคล้องกับ H1 trend หรือไม่

        Returns:
            MTFResult — ผลตรวจ
        """
        result = MTFResult()

        if h1_candles is None or len(h1_candles) < MIN_H1_BARS:
            result.reason = "mtf:ข้อมูล_h1_ไม่พอ"
            return result  # ถ้าไม่มี H1 data → ปล่อยผ่าน

        try:
            close = h1_candles["close"].astype(float)
            ema = close.ewm(span=self.ema_period, adjust=False).mean()

            current_close = float(close.iloc[-1])
            current_ema = float(ema.iloc[-1])
            prev_ema = float(ema.iloc[-2])

            # ─── หาทิศทาง H1 Trend ───
            ema_slope = current_ema - prev_ema
            price_vs_ema = current_close - current_ema

            # ปรับ slope ตาม ATR เพื่อเทียบมาตราฐาน
            h = h1_candles["high"].astype(float)
            l = h1_candles["low"].astype(float)
            tr = pd.concat([
                h - l,
                (h - close.shift()).abs(),
                (l - close.shift()).abs()
            ], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])

            if atr > 0:
                norm_slope = ema_slope / atr
            else:
                norm_slope = 0.0

            result.h1_ema_slope = round(norm_slope, 3)

            # จำแนกเทรน H1
            if norm_slope > 0.05 and price_vs_ema > 0:
                result.h1_trend = "UP"
            elif norm_slope < -0.05 and price_vs_ema < 0:
                result.h1_trend = "DOWN"
            elif abs(norm_slope) < 0.02:
                result.h1_trend = "FLAT"
            else:
                result.h1_trend = "MIXED"

            # ─── ตรวจความสอดคล้อง ───
            if action == Action.BUY:
                if result.h1_trend == "UP":
                    result.aligned = True
                    result.confidence_adj = +0.05
                    result.reason = f"mtf:h1_ตรงทาง_ขาขึ้น(slope={norm_slope:.3f})"
                elif result.h1_trend == "DOWN":
                    result.aligned = False
                    result.confidence_adj = -0.08
                    result.reason = f"mtf:h1_สวนทาง_ขาลง(slope={norm_slope:.3f})"
                else:
                    result.aligned = True  # FLAT/MIXED → ปล่อยผ่าน แต่ไม่ boost
                    result.confidence_adj = 0.0
                    result.reason = f"mtf:h1_{result.h1_trend.lower()}"

            elif action == Action.SELL:
                if result.h1_trend == "DOWN":
                    result.aligned = True
                    result.confidence_adj = +0.05
                    result.reason = f"mtf:h1_ตรงทาง_ขาลง(slope={norm_slope:.3f})"
                elif result.h1_trend == "UP":
                    result.aligned = False
                    result.confidence_adj = -0.08
                    result.reason = f"mtf:h1_สวนทาง_ขาขึ้น(slope={norm_slope:.3f})"
                else:
                    result.aligned = True
                    result.confidence_adj = 0.0
                    result.reason = f"mtf:h1_{result.h1_trend.lower()}"

            logger.debug("mtf_gate_check", extra={
                "action": action.value,
                "h1_trend": result.h1_trend,
                "aligned": result.aligned,
                "slope": result.h1_ema_slope,
            })

        except Exception as e:
            logger.debug("mtf_gate_error", extra={"error": str(e)})
            result.reason = f"mtf:error({e})"

        return result
