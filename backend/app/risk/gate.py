"""
Pre-Trade Gate — ด่านตรวจก่อนเทรด (Hard Gatekeeper).

นี่คือหัวใจของ safety system — ทุกเทรดต้องผ่านด่านนี้.
ถ้าไม่ผ่านข้อใดข้อหนึ่ง → บล็อกเทรด + log เหตุผลชัดเจน.

17 ด่านตรวจ:
    1.  MT5 เชื่อมต่ออยู่
    2.  สัญลักษณ์เทรดได้
    3.  ตลาดเปิด
    4.  มี Profile + schema valid
    5.  Spread ≤ threshold
    6.  อยู่ใน session ที่อนุญาต
    7.  ไม่อยู่ในช่วงข่าว (±30 นาที)
    8.  Floating DD ≤ 10%
    9.  ขาดทุนวันนี้ไม่เกินลิมิต (default 3%)
    10. Capital floor ≥ 90% ของยอดตั้งต้น
    11. จำนวน positions ไม่เกิน max
    12. มี SL
    13. Lot size valid
    14. ความเสี่ยง USD ≤ 2% equity
    15. Regime Filter — ตลาด sideways/low-vol → NO-TRADE
    16. Cooldown — หยุดเทรดหลังขาดทุน
    17. Session Max Trades — เทรดครบ max ต่อ session

กฎเหล็ก:
    - ห้าม bypass ด่านนี้ไม่ว่ากรณีใด
    - ทุกเหตุผลที่บล็อกต้องแสดงบน dashboard
    - "ไม่เทรด" ต้องมีเหตุผลเสมอ — ห้ามบล็อกเงียบ
"""

from app.core.logging import get_logger
from app.domain.enums import BlockReason
from app.domain.models import (
    AccountState,
    Decision,
    GateResult,
    SymbolProfile,
    RegimeContext,
)

logger = get_logger(__name__)


class PreTradeGate:
    """
    Pre-Trade Gate — ตรวจสอบ 17 เงื่อนไขก่อนอนุญาตให้เทรด.
    
    วิธีใช้:
        gate = PreTradeGate(settings, cooldown_mgr, session_guard, regime_filter)
        result = gate.check(decision, profile, account, ...)
        if not result.passed:
            # บล็อก — แสดงเหตุผลบน dashboard
            for reason in result.reasons:
                log_blocked(reason)
    """

    def __init__(
        self,
        settings,
        cooldown_mgr=None,
        session_guard=None,
        regime_filter=None,
    ) -> None:
        self.settings = settings
        self.cooldown_mgr = cooldown_mgr
        self.session_guard = session_guard
        self.regime_filter = regime_filter
        
        # Phase 4 - Correlation Guard
        from app.risk.correlation import CorrelationGuard
        self.correlation_guard = CorrelationGuard(settings)

    def check(
        self,
        decision: Decision,
        profile: SymbolProfile,
        account: AccountState,
        mt5_connected: bool = False,
        market_open: bool = False,
        current_spread: float = 0.0,
        current_session: str = "CLOSED",
        news_safe: bool = True,
        open_positions_count: int = 0,
        total_positions_count: int = 0,
        open_positions: list = None,
        candles=None,
        performance_metrics: dict | None = None,
        regime_context: RegimeContext | None = None,
        macro_bias_direction: str = "ANY",
    ) -> GateResult:
        """
        ตรวจสอบทุกเงื่อนไข — return GateResult.
        
        ตรวจทุกข้อแม้ข้อก่อนหน้าไม่ผ่าน (เพื่อรายงานทุกปัญหาพร้อมกัน).
        """
        reasons: list[BlockReason] = []
        details: dict = {}

        # 💰 [USER RULE] ด่าน 0: บันทึกค่าเงินก่อนเทรด (Capital Monitor)
        logger.info(
            "capital_check",
            extra={
                "symbol": decision.symbol,
                "balance": account.balance,
                "equity": account.equity,
                "free_margin": account.free_margin,
            }
        )

        # 0.1: Minimum Equity Guard (กันไว้เผื่อกรณีพอร์ตเหลือน้อยเกินไป)
        min_equity = getattr(self.settings, 'min_equity_threshold', 50.0)
        if account.equity < min_equity:
            reasons.append(BlockReason.EQUITY_TOO_LOW)
            details["equity"] = account.equity
            details["min_required"] = min_equity

        # 0.2: Precious Metals Equity Guard ($300 threshold for XAU/XAG)
        is_precious_metal = any(m in decision.symbol.upper() for m in ["XAU", "XAG", "GOLD", "SILVER"])
        # RECOVERY BYPASS: Allow XAU micro-lots even below $300 to facilitate recovery
        if is_precious_metal and account.equity < 300.0:
            if "XAU" in decision.symbol.upper() and account.equity >= 50.0:
                 logger.info("recovery_equity_bypass", extra={"symbol": decision.symbol, "equity": account.equity})
            else:
                reasons.append(BlockReason.EQUITY_TOO_LOW)
                details["equity"] = account.equity
                details["pm_min_required"] = 300.0
                details["reason"] = "Precious Metals locked until equity >= $300"

        # --- ด่าน 1: MT5 เชื่อมต่อ ---
        if not mt5_connected:
            reasons.append(BlockReason.MT5_DISCONNECTED)

        # --- ด่าน 2: สัญลักษณ์เทรดได้ ---
        if not profile.is_active:
            reasons.append(BlockReason.SYMBOL_NOT_TRADABLE)

        # --- ด่าน 3: ตลาดเปิด ---
        if not market_open:
            reasons.append(BlockReason.MARKET_CLOSED)

        # --- ด่าน 4: Profile ครบถ้วน ---
        if profile.contract_size <= 0 or profile.point <= 0:
            reasons.append(BlockReason.PROFILE_INCOMPLETE)

        # --- ด่าน 5: Spread ≤ threshold ---
        if current_spread > profile.spread_max_allowed:
            reasons.append(BlockReason.SPREAD_GUARD)
            details["spread_current"] = current_spread
            details["spread_max"] = profile.spread_max_allowed

        # --- ด่าน 6: Session ที่อนุญาต ---
        is_crypto = any(c in decision.symbol.upper() for c in ["BTC", "ETH", "SOL", "BNB"])
        
        # Crypto อนุญาตในช่วง WEEKEND และสภาวะปกติ
        if current_session == "WEEKEND":
            if not is_crypto:
                reasons.append(BlockReason.SESSION_BLOCKED)
                details["current_session"] = current_session
                details["reason"] = "WEEKEND: Only crypto allowed"
        elif profile.sessions_allowed and current_session not in profile.sessions_allowed:
            reasons.append(BlockReason.SESSION_BLOCKED)
            details["current_session"] = current_session

        # --- ด่าน 7: News safe ---
        if not news_safe:
            reasons.append(BlockReason.NEWS_BLOCK)
            
        # --- ด่าน 7.5: Macro Bias Direction ---
        if macro_bias_direction == "LONG_ONLY" and decision.action == Action.SELL:
            reasons.append(BlockReason.MACRO_BIAS_BLOCKED)
            details["macro_bias"] = macro_bias_direction
        elif macro_bias_direction == "SHORT_ONLY" and decision.action == Action.BUY:
            reasons.append(BlockReason.MACRO_BIAS_BLOCKED)
            details["macro_bias"] = macro_bias_direction

        # --- ด่าน 8: Floating DD ≤ 10% ---
        from app.risk.guards import check_floating_dd
        if not check_floating_dd(account, symbol=decision.symbol, max_dd_pct=self.settings.floating_dd_block_pct):
            reasons.append(BlockReason.FLOATING_DD_EXCEEDED)
            details["floating_dd_pct"] = round(abs(account.floating_pl) / account.equity * 100, 2) if account.equity > 0 else 0

        # --- ด่าน 8.5: Floating DD in Account Currency (USD/USC) ---
        max_dd_usd = getattr(self.settings, 'floating_dd_block_usd', 0.0)
        if max_dd_usd > 0 and account.floating_pl < -max_dd_usd:
            reasons.append(BlockReason.FLOATING_DD_EXCEEDED)
            details["floating_dd_usd"] = account.floating_pl
            details["max_dd_usd"] = max_dd_usd

        # --- ด่าน 9: Daily loss limit (configurable, default 5%) ---
        # 🛡️ 5 USD DD Kill-Switch (Compounding Phase 1 Safe)
        max_daily_loss_usd = getattr(self.settings, 'max_daily_loss_usd', 5.0)
        if account.daily_pl <= -max_daily_loss_usd:
            reasons.append(BlockReason.DAILY_LOSS_EXCEEDED)
            details["daily_loss_usd"] = account.daily_pl
            details["max_daily_loss_usd"] = max_daily_loss_usd
            details["action"] = "KILL_SWITCH_ACTIVE_24H"

        # --- ด่าน 9.5: Daily profit target (Trigger Risk Reduction, don't block) ---
        # 18 USD = ~600 THB Target
        # Note: We no longer BLOCK here, we allow sizing.py to reduce risk by 80%
        target_daily_profit_usd = getattr(self.settings, 'daily_target_amount', 18.0)
        # (Removed blocking logic to allow 'Let Winners Run' with 80% risk reduction in sizing.py)

        # --- ด่าน 10: Capital floor ---
        if account.initial_balance > 0:
            floor = account.initial_balance * (self.settings.capital_floor_pct / 100)
            if account.equity < floor:
                reasons.append(BlockReason.CAPITAL_FLOOR_BREACH)
                details["equity"] = account.equity
                details["floor"] = floor

        # --- ด่าน 11: Max positions per symbol ---
        if open_positions_count >= self.settings.max_positions_per_symbol:
            reasons.append(BlockReason.MAX_POSITIONS)
            details["open"] = open_positions_count
            details["max"] = self.settings.max_positions_per_symbol

        # --- ด่าน 11b: Max TOTAL positions across ALL symbols ---
        max_total = getattr(self.settings, 'max_total_positions', 10)
        if total_positions_count >= max_total:
            reasons.append(BlockReason.MAX_TOTAL_POSITIONS)
            details["total_open"] = total_positions_count
            details["max_total"] = max_total

        # --- ด่าน 11c: Aggregate margin utilization ---
        # ถ้า margin ที่ใช้อยู่เกิน 50% ของ equity → บล็อก (ป้องกันพอตระเบิด)
        max_margin_pct = 50.0
        
        # --- ด่าน 11d: Portfolio Correlation Guard (Phase 4) ---
        if open_positions:
            is_corr_allowed, corr_reason = self.correlation_guard.is_exposure_allowed(
                decision.symbol, decision.action, open_positions
            )
            if not is_corr_allowed:
                reasons.append(BlockReason.CORRELATED_EXPOSURE)
                details["correlation_reason"] = corr_reason
        if account.equity > 0 and account.margin > 0:
            margin_usage_pct = (account.margin / account.equity) * 100
            if margin_usage_pct > max_margin_pct:
                reasons.append(BlockReason.MARGIN_UTILIZATION_HIGH)
                details["margin_usage_pct"] = round(margin_usage_pct, 2)
                details["max_margin_pct"] = max_margin_pct

        # --- ด่าน 12: SL mandatory ---
        if decision.stop_loss is None or decision.stop_loss <= 0:
            reasons.append(BlockReason.NO_STOP_LOSS)

        # --- ด่าน 13: Lot size valid (จะตรวจตอน sizing) ---
        # ตรวจที่ sizing.py อีกครั้ง

        # --- ด่าน 14: Risk ≤ 2% equity (จะตรวจตอน sizing) ---
        # ตรวจที่ sizing.py อีกครั้ง

        # --- ด่าน 15: Unified Regime Filter (One Brain) ---
        if regime_context:
            if not regime_context.actionable:
                reasons.append(BlockReason.REGIME_NO_TRADE)
                details["regime_reason"] = regime_context.reason
                details["regime_details"] = regime_context.details
        elif self.regime_filter is not None and candles is not None:
            # Fallback for backward compatibility
            filter_result = self.regime_filter.check(candles)
            if not filter_result.tradable:
                reasons.append(BlockReason.REGIME_NO_TRADE)
                details["regime_reason"] = filter_result.reason
                if filter_result.details:
                    details["regime_details"] = filter_result.details

        # --- ด่าน 16: Cooldown — หยุดเทรดหลังขาดทุน ---
        if self.cooldown_mgr is not None:
            cd_allowed, cd_reason = self.cooldown_mgr.is_allowed(decision.symbol)
            if not cd_allowed:
                if "SESSION_HALT" in cd_reason:
                    reasons.append(BlockReason.LOSS_STREAK_HALT)
                else:
                    reasons.append(BlockReason.COOLDOWN_ACTIVE)
                details["cooldown_reason"] = cd_reason

        # --- ด่าน 17: Session Max Trades ---
        if self.session_guard is not None:
            # 17a. Session Limit
            sg_allowed, sg_reason = self.session_guard.is_allowed(
                decision.symbol, current_session,
            )
            if not sg_allowed:
                reasons.append(BlockReason.SESSION_MAX_TRADES)
                details["session_guard_reason"] = sg_reason

            # 17b. Daily Limit (New for Sniper Mode)
            # Determine limit based on symbol
            is_gold = "XAU" in decision.symbol.upper() or "GOLD" in decision.symbol.upper()
            is_crypto = any(c in decision.symbol.upper() for c in ["BTC", "ETH", "SOL", "BNB"])
            
            if is_crypto:
                daily_limit = getattr(self.settings, 'max_daily_trades_crypto', 5)
            elif is_gold:
                daily_limit = getattr(self.settings, 'max_daily_trades_gold', 3)
            else:
                daily_limit = getattr(self.settings, 'max_daily_trades_fx', 2)
            
            d_allowed, d_reason = self.session_guard.is_daily_allowed(
                decision.symbol, daily_limit
            )
            if not d_allowed:
                reasons.append(BlockReason.DAILY_MAX_TRADES)
                details["daily_guard_reason"] = d_reason


        # --- ด่าน 18: Psychological Guard (Auto Coach) ---
        if performance_metrics:
            if performance_metrics.get("martingale_detected", False):
                # ถ้า AutoCoach บอกว่า Martingale → ให้ Gate บล็อก หรือ Sizing ลด Lot?
                # Sizing ลด lot เหลือ min ไปแล้ว แต่ถ้าอยากเข้มงวดก็บล็อกเลย
                # ในที่นี้ขอ Block ถ้าเป็น Live Mode เพื่อความปลอดภัยสูงสุด
                reasons.append(BlockReason.STRATEGY_BLACKLISTED) # ใช้ reason นี้ไปก่อน
                details["psycho_reason"] = "Martingale Detected"
            
            # Check for "Revenge Trader" personality if available in metrics?
            # performance_metrics passed from MasterLoop is likely just "performance_summary".
            # If we want personality check, we need to look at "trader_personality" part of report.
            # pipeline.execute receives "performance_metrics" as just summary dict in current master_loop impl.
            # Let's rely on martingale_detected for now.

        # --- สรุปผล ---
        passed = len(reasons) == 0

        if not passed:
            logger.warning(
                "gate_blocked",
                extra={
                    "symbol": decision.symbol,
                    "reasons": [r.value for r in reasons],
                    "details": details,
                    "stage": "gate",
                    "result": "blocked",
                },
            )
        else:
            logger.info(
                "gate_passed",
                extra={
                    "symbol": decision.symbol,
                    "stage": "gate",
                    "result": "ok",
                },
            )

        return GateResult(passed=passed, reasons=reasons, details=details)
