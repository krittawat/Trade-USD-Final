"""
Regime Filter — ตัวกรอง NO-TRADE สำหรับตลาด sideways/low-volatility.

ตรวจ 4 เงื่อนไข — ถ้าเข้าข้อใดข้อหนึ่ง → ห้ามเทรด:
    1. ATR(14) < ATR_MA(50) — volatility ต่ำกว่าค่าเฉลี่ย
    2. EMA(9/21/50) อัดแน่น — ไม่มีเทรนด์ชัด
    3. ADX < 18 — ตลาด sideways จัด
    4. Range 30 แท่งล่าสุด < threshold — กรอบแคบเกินไป

ใช้ใน:
    - Gate: บล็อกเทรดเมื่อตลาด NO-TRADE
    - Backtester: enforce กฎเดียวกับ live
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RegimeFilterResult:
    """ผลตรวจ regime filter."""
    tradable: bool           # True = เทรดได้, False = NO-TRADE
    reason: str = ""         # เหตุผลที่ห้ามเทรด
    details: dict | None = None


class RegimeFilter:
    """
    ตัวกรองสภาวะตลาด — ตัดสินว่าเทรดได้ไหม.

    Usage:
        rf = RegimeFilter(settings)
        result = rf.check(candles)
        if not result.tradable:
            # บล็อก — ตลาด sideways/low-vol
    """

    def __init__(
        self,
        enabled: bool = True,
        adx_min: float = 12.0,
        ema_compression_pct: float = 0.1,
        range_threshold_pct: float = 0.1,    # Relaxed from 0.3
        atr_period: int = 14,
        atr_ma_period: int = 50,
        atr_ratio_min: float = 0.8,         # Added to allow relaxation
    ) -> None:
        self.enabled = enabled
        self.adx_min = adx_min
        self.ema_compression_pct = 0.05      # Relaxed from 0.1
        self.range_threshold_pct = range_threshold_pct
        self.atr_period = atr_period
        self.atr_ma_period = atr_ma_period
        self.atr_ratio_min = atr_ratio_min

    def check(self, candles: pd.DataFrame) -> RegimeFilterResult:
        """
        ตรวจว่าตลาดเทรดได้ไหม.

        Args:
            candles: DataFrame ที่มี [open, high, low, close]

        Returns:
            RegimeFilterResult — tradable=True ถ้าเทรดได้
        """
        if not self.enabled:
            return RegimeFilterResult(tradable=True)

        if candles is None or len(candles) < self.atr_ma_period + 10:
            # ข้อมูลไม่พอ → อนุญาต (fail-open ดีกว่า crash)
            return RegimeFilterResult(tradable=True, reason="insufficient_data")

        try:
            high = candles["high"].values.astype(float)
            low = candles["low"].values.astype(float)
            close = candles["close"].values.astype(float)
        except (KeyError, ValueError):
            return RegimeFilterResult(tradable=True, reason="invalid_columns")

        details: dict = {}

        # ─── 1. ATR(14) < ATR_MA(50) → volatility ต่ำ ───
        atr_blocked, atr_details = self._check_atr(high, low, close)
        details.update(atr_details)
        if atr_blocked:
            logger.info("regime_filter_blocked", extra={
                "reason": "atr_below_average", **atr_details,
            })
            return RegimeFilterResult(
                tradable=False,
                reason="ATR ต่ำกว่าค่าเฉลี่ย — volatility ไม่เพียงพอ",
                details=details,
            )

        # ─── 2. EMA(9/21/50) อัดแน่น ───
        ema_blocked, ema_details = self._check_ema_compression(close)
        details.update(ema_details)
        if ema_blocked:
            logger.info("regime_filter_blocked", extra={
                "reason": "ema_compressed", **ema_details,
            })
            return RegimeFilterResult(
                tradable=False,
                reason="EMA 9/21/50 อัดแน่น — ไม่มีเทรนด์ชัด",
                details=details,
            )

        # ─── 3. ADX < 18 ───
        adx_blocked, adx_details = self._check_adx(high, low, close)
        details.update(adx_details)
        if adx_blocked:
            logger.info("regime_filter_blocked", extra={
                "reason": "adx_below_threshold", **adx_details,
            })
            return RegimeFilterResult(
                tradable=False,
                reason=f"ADX < {self.adx_min} — sideways",
                details=details,
            )

        # ─── 4. Range 30 แท่ง < threshold ───
        range_blocked, range_details = self._check_range(high, low, close)
        details.update(range_details)
        if range_blocked:
            logger.info("regime_filter_blocked", extra={
                "reason": "narrow_range", **range_details,
            })
            return RegimeFilterResult(
                tradable=False,
                reason="กรอบราคา 30 แท่งแคบเกินไป",
                details=details,
            )

        return RegimeFilterResult(tradable=True, details=details)

    # ────────────────────────────────────────────────
    # Internal checks
    # ────────────────────────────────────────────────

    def _check_atr(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
    ) -> tuple[bool, dict]:
        """ATR(14) vs ATR_MA(50) — True ถ้าบล็อก."""
        n = len(close)
        if n < self.atr_ma_period + 2:
            return False, {}

        # True Range
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1]),
            ),
        )

        # ATR(14) = SMA of last 14 TR values
        if len(tr) < self.atr_ma_period:
            return False, {}

        atr_current = float(np.mean(tr[-self.atr_period:]))
        atr_ma = float(np.mean(tr[-self.atr_ma_period:]))

        details = {
            "atr_current": round(atr_current, 4),
            "atr_ma": round(atr_ma, 4),
            "atr_ratio": round(atr_current / atr_ma, 3) if atr_ma > 0 else 0,
        }
        # Relaxed from atr_current < atr_ma (1.0 ratio)
        blocked = (atr_current / atr_ma) < self.atr_ratio_min if atr_ma > 0 else False
        return blocked, details

    def _check_ema_compression(self, close: np.ndarray) -> tuple[bool, dict]:
        """EMA 9/21/50 compression — True ถ้าบล็อก."""
        n = len(close)
        if n < 60:
            return False, {}

        ema9 = self._ema(close, 9)
        ema21 = self._ema(close, 21)
        ema50 = self._ema(close, 50)

        if ema9 is None or ema21 is None or ema50 is None:
            return False, {}

        # Spread = (max EMA - min EMA) / price × 100
        spread = (max(ema9, ema21, ema50) - min(ema9, ema21, ema50))
        price = float(close[-1])
        spread_pct = (spread / price * 100) if price > 0 else 0

        details = {
            "ema9": round(ema9, 4),
            "ema21": round(ema21, 4),
            "ema50": round(ema50, 4),
            "ema_spread_pct": round(spread_pct, 4),
            "ema_threshold_pct": self.ema_compression_pct,
        }
        blocked = spread_pct < self.ema_compression_pct
        return blocked, details

    def _check_adx(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
    ) -> tuple[bool, dict]:
        """ADX check — True ถ้าบล็อก (ADX < threshold)."""
        n = len(close)
        if n < 30:
            return False, {}

        try:
            adx_val = self._compute_adx(high, low, close, period=14)
        except Exception:
            return False, {}

        if adx_val is None:
            return False, {}

        details = {
            "adx": round(adx_val, 2),
            "adx_threshold": self.adx_min,
        }
        blocked = adx_val < self.adx_min
        return blocked, details

    def _check_range(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
    ) -> tuple[bool, dict]:
        """30-bar range check — True ถ้าบล็อก."""
        lookback = 30
        if len(high) < lookback:
            return False, {}

        range_high = float(np.max(high[-lookback:]))
        range_low = float(np.min(low[-lookback:]))
        price = float(close[-1])

        range_pct = ((range_high - range_low) / price * 100) if price > 0 else 0

        details = {
            "range_high": round(range_high, 4),
            "range_low": round(range_low, 4),
            "range_pct": round(range_pct, 3),
            "range_threshold_pct": self.range_threshold_pct,
        }
        blocked = range_pct < self.range_threshold_pct
        return blocked, details

    # ────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> float | None:
        """คำนวณ EMA ล่าสุดจาก numpy array."""
        if len(data) < period:
            return None
        alpha = 2 / (period + 1)
        ema = float(data[0])
        for val in data[1:]:
            ema = alpha * float(val) + (1 - alpha) * ema
        return ema

    @staticmethod
    def _compute_adx(
        high: np.ndarray, low: np.ndarray, close: np.ndarray,
        period: int = 14,
    ) -> float | None:
        """คำนวณ ADX จาก numpy arrays (ไม่ต้องพึ่ง pandas_ta)."""
        n = len(close)
        if n < period * 2 + 1:
            return None

        # +DM / -DM
        plus_dm = np.maximum(high[1:] - high[:-1], 0)
        minus_dm = np.maximum(low[:-1] - low[1:], 0)

        # ถ้า +DM > -DM → ใช้ +DM, ถ้า -DM > +DM → ใช้ -DM
        mask_plus = plus_dm > minus_dm
        mask_minus = minus_dm > plus_dm
        plus_dm = np.where(mask_plus, plus_dm, 0)
        minus_dm = np.where(mask_minus, minus_dm, 0)

        # True Range
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1]),
            ),
        )

        # Wilder smoothing (RMA)
        def wilder_smooth(data: np.ndarray, p: int) -> np.ndarray:
            result = np.zeros_like(data)
            result[p - 1] = np.mean(data[:p])
            for i in range(p, len(data)):
                result[i] = (result[i - 1] * (p - 1) + data[i]) / p
            return result

        atr = wilder_smooth(tr, period)
        smooth_plus = wilder_smooth(plus_dm, period)
        smooth_minus = wilder_smooth(minus_dm, period)

        # +DI / -DI
        # Use np.divide to avoid RuntimeWarning on division by zero
        plus_di = np.divide(smooth_plus * 100, atr, out=np.zeros_like(atr), where=atr > 0)
        minus_di = np.divide(smooth_minus * 100, atr, out=np.zeros_like(atr), where=atr > 0)

        # DX
        di_sum = plus_di + minus_di
        dx = np.divide(np.abs(plus_di - minus_di) * 100, di_sum, out=np.zeros_like(di_sum), where=di_sum > 0)

        # ADX = Wilder smooth of DX
        adx = wilder_smooth(dx, period)

        last_adx = float(adx[-1])
        return last_adx if last_adx > 0 else None
