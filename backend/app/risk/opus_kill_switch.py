"""
OPUS Ghost Protocol — Kill Switch (Hard Disable Conditions).

เงื่อนไขหยุดเทรดแบบเด็ดขาด (Non-Negotiable):
    1. Consecutive Losses — 3 ครั้งติดกัน → หยุด session
    2. Daily Drawdown     — DD >= -6% → หยุดทั้งวัน
    3. Extreme ATR         — ATR > 2.2x baseline → บล็อก entry ใหม่
    4. Spread Explosion    — Spread > threshold → บล็อก
    5. Daily Target Hit    — กำไรถึงเป้า → หยุดทั้งวัน
    6. Low Liquidity       — ช่วง pre-Asia (20:00-22:00 UTC) → บล็อก XAU/XAG
    7. News Spike Active   — ATR spike >2x + wick >70% → บล็อก 15 นาที
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Configuration Defaults
# ═══════════════════════════════════════════════════════════════════
DEFAULT_MAX_CONSECUTIVE_LOSSES = 3
DEFAULT_MAX_DAILY_DD_PCT = 0.06      # -6%
DEFAULT_MAX_DAILY_DD_USD = 14.0      # -14 USD default
DEFAULT_ATR_EXTREME_MULT = 2.2
DEFAULT_SPREAD_MAX_POINTS = 50       # Max spread in points
DEFAULT_DAILY_TARGET_USD = 20.0      # USD target per day
DEFAULT_NEWS_BLOCK_MINUTES = 15
DEFAULT_LOW_LIQ_START_UTC = 20       # 20:00 UTC
DEFAULT_LOW_LIQ_END_UTC = 22         # 22:00 UTC


@dataclass
class KillSwitchResult:
    """Output of kill switch evaluation."""
    blocked: bool = False
    reason: str = ""
    block_type: str = "NONE"  # LOSS_STREAK | DAILY_DD | ATR_EXTREME | SPREAD | TARGET_HIT | LOW_LIQ | NEWS_SPIKE
    resume_after: Optional[datetime] = None
    details: dict = field(default_factory=dict)


class OpusKillSwitch:
    """
    OPUS Ghost Protocol Kill Switch — Hard Safety Controller.

    Usage:
        ks = OpusKillSwitch(config)
        result = ks.evaluate(state)
        if result.blocked:
            # HALT — do not trade
    """

    def __init__(
        self,
        max_consecutive_losses: int = DEFAULT_MAX_CONSECUTIVE_LOSSES,
        max_daily_dd_pct: float = DEFAULT_MAX_DAILY_DD_PCT,
        max_daily_dd_usd: float = DEFAULT_MAX_DAILY_DD_USD,
        atr_extreme_mult: float = DEFAULT_ATR_EXTREME_MULT,
        spread_max_points: float = DEFAULT_SPREAD_MAX_POINTS,
        daily_target_usd: float = DEFAULT_DAILY_TARGET_USD,
        news_block_minutes: int = DEFAULT_NEWS_BLOCK_MINUTES,
        low_liq_start_utc: int = DEFAULT_LOW_LIQ_START_UTC,
        low_liq_end_utc: int = DEFAULT_LOW_LIQ_END_UTC,
    ):
        self.max_consecutive_losses = max_consecutive_losses
        self.max_daily_dd_pct = max_daily_dd_pct
        self.max_daily_dd_usd = max_daily_dd_usd
        self.atr_extreme_mult = atr_extreme_mult
        self.spread_max_points = spread_max_points
        self.daily_target_usd = daily_target_usd
        self.news_block_minutes = news_block_minutes
        self.low_liq_start_utc = low_liq_start_utc
        self.low_liq_end_utc = low_liq_end_utc

        # Tracking state
        self._news_spike_until: Optional[datetime] = None

    def evaluate(
        self,
        consecutive_losses: int = 0,
        daily_pl_usd: float = 0.0,
        daily_pl_pct: float = 0.0,
        initial_equity: float = 0.0,
        current_equity: float = 0.0,
        atr_current: float = 0.0,
        atr_baseline: float = 0.0,
        spread_points: float = 0.0,
        symbol: str = "",
        now: Optional[datetime] = None,
        has_news_spike: bool = False,
    ) -> KillSwitchResult:
        """
        Evaluate all kill switch conditions.

        Args:
            consecutive_losses: Number of consecutive losing trades today
            daily_pl_usd: Today's realized + unrealized P/L in USD
            daily_pl_pct: Today's P/L as percentage of initial equity
            initial_equity: Start-of-day equity
            current_equity: Current equity
            atr_current: Current ATR value
            atr_baseline: Baseline ATR (50-bar average)
            spread_points: Current spread in points
            symbol: Trading symbol
            now: Current UTC time
            has_news_spike: Whether a news spike was detected in recent bars

        Returns:
            KillSwitchResult
        """
        if now is None:
            now = datetime.now(timezone.utc)

        # ─── Check 1: Consecutive Losses ───
        if consecutive_losses >= self.max_consecutive_losses:
            logger.warning("opus_kill_switch_triggered", extra={
                "type": "LOSS_STREAK",
                "consecutive_losses": consecutive_losses,
                "symbol": symbol,
            })
            return KillSwitchResult(
                blocked=True,
                reason=f"OPUS Kill: {consecutive_losses} consecutive losses (max={self.max_consecutive_losses})",
                block_type="LOSS_STREAK",
                details={"consecutive_losses": consecutive_losses},
            )

        # ─── Check 2: Daily Drawdown (Percent + USD Limit) ───
        if daily_pl_pct <= -self.max_daily_dd_pct or daily_pl_usd <= -self.max_daily_dd_usd:
            logger.warning("opus_kill_switch_triggered", extra={
                "type": "DAILY_DD",
                "daily_pl_pct": daily_pl_pct,
                "daily_pl_usd": daily_pl_usd,
                "max_dd_pct": self.max_daily_dd_pct,
                "max_dd_usd": self.max_daily_dd_usd,
                "symbol": symbol,
            })
            return KillSwitchResult(
                blocked=True,
                reason=f"OPUS Kill: Daily Loss of ${daily_pl_usd:.2f} (Pct: {daily_pl_pct:.2%}) exceeded limits (-${self.max_daily_dd_usd:.2f} or -{self.max_daily_dd_pct:.0%})",
                block_type="DAILY_DD",
                resume_after=self._next_day_start(now),
                details={
                    "daily_pl_pct": round(daily_pl_pct, 4),
                    "daily_pl_usd": round(daily_pl_usd, 2),
                },
            )

        # ─── Check 3: Extreme ATR ───
        if atr_baseline > 0 and atr_current > 0:
            atr_ratio = atr_current / atr_baseline
            if atr_ratio > self.atr_extreme_mult:
                logger.warning("opus_kill_switch_triggered", extra={
                    "type": "ATR_EXTREME",
                    "atr_ratio": round(atr_ratio, 2),
                    "threshold": self.atr_extreme_mult,
                    "symbol": symbol,
                })
                return KillSwitchResult(
                    blocked=True,
                    reason=f"OPUS Kill: ATR {atr_ratio:.1f}x baseline (max={self.atr_extreme_mult}x)",
                    block_type="ATR_EXTREME",
                    details={
                        "atr_current": round(atr_current, 5),
                        "atr_baseline": round(atr_baseline, 5),
                        "atr_ratio": round(atr_ratio, 2),
                    },
                )

        # ─── Check 4: Spread Explosion ───
        if spread_points > self.spread_max_points:
            logger.warning("opus_kill_switch_triggered", extra={
                "type": "SPREAD",
                "spread": spread_points,
                "max": self.spread_max_points,
                "symbol": symbol,
            })
            return KillSwitchResult(
                blocked=True,
                reason=f"OPUS Kill: Spread {spread_points:.1f} > max {self.spread_max_points:.1f}",
                block_type="SPREAD",
                details={"spread_points": spread_points},
            )

        # ─── Check 5: Daily Target Hit ───
        if self.daily_target_usd > 0 and daily_pl_usd >= self.daily_target_usd:
            logger.info("opus_kill_switch_triggered", extra={
                "type": "TARGET_HIT",
                "daily_pl_usd": round(daily_pl_usd, 2),
                "target": self.daily_target_usd,
                "symbol": symbol,
            })
            return KillSwitchResult(
                blocked=True,
                reason=f"OPUS: Daily target ${daily_pl_usd:.2f} >= ${self.daily_target_usd:.2f} — STOP",
                block_type="TARGET_HIT",
                resume_after=self._next_day_start(now),
                details={
                    "daily_pl_usd": round(daily_pl_usd, 2),
                    "target_usd": self.daily_target_usd,
                },
            )

        # ─── Check 6: Low Liquidity Session ───
        current_hour = now.hour
        is_low_liq = self.low_liq_start_utc <= current_hour < self.low_liq_end_utc
        is_metals = any(m in symbol.upper() for m in ["XAU", "XAG", "GOLD", "SILVER"])
        if is_low_liq and is_metals:
            resume = now.replace(hour=self.low_liq_end_utc, minute=0, second=0, microsecond=0)
            logger.info("opus_kill_switch_triggered", extra={
                "type": "LOW_LIQ",
                "hour_utc": current_hour,
                "symbol": symbol,
            })
            return KillSwitchResult(
                blocked=True,
                reason=f"OPUS Kill: Low liquidity ({current_hour}:00 UTC) for {symbol}",
                block_type="LOW_LIQ",
                resume_after=resume,
                details={"hour_utc": current_hour},
            )

        # ─── Check 7: News Spike Active ───
        if has_news_spike:
            self._news_spike_until = now + timedelta(minutes=self.news_block_minutes)

        if self._news_spike_until and now < self._news_spike_until:
            logger.info("opus_kill_switch_triggered", extra={
                "type": "NEWS_SPIKE",
                "resume_at": self._news_spike_until.isoformat(),
                "symbol": symbol,
            })
            return KillSwitchResult(
                blocked=True,
                reason=f"OPUS Kill: News spike active, resume at {self._news_spike_until.strftime('%H:%M')} UTC",
                block_type="NEWS_SPIKE",
                resume_after=self._news_spike_until,
            )

        # All clear
        return KillSwitchResult(blocked=False)

    def _next_day_start(self, now: datetime) -> datetime:
        """Get start of next trading day (00:00 UTC)."""
        return (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    def reset_news_spike(self):
        """Clear news spike block."""
        self._news_spike_until = None
