"""
PatternDiscovery — Auto-discover chart patterns ด้วย KMeans clustering.

วิธีทำงาน:
    1. ตัด OHLCV เป็น sliding windows (20 bars)
    2. Normalize แต่ละ window เป็น 0-1 (relative to window range)
    3. KMeans clustering → grouping similar price movements
    4. Label แต่ละ cluster ด้วย win_rate จากผลเทรดจริง
    5. Auto-name clusters ตาม dominant feature
    6. เก็บ cluster centroids ใน brain.db

RAM Safety (8GB mode):
    - Process ทีละ batch (max 500 windows)
    - เก็บแค่ cluster centroids (20 × 80 floats = 6KB)
    - รันเฉพาะใน training cycle (ทุก 6 ชม.)

กฎ:
    - Discovered patterns เป็น advisor เท่านั้น
    - ใช้เสริม rule-based PatternDetector (ไม่แทนที่)
"""

import json
import time
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── Config ───
N_CLUSTERS = 20           # จำนวน pattern clusters
WINDOW_SIZE = 20           # bars per window
BATCH_SIZE = 500           # max windows per batch (RAM safety)
MIN_CLUSTER_SIZE = 5       # ขั้นต่ำต่อ cluster ก่อนรายงาน
OHLC_FEATURES = 4          # open, high, low, close per bar
FEATURE_DIM = WINDOW_SIZE * OHLC_FEATURES  # 20 × 4 = 80

# ─── Auto-naming rules ───
# ดูจาก centroid shape เพื่อตั้งชื่ออัตโนมัติ
PATTERN_NAME_RULES = {
    "V_REVERSAL_UP": lambda c: c[-1] > c[0] and min(c) < c[0] * 0.95,
    "V_REVERSAL_DOWN": lambda c: c[-1] < c[0] and max(c) > c[0] * 1.05,
    "BREAKOUT_UP": lambda c: c[-1] > c[0] * 1.02 and np.std(c[-5:]) > np.std(c[:5]),
    "BREAKOUT_DOWN": lambda c: c[-1] < c[0] * 0.98 and np.std(c[-5:]) > np.std(c[:5]),
    "CONSOLIDATION": lambda c: np.std(c) < 0.05,
    "UPTREND": lambda c: np.corrcoef(range(len(c)), c)[0, 1] > 0.7,
    "DOWNTREND": lambda c: np.corrcoef(range(len(c)), c)[0, 1] < -0.7,
    "RANGE_BOUND": lambda c: np.std(c) < 0.1 and abs(c[-1] - c[0]) < 0.02,
}


class DiscoveredPattern:
    """Pattern ที่ค้นพบด้วย clustering."""

    def __init__(
        self,
        pattern_id: str,
        label: str,
        centroid: list[float],
        total_examples: int = 0,
        win_rate: float = 0.0,
        avg_r: float = 0.0,
    ) -> None:
        self.pattern_id = pattern_id
        self.label = label
        self.centroid = centroid
        self.total_examples = total_examples
        self.win_rate = win_rate
        self.avg_r = avg_r

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "label": self.label,
            "total_examples": self.total_examples,
            "win_rate": round(self.win_rate, 3),
            "avg_r": round(self.avg_r, 3),
        }


class PatternDiscovery:
    """
    Auto-discover chart patterns ด้วย KMeans clustering.

    ระบบจะค้นหา recurring price patterns จากข้อมูลจริง
    แล้วเก็บ cluster centroids สำหรับใช้ใน trading.
    """

    def __init__(self, memory_store=None) -> None:
        self.memory = memory_store
        self._patterns: list[DiscoveredPattern] = []
        self._kmeans = None
        self._last_discovery_time: float = 0.0

    @property
    def patterns(self) -> list[DiscoveredPattern]:
        return self._patterns

    # ────────────────────────────────────────────────────────────────
    # discover() — ค้นหา patterns จาก candles
    # ────────────────────────────────────────────────────────────────

    async def discover(
        self,
        candles: pd.DataFrame,
        trade_outcomes: list[dict] | None = None,
    ) -> list[DiscoveredPattern]:
        """
        Discover patterns จาก historical candles.

        Args:
            candles: DataFrame [open, high, low, close, volume]
            trade_outcomes: [{bar_index: int, profit: float, rr: float}, ...]
                           ถ้ามี → ใช้ label clusters ด้วย win_rate

        Returns:
            list[DiscoveredPattern]: patterns ที่ค้นพบ
        """
        if candles is None or len(candles) < WINDOW_SIZE + 10:
            return []

        try:
            # ─── 1. Extract windows ───
            windows = self._extract_windows(candles)
            if len(windows) < N_CLUSTERS:
                logger.debug("pattern_discovery_insufficient_data", extra={
                    "windows": len(windows),
                    "required": N_CLUSTERS,
                })
                return []

            # ─── 2. Normalize ───
            normalized = self._normalize_windows(windows)

            # ─── 3. Cluster ───
            from sklearn.cluster import KMeans

            n_clusters = min(N_CLUSTERS, len(normalized) // 2)
            kmeans = KMeans(
                n_clusters=n_clusters,
                n_init=3,       # reduce iterations (RAM/time)
                max_iter=100,
                random_state=42,
            )
            labels = kmeans.fit_predict(normalized)
            self._kmeans = kmeans

            # ─── 4. Analyze clusters ───
            patterns = []
            for cluster_id in range(n_clusters):
                mask = labels == cluster_id
                cluster_size = int(np.sum(mask))

                if cluster_size < MIN_CLUSTER_SIZE:
                    continue

                centroid = kmeans.cluster_centers_[cluster_id]

                # Auto-name cluster
                close_profile = centroid[3::OHLC_FEATURES]  # extract close values
                label = self._auto_name(close_profile, cluster_id)

                # Calculate win_rate from trade outcomes
                win_rate = 0.5
                avg_r = 0.0
                if trade_outcomes:
                    cluster_indices = np.where(mask)[0]
                    wins, total_r, count = 0, 0.0, 0
                    for idx in cluster_indices:
                        # Map window index → bar index in original data
                        bar_idx = idx + WINDOW_SIZE
                        outcome = self._find_outcome(bar_idx, trade_outcomes)
                        if outcome:
                            count += 1
                            if outcome["profit"] > 0:
                                wins += 1
                            total_r += outcome.get("rr", 0)
                    if count > 0:
                        win_rate = wins / count
                        avg_r = total_r / count

                pattern = DiscoveredPattern(
                    pattern_id=f"CLUSTER_{cluster_id:03d}",
                    label=label,
                    centroid=centroid.tolist(),
                    total_examples=cluster_size,
                    win_rate=win_rate,
                    avg_r=avg_r,
                )
                patterns.append(pattern)

            # ─── 5. Sort by relevance (win_rate × examples) ───
            patterns.sort(key=lambda p: p.win_rate * p.total_examples, reverse=True)

            self._patterns = patterns
            self._last_discovery_time = time.monotonic()

            # ─── 6. Persist to brain.db ───
            if self.memory:
                for p in patterns:
                    try:
                        self.memory.save_discovered_pattern(p)
                    except Exception:
                        pass

            logger.info("patterns_discovered", extra={
                "total_windows": len(normalized),
                "clusters": n_clusters,
                "valid_patterns": len(patterns),
                "top_pattern": patterns[0].label if patterns else "none",
                "top_win_rate": round(patterns[0].win_rate, 3) if patterns else 0,
            })

            return patterns

        except Exception as e:
            logger.error("pattern_discovery_error", extra={"error": str(e)}, exc_info=True)
            return []

    # ────────────────────────────────────────────────────────────────
    # match() — จับคู่ candles กับ discovered patterns
    # ────────────────────────────────────────────────────────────────

    def match(self, candles: pd.DataFrame) -> Optional[DiscoveredPattern]:
        """
        Match current candles กับ discovered patterns.

        Returns:
            DiscoveredPattern ที่ใกล้เคียงที่สุด, หรือ None
        """
        if self._kmeans is None or not self._patterns:
            return None

        try:
            windows = self._extract_windows(candles)
            if not windows:
                return None

            # ใช้ window สุดท้าย
            last_window = self._normalize_windows([windows[-1]])
            cluster_id = int(self._kmeans.predict(last_window)[0])

            # หา pattern ที่ match
            for p in self._patterns:
                if p.pattern_id == f"CLUSTER_{cluster_id:03d}":
                    return p

        except Exception:
            pass

        return None

    # ────────────────────────────────────────────────────────────────
    # Internal helpers
    # ────────────────────────────────────────────────────────────────

    def _extract_windows(self, candles: pd.DataFrame) -> list[np.ndarray]:
        """ตัด candles เป็น sliding windows."""
        windows = []
        data = candles[["open", "high", "low", "close"]].values

        step = max(1, len(data) // BATCH_SIZE)  # limit total windows
        for i in range(WINDOW_SIZE, len(data), step):
            window = data[i - WINDOW_SIZE:i].flatten()  # shape: (80,)
            windows.append(window)

            if len(windows) >= BATCH_SIZE:
                break

        return windows

    def _normalize_windows(self, windows: list[np.ndarray]) -> np.ndarray:
        """Normalize windows to 0-1 range (relative to window min/max)."""
        normalized = []
        for w in windows:
            w_min = w.min()
            w_max = w.max()
            w_range = w_max - w_min
            if w_range > 0:
                normalized.append((w - w_min) / w_range)
            else:
                normalized.append(np.zeros_like(w))
        return np.array(normalized)

    def _auto_name(self, close_profile: np.ndarray, cluster_id: int) -> str:
        """Auto-name cluster ตาม shape ของ close prices."""
        try:
            for name, rule_fn in PATTERN_NAME_RULES.items():
                if rule_fn(close_profile):
                    return f"{name}_{cluster_id}"
        except Exception:
            pass
        return f"PATTERN_{cluster_id}"

    def _find_outcome(
        self, bar_idx: int, outcomes: list[dict], tolerance: int = 3,
    ) -> Optional[dict]:
        """Find trade outcome ที่ bar_idx ± tolerance."""
        for o in outcomes:
            if abs(o.get("bar_index", -999) - bar_idx) <= tolerance:
                return o
        return None

    def get_summary(self) -> list[dict]:
        """สรุป discovered patterns (สำหรับ API)."""
        return [p.to_dict() for p in self._patterns]
