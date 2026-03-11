# -*- coding: utf-8 -*-
"""
OPUS Institutional Risk Governor — Capital Preservation Engine
==============================================================
Institutional-grade risk management layer that sits ABOVE the Risk Gate.
Computes account-wide risk status, defensive mode, lot multiplier,
and daily discipline control.

Primary Objective: Capital Preservation > Consistency > Profit
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from backend.trader.config.paths import SETTINGS_PATH

logger = logging.getLogger("opus_logger")

with open(SETTINGS_PATH, encoding="utf-8") as _f:
    _CFG = json.load(_f)
    _OPUS_CFG = _CFG.get("opus_governor", {})
    _RISK_CFG = _CFG.get("risk_limits", {})


# ═══════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════

@dataclass
class OPUSStatus:
    """Structured status output — printed every cycle."""
    # 1. Account Risk Status
    risk_level: str = "NORMAL"  # NORMAL / CAUTION / DEFENSIVE / CRITICAL
    equity: float = 0.0
    balance: float = 0.0
    margin_level: float = 999.0
    floating_dd_pct: float = 0.0

    # 2. Trade Allowed
    trade_allowed: bool = True
    block_reason: str = ""

    # 3. Current Regime (set externally)
    regime: str = "UNKNOWN"

    # 4. Recommended Symbol (set externally)
    recommended_symbol: str = ""

    # 5. Lot Multiplier
    lot_multiplier: float = 1.0

    # 6. Defensive Mode
    defensive_mode: bool = False
    defensive_reasons: list = field(default_factory=list)

    # 7. Daily Discipline
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    discipline_ok: bool = True
    discipline_reason: str = ""

    # 8. Next Valid Setup
    next_setup: str = "Waiting for signal..."

    def log_output(self):
        """Print structured OPUS Status to logger."""
        logger.info("═══ OPUS STATUS ═══════════════════════════════")
        logger.info(f"  1. Risk: {self.risk_level} | Eq=${self.equity:.2f} | Bal=${self.balance:.2f} | Margin={self.margin_level:.0f}% | FloatDD={self.floating_dd_pct:.2f}%")
        logger.info(f"  2. Trade Allowed: {'YES' if self.trade_allowed else 'NO'} {self.block_reason}")
        logger.info(f"  3. Regime: {self.regime}")
        
        # Precious Metals Guard Info
        pm_status = "LOCKED (Eq < $200)" if self.equity < 200 else "READY"
        logger.info(f"  4. Alpha Units: USOIL, BTC | XAU/XAG: {pm_status}")
        
        logger.info(f"  5. Lot Multiplier: {self.lot_multiplier:.2f}x")
        logger.info(f"  6. Defensive: {'ON' if self.defensive_mode else 'OFF'} {self.defensive_reasons}")
        logger.info(f"  7. Discipline: {'OK' if self.discipline_ok else 'NO'} Losses={self.consecutive_losses}/3 PnL=${self.daily_pnl:.2f} {self.discipline_reason}")
        logger.info(f"  8. Next: {self.next_setup}")
        logger.info("═══════════════════════════════════════════════")


# ═══════════════════════════════════════════════════════════
# GOVERNOR
# ═══════════════════════════════════════════════════════════

class OPUSGovernor:
    """
    Institutional Risk Governor.
    Reads LIVE MT5 account state and computes risk status,
    defensive mode, lot multiplier, and discipline control.
    """

    def __init__(self):
        self.margin_reduce_threshold = _OPUS_CFG.get("margin_reduce_threshold", 250)
        self.margin_block_threshold = _OPUS_CFG.get("margin_block_threshold", 180)
        self.defensive_dd_threshold = _OPUS_CFG.get("defensive_dd_threshold", 4.0)
        self.defensive_lot_reduction = _OPUS_CFG.get("defensive_lot_reduction", 0.5)
        self.risk_per_trade = _OPUS_CFG.get("risk_per_trade_pct", 1.2)
        self.defensive_risk = _OPUS_CFG.get("defensive_risk_pct", 0.6)
        self.daily_loss_limit_pct = _OPUS_CFG.get("daily_loss_limit_pct", 5.0)
        
        # ─── THB PROFIT TARGETS ──────────────────────────
        # ─── THB PROFIT TARGETS (SYNCED) ──────────────────
        # ─── THB PROFIT TARGETS (SYNCED WITH SETTINGS) ──────────────────
        from backend.app.core.config import get_settings
        settings = get_settings()
        
        self.USD_TO_THB = _RISK_CFG.get("usd_to_thb_rate", 35.0)
        self.daily_target_usd = _RISK_CFG.get("daily_target_currency", 60.0)
        self.vault_threshold_usd = _RISK_CFG.get("vault_target_currency", 18.0)
        
        self.max_consecutive_losses = _RISK_CFG.get("max_consecutive_losses", 5)
        self.rr_minimum = _OPUS_CFG.get("rr_minimum", 1.2)
        self.cooldown_hours = _OPUS_CFG.get("consecutive_loss_cooldown_hours", 2)
        self._loss_cooldown_until: Optional[datetime] = None
        self.daily_target_thb = self.daily_target_usd * self.USD_TO_THB
        
        # ─── DRAWDOWN SCALING (V1.2) ───────────────────
        # Reduce risk linearly as we approach daily limit
        self.dd_scaling_start_pct = 2.0  # Start reducing at 2% daily loss
        self.dd_scaling_limit_pct = 4.0  # Stop trading at 4% daily loss (Governor hard limit)
        self.min_equity_threshold = _RISK_CFG.get("min_equity_threshold", 50.0)

    def compute_status(self, account_state: dict, market_state: dict = None) -> OPUSStatus:
        """
        Master status computation. Call once per cycle BEFORE any trading logic.
        Returns OPUSStatus with all 8 fields populated.
        """
        status = OPUSStatus()

        # ─── Extract account state ───
        equity = account_state.get("equity", 0)
        balance = account_state.get("balance", 0)
        margin_level = account_state.get("margin_level", 999)
        daily_pnl = account_state.get("daily_pnl", 0)
        consecutive_losses = account_state.get("consecutive_losses", 0)
        
        # If daily_pnl was overridden to session_pnl in main.py, 
        # we treat consecutive losses as 0 for this session start.
        if "session_reset" in account_state:
            consecutive_losses = 0

        # Floating drawdown = (balance - equity) / balance * 100
        if balance > 0:
            floating_dd_pct = max(0, (balance - equity) / balance * 100)
        else:
            floating_dd_pct = 0

        status.equity = equity
        status.balance = balance
        status.margin_level = margin_level
        status.floating_dd_pct = floating_dd_pct
        status.daily_pnl = daily_pnl
        status.consecutive_losses = consecutive_losses

        # ─── 1. Risk Level ───
        status.risk_level = self._classify_risk_level(margin_level, floating_dd_pct, daily_pnl, equity)

        # ─── 2. Trade Allowed & Vault Mode ───
        allowed, reason = self._is_trade_allowed(account_state, margin_level, floating_dd_pct)
        status.trade_allowed = allowed
        status.block_reason = reason

        # ─── 5. Lot Multiplier (Incorporates Vault Mode) ───
        status.lot_multiplier = self._compute_lot_multiplier(margin_level, floating_dd_pct, daily_pnl)

        # ─── 6. Defensive Mode ───
        status.defensive_mode, status.defensive_reasons = self._check_defensive_mode(
            floating_dd_pct, margin_level
        )
        # Vault Mode is a type of defensive mode for capital locking
        # DISABLED in Money Hunter mode (DD > 3%)
        if daily_pnl >= self.vault_threshold_usd and floating_dd_pct <= 3.0:
            status.defensive_mode = True
            status.defensive_reasons.append(f"VAULT MODE ACTIVE (PnL ${daily_pnl:.2f} >= ${self.vault_threshold_usd:.2f})")

        # ─── 7. Daily Discipline ───
        status.discipline_ok, status.discipline_reason = self._check_discipline(
            daily_pnl, equity, consecutive_losses
        )
        if not status.discipline_ok:
            status.trade_allowed = False
            status.block_reason = status.discipline_reason

        # 💎 Priority Symbol Logic (Recovery Focus)
        status.recommended_symbol = "XAUUSD, BTCUSD (High Stability Focus)"
        if floating_dd_pct > 3.0:
            status.recommended_symbol = "🔥 MONEY HUNTER (Recovery): Hunting high-prob XAU/BTC only"
        
        if equity < 200:
            status.recommended_symbol += " | XAU/XAG LOCKED (Safety Threshold)"

        return status

    def _classify_risk_level(self, margin_level: float, floating_dd_pct: float,
                              daily_pnl: float, equity: float) -> str:
        """Classify account risk level."""
        if margin_level < self.margin_block_threshold:
            return "CRITICAL"
        
        # Vault Mode status check (Only if not in Money Hunter recovery)
        if daily_pnl >= self.vault_threshold_usd and floating_dd_pct <= 3.0:
            return "VAULT_LOCK"
            
        if floating_dd_pct > self.defensive_dd_threshold or margin_level < self.margin_reduce_threshold:
            return "DEFENSIVE"
        if floating_dd_pct > 2.0 or margin_level < 500:
            return "CAUTION"
        return "NORMAL"

    def _is_trade_allowed(self, account_state: dict, margin_level: float, floating_dd_pct: float) -> tuple:
        """Check if trading is allowed based on critical OPUS constraints."""
        # 0. Minimum Equity Guard (Hard Stop)
        if account_state.get("equity", 0) < self.min_equity_threshold:
            return False, f"EQUITY CRITICAL: ${account_state.get('equity', 0):.2f} < ${self.min_equity_threshold:.2f}"

        # Margin level critical
        if margin_level < self.margin_block_threshold and margin_level > 0:
            return False, f"MARGIN CRITICAL: {margin_level:.0f}% < {self.margin_block_threshold}%"

        # 🛡️ [HARD STOP] Floating DD Critical (Block new trades)
        if floating_dd_pct >= 10.0:
            return False, f"🛡️ [HARD STOP] FLOATING DD CRITICAL: {floating_dd_pct:.2f}% (Safety Halt)"

        # 🛡️ [CAPITAL GUARD] Minimum Equity Protection
        if account_state.get("equity", 0) < 100.0:
             return False, "❌ [STOP] CAPITAL PROTECTION: Equity < $100. Manual intervention required to protect base capital."

        # Daily target reached (2,000 THB Hard Stop)
        daily_pnl = account_state.get("daily_pnl", 0)
        if daily_pnl >= self.daily_target_usd:
            target_thb = getattr(self, 'daily_target_thb', self.daily_target_usd * self.USD_TO_THB)
            return False, f"🏆 [STOP] DAILY TARGET HIT: ${daily_pnl:.2f} (>{target_thb:.0f} THB)"

        # Max consecutive losses (Disciplined Cooldown handled in _check_discipline)
        return True, ""

    def _compute_lot_multiplier(self, margin_level: float, floating_dd_pct: float, daily_pnl: float = 0.0, equity: float = 0.0) -> float:
        """
        Dynamic lot multiplier based on account stress and profit locking.
        1.0 = normal, <1.0 = reduced risk.
        """
        multiplier = 1.0

        # 1. 🏦 VAULT MODE 2.0 (Institutional Profit Locking)
        # Goal: Secure 600 THB ($18) for daily withdrawal.
        # SKIP if in Money Hunter mode (DD > 3%)
        if daily_pnl >= self.vault_threshold_usd and floating_dd_pct <= 3.0:
            # Check if we also hit the hard cap
            if daily_pnl >= self.daily_target_usd:
                logger.info(f"🏆 [GOAL] DAILY TARGET REACHED (${daily_pnl:.2f})")
                logger.info(f"🏆 [เป้าหมาย] พอร์ตทำกำไรครบเป้าหมายรายวันแล้ว (${daily_pnl * self.USD_TO_THB:.0f} บาท)! หยุดการเทรดเพื่อรักษาเงินต้น")
                return 0.0
            
            # Withdrawal Readiness Alert
            pnl_thb = daily_pnl * self.USD_TO_THB
            logger.info(f"🏦 [VAULT] WITHDRAWAL GOAL REACHED: {pnl_thb:.0f} THB (${daily_pnl:.2f})")
            logger.info(f"🏦 [ถอนเงิน] กำไรบรรลุเป้าหมายการถอนรายวัน ({pnl_thb:.0f} บาท) ระบบล็อคกำไรโดยลด Lot เหลือ 10%")
            multiplier = 0.1  # Reduce to 10% (0.01-0.02 lots max on small accounts)
            return multiplier 

        # 2. Drawdown Scaling (Linear Reduction)
        if daily_pnl < 0 and equity > 0:
            loss_pct = abs(daily_pnl) / equity * 100
            if loss_pct >= self.dd_scaling_start_pct:
                scale = 1.0 - ((loss_pct - self.dd_scaling_start_pct) / (self.dd_scaling_limit_pct - self.dd_scaling_start_pct))
                multiplier *= max(0.2, scale)
                logger.warning(f"📉 [DD-SCALE] Daily Loss {loss_pct:.2f}% -> Multiplier x{multiplier:.2f}")

        # 3. Margin Stress → reduce 50%
        if 0 < margin_level < self.margin_reduce_threshold:
            multiplier *= 0.5
            logger.warning(f"⚠️ [GOV] Margin ต่ำ ({margin_level:.0f}%) -> ลด Lot x0.5")

        # 4. Floating DD stress → reduce to defensive level
        if floating_dd_pct > self.defensive_dd_threshold:
            multiplier *= self.defensive_lot_reduction
            logger.warning(f"⚠️ [GOV] Floating DD สูง ({floating_dd_pct:.1f}%) -> ลด Lot x{self.defensive_lot_reduction}")

        return round(max(0.1, multiplier), 2)

    def _check_defensive_mode(self, floating_dd_pct: float, margin_level: float) -> tuple:
        """
        Defensive mode = reduced risk profile.
        When active: no counter-trend, no breakout, lot reduced.
        """
        reasons = []
        active = False

        if floating_dd_pct > self.defensive_dd_threshold:
            active = True
            reasons.append(f"Floating DD {floating_dd_pct:.1f}% > {self.defensive_dd_threshold}%")

        if 0 < margin_level < self.margin_reduce_threshold:
            active = True
            reasons.append(f"Margin {margin_level:.0f}% < {self.margin_reduce_threshold}%")

        return active, reasons

    def _check_discipline(self, daily_pnl: float, equity: float,
                           consecutive_losses: int) -> tuple:
        """
        Daily discipline control with 4h cooldown timer.
        Returns (ok, reason).
        """
        now = datetime.now(timezone.utc)

        # Check active cooldown timer
        if self._loss_cooldown_until and now < self._loss_cooldown_until:
            remaining = int((self._loss_cooldown_until - now).total_seconds() // 60)
            return False, f"⏳ LOSS COOLDOWN: {remaining}m remaining ({self.cooldown_hours}h timer)"

        # 3 consecutive losses → activate 4h cooldown
        if consecutive_losses >= self.max_consecutive_losses:
            self._loss_cooldown_until = now + timedelta(hours=self.cooldown_hours)
            logger.warning(
                f"🛑 [DISCIPLINE] {consecutive_losses} losses → {self.cooldown_hours}h cooldown until "
                f"{self._loss_cooldown_until.strftime('%H:%M UTC')}"
            )
            return False, f"🛡️ [DISCIPLINE] {consecutive_losses} losses → {self.cooldown_hours}h cooldown"

        # Daily PnL targets already handled in _is_trade_allowed

        # Daily loss limit
        # If reset-pnl is active, we check session loss instead of absolute daily loss
        if daily_pnl < 0 and equity > 0:
            loss_pct = abs(daily_pnl) / equity * 100
            if loss_pct >= self.daily_loss_limit_pct:
                return False, f"❌ [LOSS] LIMIT REACHED: {loss_pct:.1f}% of equity"

        return True, "OK"


    def get_risk_pct(self, defensive: bool = False) -> float:
        """Return risk per trade based on mode."""
        return self.defensive_risk if defensive else self.risk_per_trade

    def validate_rr(self, entry: float, sl: float, tp: float) -> bool:
        """Validate minimum Risk:Reward ratio."""
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        if risk <= 0:
            return False
        rr = reward / risk
        return rr >= self.rr_minimum


# ═══════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════
governor = OPUSGovernor()
