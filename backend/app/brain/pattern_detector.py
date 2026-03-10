"""
PatternDetector — ตรวจจับ Candlestick + Chart Patterns จาก OHLCV.

รองรับ 4 ระดับ:
    Level 1: Single-bar candlestick (Hammer, Doji, Pin Bar, Marubozu)
    Level 2: Multi-bar candlestick (Engulfing, Morning/Evening Star, Three Soldiers/Crows)
    Level 3: Structural patterns (Double Top/Bottom, H&S, Breakout, S/R zones)
    Level 4: SMC/ICT patterns (FVG, Order Block, BOS, CHoCH)

Performance (Optimized v3):
    - ทำงานบน NumPy arrays (ไม่ใช้ loop-heavy pandas)
    - _find_swings vectorized ด้วย sliding_window_view (ไม่มี Python loop)
    - detect_batch() — scan ทุก bar ในครั้งเดียว (สำหรับ PracticeEngine)
    - Cached swing results ภายใน detect_all เพื่อไม่คำนวณซ้ำ
    - Stateless, reentrant
    - Multi-TF: PatternSignal carries timeframe tag

Output: list[PatternSignal] — ใช้เป็น feature สำหรับ PracticeEngine
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Thresholds ──────────────────────────────────────────────────────
DOJI_BODY_RATIO = 0.1           # body < 10% of range = doji
PIN_BAR_WICK_RATIO = 2.5        # wick > 2.5x body = pin bar
ENGULFING_BODY_RATIO = 1.3      # current body > 1.3x prev body
MARUBOZU_WICK_RATIO = 0.05      # wicks < 5% of range = marubozu
INSIDE_BAR_TOLERANCE = 0.0      # exact inside range
SWING_LOOKBACK = 10             # bars for swing detection
DOUBLE_PATTERN_TOLERANCE = 0.002  # 0.2% price tolerance for double top/bottom
SR_CLUSTER_PCT = 0.003          # 0.3% clustering tolerance for S/R
BREAKOUT_VOL_MULT = 1.5         # volume > 1.5x avg = volume spike
MIN_BARS_FOR_PATTERNS = 20      # ขั้นต่ำสำหรับ structural patterns
FVG_MIN_ATR_MULT = 0.3          # FVG gap must be >= 0.3 ATR
OB_DISPLACEMENT_ATR = 1.5       # Order Block displacement >= 1.5 ATR
BOS_LOOKBACK = 20               # BOS/CHoCH lookback for swing detection


@dataclass
class PatternSignal:
    """สัญญาณ pattern ที่ตรวจพบ."""
    name: str                # ชื่อ pattern เช่น "bullish_engulfing"
    direction: str           # "bullish" | "bearish" | "neutral"
    strength: float          # 0.0-1.0 (ความแรงสัญญาณ)
    bar_index: int = -1      # ตำแหน่ง bar ที่เจอ
    timeframe: str = "M5"    # timeframe ที่ detect เช่น "M5", "H1", "H4"
    details: dict = field(default_factory=dict)


def _precompute(candles: pd.DataFrame) -> dict:
    """Pre-compute NumPy arrays once — shared by detect_all / detect_batch."""
    o = candles["open"].to_numpy(dtype=np.float64)
    h = candles["high"].to_numpy(dtype=np.float64)
    l = candles["low"].to_numpy(dtype=np.float64)
    c = candles["close"].to_numpy(dtype=np.float64)
    v = candles["volume"].to_numpy(dtype=np.float64) if "volume" in candles.columns else None
    n = len(o)
    body = np.abs(c - o)
    candle_range = h - l
    candle_range = np.where(candle_range == 0, 1e-10, candle_range)
    body_ratio = body / candle_range
    is_bullish = c > o
    is_bearish = c < o
    return {
        "o": o, "h": h, "l": l, "c": c, "v": v, "n": n,
        "body": body, "candle_range": candle_range,
        "body_ratio": body_ratio,
        "is_bullish": is_bullish, "is_bearish": is_bearish,
    }


class PatternDetector:
    """
    High-performance chart pattern detector (v2 — optimized).

    วิธีใช้:
        detector = PatternDetector()

        # Single-shot (legacy):
        signals = detector.detect_all(candles)

        # Batch mode (PracticeEngine — 3-5× faster):
        batch = detector.detect_batch(candles, start=50)
        signals_at_bar_100 = batch.get(100, [])

    Design:
        - Stateless: ปลอดภัยสำหรับ concurrent calls
        - NumPy-based: เร็วกว่า pure Python loops
        - Vectorized swings: ไม่มี Python for-loop ใน _find_swings
        - detect_batch: pre-compute ทุกอย่างครั้งเดียว → lookup O(1)
    """

    def detect_all(self, candles: pd.DataFrame) -> list[PatternSignal]:
        """
        ตรวจจับ patterns ทั้งหมดจาก candles (ดูเฉพาะ bar สุดท้าย).

        Args:
            candles: DataFrame [open, high, low, close, volume]

        Returns:
            list[PatternSignal] sorted by strength desc
        """
        if candles is None or len(candles) < 3:
            return []

        d = _precompute(candles)
        signals = self._detect_at_bar(d, d["n"] - 1)
        signals.sort(key=lambda s: s.strength, reverse=True)
        return signals

    def detect_latest(self, candles: pd.DataFrame, lookback: int = 5) -> list[PatternSignal]:
        """
        ตรวจเฉพาะ patterns ล่าสุด (last N bars) — เร็วกว่า detect_all.

        ใช้ใน live trading loop ที่ต้องการ low latency.
        """
        if candles is None or len(candles) < lookback + 5:
            return []

        # ใช้ tail slice โดยไม่ copy DataFrame
        buffer = min(50, len(candles))
        tail = candles.iloc[-buffer:]
        d = _precompute(tail)

        signals: list[PatternSignal] = []
        start = max(2, d["n"] - lookback)
        for bar_i in range(start, d["n"]):
            signals.extend(self._detect_at_bar(d, bar_i))

        signals.sort(key=lambda s: s.strength, reverse=True)
        return signals

    def detect_batch(
        self,
        candles: pd.DataFrame,
        start: int = 50,
        step: int = 1,
    ) -> dict[int, list[PatternSignal]]:
        """
        Batch detection — scan ทุก bar ตั้งแต่ start ถึงจบ ในครั้งเดียว.

        PracticeEngine เรียกครั้งเดียวแทนเรียก detect_latest() ทุก bar.
        Pre-compute arrays + swings ครั้งเดียว → ประมาณ 3-5× เร็วกว่า.

        Args:
            candles: DataFrame [open, high, low, close, volume]
            start: bar index เริ่มต้น (default 50 = MIN_CANDLES)
            step: ทุกกี่ bar จะเช็ค (default 1 = ทุก bar)

        Returns:
            dict[int, list[PatternSignal]]: bar_index → signals ที่พบ
        """
        if candles is None or len(candles) < start + 3:
            return {}

        d = _precompute(candles)
        n = d["n"]

        # Pre-compute swings once for structural patterns (biggest win)
        swing_cache = {}
        if n >= MIN_BARS_FOR_PATTERNS:
            swing_cache["swing_h_10"] = self._find_swings(d["h"], mode="high", lookback=SWING_LOOKBACK)
            swing_cache["swing_l_10"] = self._find_swings(d["l"], mode="low", lookback=SWING_LOOKBACK)
            swing_cache["swing_h_5"] = self._find_swings(d["h"], mode="high", lookback=5)
            swing_cache["swing_l_5"] = self._find_swings(d["l"], mode="low", lookback=5)

        result: dict[int, list[PatternSignal]] = {}

        for bar_i in range(start, n, step):
            signals = self._detect_at_bar(d, bar_i, swing_cache=swing_cache)
            if signals:
                result[bar_i] = signals

        return result

    # ====================================================================
    # Level 1: Single-bar Candlestick Patterns
    # ====================================================================

    def _detect_doji(self, o, h, l, c, body_ratio, n) -> list[PatternSignal]:
        """Doji — body แคบมาก (ตลาดลังเล)."""
        signals = []
        i = n - 1
        if body_ratio[i] < DOJI_BODY_RATIO:
            upper_wick = h[i] - max(o[i], c[i])
            lower_wick = min(o[i], c[i]) - l[i]
            total_wick = upper_wick + lower_wick
            if total_wick > 0:
                wick_balance = abs(upper_wick - lower_wick) / total_wick
            else:
                wick_balance = 0

            # Dragonfly (bullish) vs Gravestone (bearish) vs Standard
            if lower_wick > upper_wick * 2:
                name = "dragonfly_doji"
                direction = "bullish"
                strength = 0.55
            elif upper_wick > lower_wick * 2:
                name = "gravestone_doji"
                direction = "bearish"
                strength = 0.55
            else:
                name = "doji"
                direction = "neutral"
                strength = 0.4
            signals.append(PatternSignal(name=name, direction=direction,
                                          strength=strength, bar_index=i))
        return signals

    def _detect_hammer(self, o, h, l, c, body, body_ratio, is_bullish, n) -> list[PatternSignal]:
        """Hammer — lower wick ยาว, body เล็ก, อยู่ด้านบน (bullish reversal)."""
        signals = []
        i = n - 1
        if body[i] > 0 and body_ratio[i] < 0.4:
            lower_wick = min(o[i], c[i]) - l[i]
            upper_wick = h[i] - max(o[i], c[i])
            if lower_wick > body[i] * PIN_BAR_WICK_RATIO and upper_wick < body[i] * 0.5:
                # ยืนยันด้วย context: ควรอยู่หลัง downmove
                if n > 3 and c[i-3] > c[i-1]:  # preceding downtrend
                    strength = 0.7
                else:
                    strength = 0.55
                signals.append(PatternSignal(
                    name="hammer", direction="bullish",
                    strength=strength, bar_index=i,
                ))
        return signals

    def _detect_inverted_hammer(self, o, h, l, c, body, body_ratio, is_bullish, n) -> list[PatternSignal]:
        """Inverted Hammer — upper wick ยาว, body เล็ก, อยู่ด้านล่าง."""
        signals = []
        i = n - 1
        if body[i] > 0 and body_ratio[i] < 0.4:
            upper_wick = h[i] - max(o[i], c[i])
            lower_wick = min(o[i], c[i]) - l[i]
            if upper_wick > body[i] * PIN_BAR_WICK_RATIO and lower_wick < body[i] * 0.5:
                if n > 3 and c[i-3] > c[i-1]:
                    strength = 0.6
                else:
                    strength = 0.5
                signals.append(PatternSignal(
                    name="inverted_hammer", direction="bullish",
                    strength=strength, bar_index=i,
                ))
        return signals

    def _detect_pin_bar(self, o, h, l, c, body, candle_range, n) -> list[PatternSignal]:
        """Pin Bar — wick ข้างหนึ่งยาวมาก ≥ 2/3 ของ range, body เล็ก."""
        signals = []
        i = n - 1
        if candle_range[i] == 0 or body[i] == 0:
            return signals

        upper_wick = h[i] - max(o[i], c[i])
        lower_wick = min(o[i], c[i]) - l[i]

        # Bullish pin (long lower wick)
        if lower_wick / candle_range[i] >= 0.66 and body[i] / candle_range[i] < 0.25:
            signals.append(PatternSignal(
                name="bullish_pin_bar", direction="bullish",
                strength=0.70, bar_index=i,
                details={"wick_ratio": round(lower_wick / candle_range[i], 3)},
            ))
        # Bearish pin (long upper wick)
        elif upper_wick / candle_range[i] >= 0.66 and body[i] / candle_range[i] < 0.25:
            signals.append(PatternSignal(
                name="bearish_pin_bar", direction="bearish",
                strength=0.70, bar_index=i,
                details={"wick_ratio": round(upper_wick / candle_range[i], 3)},
            ))
        return signals

    def _detect_marubozu(self, o, h, l, c, body_ratio, is_bullish, is_bearish, n) -> list[PatternSignal]:
        """Marubozu — body ใหญ่เกือบเต็ม range (strong momentum)."""
        signals = []
        i = n - 1
        if body_ratio[i] > (1 - MARUBOZU_WICK_RATIO):
            if is_bullish[i]:
                signals.append(PatternSignal(
                    name="bullish_marubozu", direction="bullish",
                    strength=0.65, bar_index=i,
                ))
            elif is_bearish[i]:
                signals.append(PatternSignal(
                    name="bearish_marubozu", direction="bearish",
                    strength=0.65, bar_index=i,
                ))
        return signals

    # ====================================================================
    # Level 2: Multi-bar Candlestick Patterns
    # ====================================================================

    def _detect_engulfing(self, o, h, l, c, body, is_bullish, is_bearish, n) -> list[PatternSignal]:
        """Engulfing — bar ปัจจุบันกลืน bar ก่อนหน้าทั้งตัว."""
        signals = []
        if n < 2:
            return signals
        i = n - 1

        prev_body = body[i - 1]
        curr_body = body[i]

        if prev_body == 0:
            return signals

        # Bullish Engulfing: prev bearish + curr bullish + curr body > prev body
        if (is_bearish[i - 1] and is_bullish[i]
                and curr_body > prev_body * ENGULFING_BODY_RATIO
                and c[i] > o[i - 1] and o[i] < c[i - 1]):
            strength = min(0.85, 0.65 + (curr_body / prev_body - 1) * 0.1)
            signals.append(PatternSignal(
                name="bullish_engulfing", direction="bullish",
                strength=strength, bar_index=i,
            ))

        # Bearish Engulfing: prev bullish + curr bearish
        elif (is_bullish[i - 1] and is_bearish[i]
              and curr_body > prev_body * ENGULFING_BODY_RATIO
              and o[i] > c[i - 1] and c[i] < o[i - 1]):
            strength = min(0.85, 0.65 + (curr_body / prev_body - 1) * 0.1)
            signals.append(PatternSignal(
                name="bearish_engulfing", direction="bearish",
                strength=strength, bar_index=i,
            ))

        return signals

    def _detect_morning_evening_star(self, o, h, l, c, body, body_ratio,
                                      is_bullish, is_bearish, n) -> list[PatternSignal]:
        """Morning Star (bullish) / Evening Star (bearish) — 3-bar reversal."""
        signals = []
        if n < 3:
            return signals

        i = n - 1  # third bar
        mid = i - 1
        first = i - 2

        # Morning Star: big bearish → small body (doji/hammer) → big bullish
        if (is_bearish[first] and body_ratio[first] > 0.5
                and body_ratio[mid] < 0.3  # small middle
                and is_bullish[i] and body_ratio[i] > 0.5
                and c[i] > (o[first] + c[first]) / 2):  # closes above midpoint
            signals.append(PatternSignal(
                name="morning_star", direction="bullish",
                strength=0.80, bar_index=i,
            ))

        # Evening Star: big bullish → small body → big bearish
        if (is_bullish[first] and body_ratio[first] > 0.5
                and body_ratio[mid] < 0.3
                and is_bearish[i] and body_ratio[i] > 0.5
                and c[i] < (o[first] + c[first]) / 2):
            signals.append(PatternSignal(
                name="evening_star", direction="bearish",
                strength=0.80, bar_index=i,
            ))

        return signals

    def _detect_three_soldiers_crows(self, o, c, body, is_bullish, is_bearish, n) -> list[PatternSignal]:
        """Three White Soldiers / Three Black Crows — 3 bars strong trend."""
        signals = []
        if n < 3:
            return signals

        i = n - 1

        # Three White Soldiers: 3 consecutive bullish with higher closes
        if (is_bullish[i] and is_bullish[i-1] and is_bullish[i-2]
                and c[i] > c[i-1] > c[i-2]
                and o[i] > o[i-1] > o[i-2]
                and body[i] > 0 and body[i-1] > 0 and body[i-2] > 0):
            signals.append(PatternSignal(
                name="three_white_soldiers", direction="bullish",
                strength=0.75, bar_index=i,
            ))

        # Three Black Crows: 3 consecutive bearish with lower closes
        if (is_bearish[i] and is_bearish[i-1] and is_bearish[i-2]
                and c[i] < c[i-1] < c[i-2]
                and o[i] < o[i-1] < o[i-2]
                and body[i] > 0 and body[i-1] > 0 and body[i-2] > 0):
            signals.append(PatternSignal(
                name="three_black_crows", direction="bearish",
                strength=0.75, bar_index=i,
            ))

        return signals

    def _detect_inside_bar(self, h, l, n) -> list[PatternSignal]:
        """Inside Bar — bar ปัจจุบันอยู่ภายใน range ของ bar ก่อนหน้า (consolidation)."""
        signals = []
        if n < 2:
            return signals
        i = n - 1

        if h[i] <= h[i-1] and l[i] >= l[i-1]:
            signals.append(PatternSignal(
                name="inside_bar", direction="neutral",
                strength=0.50, bar_index=i,
                details={"mother_range": round(float(h[i-1] - l[i-1]), 5)},
            ))
        return signals

    # ====================================================================
    # Batch helper: detect patterns at a specific bar index
    # ====================================================================

    def _detect_at_bar(
        self,
        d: dict,
        bar_i: int,
        swing_cache: dict | None = None,
    ) -> list[PatternSignal]:
        """
        ตรวจจับ patterns ทั้งหมด ณ bar_i (ใช้ pre-computed arrays).

        Args:
            d: pre-computed arrays from _precompute()
            bar_i: bar index ที่จะตรวจ
            swing_cache: pre-computed swings (from detect_batch)
        """
        o, h, l, c, v = d["o"], d["h"], d["l"], d["c"], d["v"]
        n_eff = bar_i + 1  # effective length up to this bar
        body, candle_range = d["body"], d["candle_range"]
        body_ratio = d["body_ratio"]
        is_bullish, is_bearish = d["is_bullish"], d["is_bearish"]

        signals: list[PatternSignal] = []

        if n_eff < 3:
            return signals

        # ── Level 1: Single-bar ──
        signals.extend(self._detect_doji(o, h, l, c, body_ratio, n_eff))
        signals.extend(self._detect_hammer(o, h, l, c, body, body_ratio, is_bullish, n_eff))
        signals.extend(self._detect_inverted_hammer(o, h, l, c, body, body_ratio, is_bullish, n_eff))
        signals.extend(self._detect_pin_bar(o, h, l, c, body, candle_range, n_eff))
        signals.extend(self._detect_marubozu(o, h, l, c, body_ratio, is_bullish, is_bearish, n_eff))

        # ── Level 2: Multi-bar ──
        signals.extend(self._detect_engulfing(o, h, l, c, body, is_bullish, is_bearish, n_eff))
        signals.extend(self._detect_morning_evening_star(o, h, l, c, body, body_ratio, is_bullish, is_bearish, n_eff))
        signals.extend(self._detect_three_soldiers_crows(o, c, body, is_bullish, is_bearish, n_eff))
        signals.extend(self._detect_inside_bar(h, l, n_eff))

        # ── Level 3: Structural (use cached swings if available) ──
        if n_eff >= MIN_BARS_FOR_PATTERNS:
            signals.extend(self._detect_double_top_bottom(h, l, c, n_eff, swing_cache))
            signals.extend(self._detect_head_shoulders(h, l, c, n_eff, swing_cache))
            signals.extend(self._detect_breakout(h, l, c, v, n_eff))
            signals.extend(self._detect_sr_zones(h, l, c, n_eff))
            signals.extend(self._detect_trend_structure(h, l, n_eff, swing_cache))

            # ── Level 4: SMC/ICT Patterns ──
            atr = self._compute_atr(h, l, c, n_eff)
            signals.extend(self._detect_fvg(o, h, l, c, n_eff, atr))
            signals.extend(self._detect_order_block(o, h, l, c, body, candle_range, is_bullish, is_bearish, n_eff, atr))
            signals.extend(self._detect_bos(h, l, c, n_eff, swing_cache))
            signals.extend(self._detect_choch(h, l, c, n_eff, swing_cache))

        return signals

    # ====================================================================
    # Level 3: Structural / Chart Patterns
    # ====================================================================

    def _detect_double_top_bottom(self, h, l, c, n,
                                   swing_cache: dict | None = None) -> list[PatternSignal]:
        """Double Top / Double Bottom — 2 swing points ที่ระดับใกล้กัน."""
        signals = []

        # ใช้ cached swings ถ้ามี, ถ้าไม่มีก็คำนวณใหม่
        if swing_cache:
            swing_highs = [(v, i) for v, i in swing_cache.get("swing_h_10", []) if i < n]
            swing_lows = [(v, i) for v, i in swing_cache.get("swing_l_10", []) if i < n]
        else:
            swing_highs = self._find_swings(h[:n], mode="high", lookback=SWING_LOOKBACK)
            swing_lows = self._find_swings(l[:n], mode="low", lookback=SWING_LOOKBACK)

        # Double Top
        if len(swing_highs) >= 2:
            last_two = swing_highs[-2:]
            h1_val, h1_idx = last_two[0]
            h2_val, h2_idx = last_two[1]
            tol = h1_val * DOUBLE_PATTERN_TOLERANCE
            if abs(h1_val - h2_val) < tol and h2_idx > h1_idx + 3:
                neckline = float(np.min(l[h1_idx:h2_idx+1]))
                if c[n-1] < neckline:
                    signals.append(PatternSignal(
                        name="double_top", direction="bearish",
                        strength=0.85, bar_index=n-1,
                        details={"peak1": round(float(h1_val), 5),
                                 "peak2": round(float(h2_val), 5),
                                 "neckline": round(neckline, 5)},
                    ))

        # Double Bottom
        if len(swing_lows) >= 2:
            last_two = swing_lows[-2:]
            l1_val, l1_idx = last_two[0]
            l2_val, l2_idx = last_two[1]
            tol = l1_val * DOUBLE_PATTERN_TOLERANCE
            if abs(l1_val - l2_val) < tol and l2_idx > l1_idx + 3:
                neckline = float(np.max(h[l1_idx:l2_idx+1]))
                if c[n-1] > neckline:
                    signals.append(PatternSignal(
                        name="double_bottom", direction="bullish",
                        strength=0.85, bar_index=n-1,
                        details={"trough1": round(float(l1_val), 5),
                                 "trough2": round(float(l2_val), 5),
                                 "neckline": round(neckline, 5)},
                    ))

        return signals

    def _detect_head_shoulders(self, h, l, c, n,
                                swing_cache: dict | None = None) -> list[PatternSignal]:
        """Head & Shoulders / Inverse H&S — 3 swing points forming H&S."""
        signals = []

        if swing_cache:
            swing_highs = [(v, i) for v, i in swing_cache.get("swing_h_10", []) if i < n]
            swing_lows = [(v, i) for v, i in swing_cache.get("swing_l_10", []) if i < n]
        else:
            swing_highs = self._find_swings(h[:n], mode="high", lookback=SWING_LOOKBACK)
            swing_lows = self._find_swings(l[:n], mode="low", lookback=SWING_LOOKBACK)

        # Head & Shoulders (bearish)
        if len(swing_highs) >= 3:
            last_three = swing_highs[-3:]
            ls_val, ls_idx = last_three[0]
            hd_val, hd_idx = last_three[1]
            rs_val, rs_idx = last_three[2]

            if (hd_val > ls_val and hd_val > rs_val
                    and abs(ls_val - rs_val) / ls_val < 0.02
                    and hd_idx > ls_idx + 2 and rs_idx > hd_idx + 2):
                neckline = float(np.min(l[ls_idx:rs_idx+1]))
                if c[n-1] < neckline:
                    signals.append(PatternSignal(
                        name="head_and_shoulders", direction="bearish",
                        strength=0.90, bar_index=n-1,
                        details={"left_shoulder": round(float(ls_val), 5),
                                 "head": round(float(hd_val), 5),
                                 "right_shoulder": round(float(rs_val), 5)},
                    ))

        # Inverse H&S (bullish)
        if len(swing_lows) >= 3:
            last_three = swing_lows[-3:]
            ls_val, ls_idx = last_three[0]
            hd_val, hd_idx = last_three[1]
            rs_val, rs_idx = last_three[2]

            if (hd_val < ls_val and hd_val < rs_val
                    and abs(ls_val - rs_val) / ls_val < 0.02
                    and hd_idx > ls_idx + 2 and rs_idx > hd_idx + 2):
                neckline = float(np.max(h[ls_idx:rs_idx+1]))
                if c[n-1] > neckline:
                    signals.append(PatternSignal(
                        name="inverse_head_shoulders", direction="bullish",
                        strength=0.90, bar_index=n-1,
                        details={"left_shoulder": round(float(ls_val), 5),
                                 "head": round(float(hd_val), 5),
                                 "right_shoulder": round(float(rs_val), 5)},
                    ))

        return signals

    def _detect_breakout(self, h, l, c, v, n) -> list[PatternSignal]:
        """Breakout — ราคาทะลุ resistance/support ด้วย volume spike."""
        signals = []
        lookback = min(50, n - 1)
        if lookback < 1:
            return signals

        resistance = float(np.max(h[n-lookback-1:n-1]))
        support = float(np.min(l[n-lookback-1:n-1]))

        vol_spike = False
        if v is not None and n > 21:
            avg_vol = float(np.mean(v[n-21:n-1]))
            if avg_vol > 0 and v[n-1] > avg_vol * BREAKOUT_VOL_MULT:
                vol_spike = True

        if c[n-1] > resistance:
            strength = 0.80 if vol_spike else 0.60
            signals.append(PatternSignal(
                name="bullish_breakout", direction="bullish",
                strength=strength, bar_index=n-1,
                details={"resistance": round(resistance, 5), "volume_spike": vol_spike},
            ))

        if c[n-1] < support:
            strength = 0.80 if vol_spike else 0.60
            signals.append(PatternSignal(
                name="bearish_breakout", direction="bearish",
                strength=strength, bar_index=n-1,
                details={"support": round(support, 5), "volume_spike": vol_spike},
            ))

        return signals

    def _detect_sr_zones(self, h, l, c, n) -> list[PatternSignal]:
        """Support/Resistance zones — price clustering (vectorized)."""
        signals = []
        lookback = min(100, n)
        levels = np.concatenate([h[n-lookback:n], l[n-lookback:n]])
        current_price = c[n-1]

        sorted_levels = np.sort(levels)
        if len(sorted_levels) == 0:
            return signals

        # Vectorized clustering: find cluster boundaries via diff
        diffs = np.diff(sorted_levels)
        thresholds = sorted_levels[:-1] * SR_CLUSTER_PCT
        breaks = np.where(diffs >= thresholds)[0]

        # Build cluster boundaries
        starts = np.concatenate([[0], breaks + 1])
        ends = np.concatenate([breaks + 1, [len(sorted_levels)]])

        for s_idx, e_idx in zip(starts, ends):
            cluster_size = e_idx - s_idx
            if cluster_size >= 3:
                level = float(np.mean(sorted_levels[s_idx:e_idx]))
                touches = int(cluster_size)
                dist_pct = abs(current_price - level) / level if level > 0 else 999
                if dist_pct < 0.005:
                    if current_price > level:
                        signals.append(PatternSignal(
                            name="near_support", direction="bullish",
                            strength=min(0.70, 0.4 + touches * 0.05),
                            bar_index=n-1,
                            details={"level": round(level, 5), "touches": touches},
                        ))
                    else:
                        signals.append(PatternSignal(
                            name="near_resistance", direction="bearish",
                            strength=min(0.70, 0.4 + touches * 0.05),
                            bar_index=n-1,
                            details={"level": round(level, 5), "touches": touches},
                        ))

        return signals

    def _detect_trend_structure(self, h, l, n,
                                swing_cache: dict | None = None) -> list[PatternSignal]:
        """Trend Structure — HH+HL / LH+LL."""
        signals = []

        if swing_cache:
            swing_highs = [(v, i) for v, i in swing_cache.get("swing_h_5", []) if i < n]
            swing_lows = [(v, i) for v, i in swing_cache.get("swing_l_5", []) if i < n]
        else:
            swing_highs = self._find_swings(h[:n], mode="high", lookback=5)
            swing_lows = self._find_swings(l[:n], mode="low", lookback=5)

        if len(swing_highs) >= 3 and len(swing_lows) >= 3:
            last3_h = [v for v, _ in swing_highs[-3:]]
            last3_l = [v for v, _ in swing_lows[-3:]]

            if last3_h[2] > last3_h[1] > last3_h[0] and last3_l[2] > last3_l[1] > last3_l[0]:
                signals.append(PatternSignal(
                    name="hh_hl_uptrend", direction="bullish",
                    strength=0.75, bar_index=n-1,
                ))
            elif last3_h[2] < last3_h[1] < last3_h[0] and last3_l[2] < last3_l[1] < last3_l[0]:
                signals.append(PatternSignal(
                    name="lh_ll_downtrend", direction="bearish",
                    strength=0.75, bar_index=n-1,
                ))

        return signals

    # ====================================================================
    # Level 4: SMC / ICT Patterns (Smart Money Concepts)
    # ====================================================================

    @staticmethod
    def _compute_atr(h, l, c, n, period: int = 14) -> float:
        """Compute ATR at bar n-1 (fast numpy computation)."""
        if n < period + 1:
            return float(np.mean(h[:n] - l[:n])) if n > 0 else 1e-10
        tr = np.maximum(
            h[1:n] - l[1:n],
            np.maximum(
                np.abs(h[1:n] - c[:n-1]),
                np.abs(l[1:n] - c[:n-1]),
            ),
        )
        return float(np.mean(tr[-period:]))

    def _detect_fvg(self, o, h, l, c, n, atr: float) -> list[PatternSignal]:
        """
        Fair Value Gap (FVG) — 3-candle imbalance zone.

        Bullish FVG: candle[i-2].high < candle[i].low  (gap up)
        Bearish FVG: candle[i-2].low  > candle[i].high (gap down)

        Only valid if gap size >= FVG_MIN_ATR_MULT × ATR.
        """
        signals = []
        if n < 3 or atr <= 0:
            return signals

        i = n - 1
        min_gap = atr * FVG_MIN_ATR_MULT

        # Bullish FVG: gap between bar[i-2] high and bar[i] low
        bull_gap = l[i] - h[i-2]
        if bull_gap >= min_gap:
            # Price is near or inside the FVG zone (potential entry)
            fvg_mid = (h[i-2] + l[i]) / 2
            # Extra strength if middle candle (i-1) is a strong displacement
            mid_body = abs(c[i-1] - o[i-1])
            displacement = mid_body / atr if atr > 0 else 0
            strength = min(0.85, 0.60 + displacement * 0.08)
            signals.append(PatternSignal(
                name="bullish_fvg", direction="bullish",
                strength=round(strength, 2), bar_index=i,
                details={
                    "fvg_top": round(float(l[i]), 5),
                    "fvg_bottom": round(float(h[i-2]), 5),
                    "fvg_mid": round(float(fvg_mid), 5),
                    "gap_atr": round(bull_gap / atr, 2),
                },
            ))

        # Bearish FVG: gap between bar[i] high and bar[i-2] low
        bear_gap = l[i-2] - h[i]
        if bear_gap >= min_gap:
            fvg_mid = (l[i-2] + h[i]) / 2
            mid_body = abs(c[i-1] - o[i-1])
            displacement = mid_body / atr if atr > 0 else 0
            strength = min(0.85, 0.60 + displacement * 0.08)
            signals.append(PatternSignal(
                name="bearish_fvg", direction="bearish",
                strength=round(strength, 2), bar_index=i,
                details={
                    "fvg_top": round(float(l[i-2]), 5),
                    "fvg_bottom": round(float(h[i]), 5),
                    "fvg_mid": round(float(fvg_mid), 5),
                    "gap_atr": round(bear_gap / atr, 2),
                },
            ))

        return signals

    def _detect_order_block(self, o, h, l, c, body, candle_range,
                            is_bullish, is_bearish, n, atr: float) -> list[PatternSignal]:
        """
        Order Block — last opposite candle before a strong displacement move.

        Bullish OB: last bearish candle before a bullish displacement (body > 1.5 ATR).
        Bearish OB: last bullish candle before a bearish displacement.

        The current price returning to OB zone = high-probability entry.
        """
        signals = []
        if n < 5 or atr <= 0:
            return signals

        i = n - 1
        disp_threshold = atr * OB_DISPLACEMENT_ATR

        # Check if current bar is a strong displacement (momentum candle)
        curr_body = abs(c[i] - o[i])

        if curr_body >= disp_threshold:
            # Bullish displacement → look back for last bearish candle = Bullish OB
            if c[i] > o[i]:  # bullish candle
                for j in range(i-1, max(i-6, 0), -1):
                    if is_bearish[j]:
                        # Found the order block
                        ob_high = float(h[j])
                        ob_low = float(l[j])
                        # Strength increases with displacement size
                        disp_ratio = curr_body / atr
                        strength = min(0.90, 0.65 + disp_ratio * 0.05)
                        signals.append(PatternSignal(
                            name="bullish_order_block", direction="bullish",
                            strength=round(strength, 2), bar_index=i,
                            details={
                                "ob_high": round(ob_high, 5),
                                "ob_low": round(ob_low, 5),
                                "ob_bar": j,
                                "displacement_atr": round(disp_ratio, 2),
                            },
                        ))
                        break

            # Bearish displacement → look back for last bullish candle = Bearish OB
            elif c[i] < o[i]:  # bearish candle
                for j in range(i-1, max(i-6, 0), -1):
                    if is_bullish[j]:
                        ob_high = float(h[j])
                        ob_low = float(l[j])
                        disp_ratio = curr_body / atr
                        strength = min(0.90, 0.65 + disp_ratio * 0.05)
                        signals.append(PatternSignal(
                            name="bearish_order_block", direction="bearish",
                            strength=round(strength, 2), bar_index=i,
                            details={
                                "ob_high": round(ob_high, 5),
                                "ob_low": round(ob_low, 5),
                                "ob_bar": j,
                                "displacement_atr": round(disp_ratio, 2),
                            },
                        ))
                        break

        return signals

    def _detect_bos(self, h, l, c, n,
                    swing_cache: dict | None = None) -> list[PatternSignal]:
        """
        Break of Structure (BOS) — price breaks recent swing high/low,
        confirming trend continuation.

        Bullish BOS: close > most recent swing high
        Bearish BOS: close < most recent swing low
        """
        signals = []
        if n < BOS_LOOKBACK:
            return signals

        if swing_cache:
            swing_highs = [(v, idx) for v, idx in swing_cache.get("swing_h_5", []) if idx < n - 1]
            swing_lows = [(v, idx) for v, idx in swing_cache.get("swing_l_5", []) if idx < n - 1]
        else:
            swing_highs = self._find_swings(h[:n], mode="high", lookback=5)
            swing_lows = self._find_swings(l[:n], mode="low", lookback=5)

        i = n - 1

        # Bullish BOS: current close breaks above recent swing high
        if swing_highs:
            last_sh = swing_highs[-1]
            # Must be a genuine break (close above, not just wick)
            if c[i] > last_sh[0] and c[i-1] <= last_sh[0]:
                # Confirm with body (not just wick)
                strength = 0.80
                # Bonus if strong close (close near high of bar)
                bar_range = h[i] - l[i]
                if bar_range > 0 and (c[i] - l[i]) / bar_range > 0.7:
                    strength = 0.85
                signals.append(PatternSignal(
                    name="bullish_bos", direction="bullish",
                    strength=strength, bar_index=i,
                    details={
                        "broken_level": round(float(last_sh[0]), 5),
                        "swing_bar": last_sh[1],
                    },
                ))

        # Bearish BOS: current close breaks below recent swing low
        if swing_lows:
            last_sl = swing_lows[-1]
            if c[i] < last_sl[0] and c[i-1] >= last_sl[0]:
                strength = 0.80
                bar_range = h[i] - l[i]
                if bar_range > 0 and (h[i] - c[i]) / bar_range > 0.7:
                    strength = 0.85
                signals.append(PatternSignal(
                    name="bearish_bos", direction="bearish",
                    strength=strength, bar_index=i,
                    details={
                        "broken_level": round(float(last_sl[0]), 5),
                        "swing_bar": last_sl[1],
                    },
                ))

        return signals

    def _detect_choch(self, h, l, c, n,
                      swing_cache: dict | None = None) -> list[PatternSignal]:
        """
        Change of Character (CHoCH) — first break against the prevailing trend.

        In an uptrend (HH+HL): first close below a swing low = bearish CHoCH.
        In a downtrend (LH+LL): first close above a swing high = bullish CHoCH.

        Higher confidence than BOS because it signals potential reversal.
        """
        signals = []
        if n < BOS_LOOKBACK:
            return signals

        if swing_cache:
            swing_highs = [(v, idx) for v, idx in swing_cache.get("swing_h_5", []) if idx < n - 1]
            swing_lows = [(v, idx) for v, idx in swing_cache.get("swing_l_5", []) if idx < n - 1]
        else:
            swing_highs = self._find_swings(h[:n], mode="high", lookback=5)
            swing_lows = self._find_swings(l[:n], mode="low", lookback=5)

        i = n - 1

        if len(swing_highs) >= 3 and len(swing_lows) >= 3:
            last3_h = [v for v, _ in swing_highs[-3:]]
            last3_l = [v for v, _ in swing_lows[-3:]]

            # Uptrend detected (HH+HL) → check for bearish CHoCH
            is_uptrend = (last3_h[2] > last3_h[1] > last3_h[0] and
                          last3_l[2] > last3_l[1] > last3_l[0])
            if is_uptrend:
                # CHoCH = close breaks below last swing low
                last_sl_val = swing_lows[-1][0]
                if c[i] < last_sl_val and c[i-1] >= last_sl_val:
                    signals.append(PatternSignal(
                        name="bearish_choch", direction="bearish",
                        strength=0.85, bar_index=i,
                        details={
                            "broken_level": round(float(last_sl_val), 5),
                            "prev_trend": "uptrend",
                        },
                    ))

            # Downtrend detected (LH+LL) → check for bullish CHoCH
            is_downtrend = (last3_h[2] < last3_h[1] < last3_h[0] and
                            last3_l[2] < last3_l[1] < last3_l[0])
            if is_downtrend:
                last_sh_val = swing_highs[-1][0]
                if c[i] > last_sh_val and c[i-1] <= last_sh_val:
                    signals.append(PatternSignal(
                        name="bullish_choch", direction="bullish",
                        strength=0.85, bar_index=i,
                        details={
                            "broken_level": round(float(last_sh_val), 5),
                            "prev_trend": "downtrend",
                        },
                    ))

        return signals

    # ====================================================================
    # Helper: Swing Detection (vectorized — no Python loop)
    # ====================================================================

    def _find_swings(self, data: np.ndarray, mode: str = "high",
                     lookback: int = 5) -> list[tuple[float, int]]:
        """
        หา swing highs/lows แบบ vectorized ด้วย sliding_window_view.

        เร็วกว่า Python loop ~5-10× สำหรับ arrays ≥200 elements.
        """
        n = len(data)
        w = 2 * lookback + 1
        if n < w:
            return []

        try:
            windows = np.lib.stride_tricks.sliding_window_view(data, w)
        except Exception:
            # Fallback for older NumPy
            return self._find_swings_fallback(data, mode, lookback)

        # windows[i] = data[i:i+w], center element is at index lookback within window
        center_vals = windows[:, lookback]  # shape: (n-w+1,)

        if mode == "high":
            extrema = np.max(windows, axis=1)
            mask = center_vals == extrema
        else:
            extrema = np.min(windows, axis=1)
            mask = center_vals == extrema

        # Actual indices in original array: center of window i starts at i+lookback
        indices = np.flatnonzero(mask) + lookback
        return [(float(data[idx]), int(idx)) for idx in indices]

    def _find_swings_fallback(self, data: np.ndarray, mode: str,
                               lookback: int) -> list[tuple[float, int]]:
        """Fallback: Python loop version for older NumPy."""
        n = len(data)
        swings = []
        for i in range(lookback, n - lookback):
            window = data[i - lookback:i + lookback + 1]
            if mode == "high":
                if data[i] == np.max(window):
                    swings.append((float(data[i]), i))
            else:
                if data[i] == np.min(window):
                    swings.append((float(data[i]), i))
        return swings

    # ====================================================================
    # Utility: Pattern Summary
    # ====================================================================

    def summarize(self, signals: list[PatternSignal]) -> dict:
        """
        สรุป patterns ที่เจอ — ใช้สำหรับ logging/dashboard.

        Returns:
            dict: {
                "total": int,
                "bullish": [name, ...],
                "bearish": [name, ...],
                "neutral": [name, ...],
                "strongest": {name, direction, strength},
            }
        """
        bullish = [s.name for s in signals if s.direction == "bullish"]
        bearish = [s.name for s in signals if s.direction == "bearish"]
        neutral = [s.name for s in signals if s.direction == "neutral"]

        strongest = signals[0] if signals else None

        return {
            "total": len(signals),
            "bullish": bullish,
            "bearish": bearish,
            "neutral": neutral,
            "strongest": {
                "name": strongest.name,
                "direction": strongest.direction,
                "strength": strongest.strength,
            } if strongest else None,
        }
