"""
Order Plan Builder — คำนวณ SL/TP และสร้าง OrderPlan ก่อนส่งเทรด.

หน้าที่:
1. รับ Decision จาก strategy
2. คำนวณ SL/TP อัตโนมัติ (ถ้า strategy ไม่ให้มา) โดยใช้ ATR
3. ตรวจสอบเงื่อนไข Broker (Stops Level, Min/Max distance)
4. คำนวณ Entry Price (Market/Limit)
5. ส่งต่อให้ sizing.calculate_lot_size คำนวณ lot
6. คืนค่า OrderPlan ที่พร้อมส่ง execution

"Hard Gatekeeper for Order Parameters"
"""

import numpy as np
import pandas as pd
from typing import Optional, Union, TYPE_CHECKING
if TYPE_CHECKING:
    from app.brain.personality import PersonalityProfile

from app.core.config import Settings
from app.core.logging import get_logger
from app.domain.enums import Action, BlockReason
from app.domain.models import AccountState, Decision, OrderPlan, SymbolProfile
from app.risk.sizing import calculate_lot_size

logger = get_logger(__name__)


class OrderPlanBuilder:
    """
    ตัวสร้างแผนคำสั่งเทรด (Order Plan).
    ทำหน้าที่ตรวจสอบและเติมเต็มพารามิเตอร์เทรดให้ครบถ้วนและปลอดภัย.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        # Default policy parameters (จะ override ด้วย config ถ้ามี)
        self.atr_period = 14
        self.atr_mult_sl = 1.6   # SL = 1.6 * ATR (user adjusted from 1.5)
        self.rr_target = 1.6    # TP = 1.5 * SL distance
        self.min_sl_points = 50  # ขั้นต่ำ 5 pips (50 points) หรือ config
        
    def build(
        self,
        decision: Decision,
        profile: SymbolProfile,
        account: AccountState,
        candles: pd.DataFrame | None = None,
        mt5_price: tuple[float, float] | None = None,
        personality: Optional["PersonalityProfile"] = None,
        performance_metrics: dict | None = None,
    ) -> Union[OrderPlan, BlockReason]:
        """
        สร้าง OrderPlan จาก Decision.

        Args:
            decision: การตัดสินใจจาก strategy
            profile: ข้อมูล symbol
            account: ข้อมูลบัญชี
            candles: ข้อมูลแท่งเทียน (สำหรับคำนวณ ATR)
            mt5_price: (bid, ask) ล่าสุดจาก MT5 (ถ้ามี)
            personality: ข้อมูล personality (Optional)

        Returns:
            OrderPlan หรือ BlockReason
        """
        if decision.action == Action.HOLD:
            return BlockReason.STRATEGY_HOLD

        # 1. Determine Base Price (Entry)
        bid, ask = 0.0, 0.0
        if mt5_price:
            bid, ask = mt5_price
        else:
            # Fallback: ใช้ close ราคาล่าสุดจาก candles
            if candles is not None and len(candles) > 0:
                close = float(candles["close"].iloc[-1])
                bid, ask = close, close # Spread เป็น 0 ใน fallback
            else:
                return BlockReason.MARKET_CLOSED # ไม่มีข้อมูลราคา

        # Entry Price logic
        # ถ้า Strategy ระบุราคาเข้า (Limit/Stop) ให้ใช้ตามนั้น
        # ถ้าไม่ระบุ (None) ถือเป็น Market Order
        is_buy = decision.action == Action.BUY
        market_price = ask if is_buy else bid
        entry_price = market_price # Default to market

        # TODO: Support Limit/Stop orders input from Decision (future)
        # ปัจจุบันรองรับ Market Execution เป็นหลัก

        # 2. Determine SL / TP
        sl_price = decision.stop_loss
        tp_price = decision.take_profit

        # ═══════════════════════════════════════════════════════════════
        # TP DIRECTION VALIDATION — ป้องกัน TP ผิดด้าน (Critical Safety)
        # BUY → TP ต้องอยู่เหนือ entry, SELL → TP ต้องอยู่ใต้ entry
        # ═══════════════════════════════════════════════════════════════
        if tp_price and tp_price > 0 and market_price > 0:
            if is_buy and tp_price < market_price:
                # BUY แต่ TP ต่ำกว่าราคาปัจจุบัน → ผิดด้าน!
                old_tp = tp_price
                tp_dist = abs(market_price - tp_price)
                tp_price = market_price + tp_dist  # Flip to correct side
                logger.warning("tp_direction_auto_fixed", extra={
                    "symbol": decision.symbol,
                    "action": "BUY",
                    "old_tp": old_tp,
                    "new_tp": tp_price,
                    "entry": market_price,
                    "strategy": decision.strategy_name,
                    "ALERT": "Strategy gave WRONG-SIDE TP — auto-corrected",
                })
            elif not is_buy and tp_price > market_price:
                # SELL แต่ TP สูงกว่าราคาปัจจุบัน → ผิดด้าน!
                old_tp = tp_price
                tp_dist = abs(tp_price - market_price)
                tp_price = market_price - tp_dist  # Flip to correct side
                logger.warning("tp_direction_auto_fixed", extra={
                    "symbol": decision.symbol,
                    "action": "SELL",
                    "old_tp": old_tp,
                    "new_tp": tp_price,
                    "entry": market_price,
                    "strategy": decision.strategy_name,
                    "ALERT": "Strategy gave WRONG-SIDE TP — auto-corrected",
                })

        # ═══════════════════════════════════════════════════════════════
        # SMART ADAPTIVE SL — ฉลาดกว่า ATR×1.6 คงที่
        # 1. Swing Structure: หา swing high/low ล่าสุดเพื่อวาง SL หลัง structure
        # 2. ATR Regime: ปรับ multiplier ตาม volatility (trend=แน่น, volatile=กว้าง)
        # 3. Best-of: เลือก SL ที่ปลอดภัยที่สุด (ไกลกว่า = ปลอดภัยกว่า)
        # 4. Trailing: inject config ให้ PositionGuardian ใช้ลาก SL อัตโนมัติ
        # ═══════════════════════════════════════════════════════════════
        if not sl_price:
            atr = self._calculate_atr(candles) if candles is not None else 0.0
            if atr <= 0:
                logger.warning("no_atr_missing_sl", extra={"symbol": decision.symbol})
                return BlockReason.NO_STOP_LOSS

            # ─── Step 1: Regime-Adaptive ATR Multiplier ───
            # ดู volatility ล่าสุด vs ค่าเฉลี่ย เพื่อปรับ multiplier
            base_mult = self.atr_mult_sl  # 1.6 default
            regime_tag = ""
            if candles is not None and len(candles) >= 50:
                fast_atr = self._calculate_atr(candles, 7)    # ATR 7 bars (recent)
                slow_atr = self._calculate_atr(candles, 50)   # ATR 50 bars (baseline)
                if fast_atr > 0 and slow_atr > 0:
                    vol_ratio = fast_atr / slow_atr
                    if vol_ratio > 1.5:
                        # Volatile → กว้างขึ้น ป้องกัน wick กิน
                        base_mult = min(2.5, base_mult * 1.3)
                        regime_tag = "VOLATILE"
                    elif vol_ratio > 1.2:
                        # Slightly elevated vol
                        base_mult = min(2.2, base_mult * 1.15)
                        regime_tag = "ELEVATED"
                    elif vol_ratio < 0.7:
                        # Quiet/trending → แน่นขึ้น จับกำไรเร็ว
                        base_mult = max(1.2, base_mult * 0.85)
                        regime_tag = "QUIET"
                    else:
                        regime_tag = "NORMAL"

            atr_sl_dist = max(self.min_sl_points * profile.point, atr * base_mult)

            # ─── Step 2: Swing Structure SL ───
            # หา swing high/low ล่าสุดเพื่อวาง SL หลัง structure
            swing_sl_dist = 0.0
            if candles is not None and len(candles) >= 20:
                try:
                    lows = candles["low"].iloc[-20:].values
                    highs = candles["high"].iloc[-20:].values
                    swing_buffer = atr * 0.3  # buffer 0.3 ATR หลัง swing

                    if is_buy:
                        # BUY: SL ใต้ swing low ล่าสุด
                        recent_low = float(lows.min())
                        swing_sl_dist = max(0, entry_price - recent_low + swing_buffer)
                    else:
                        # SELL: SL เหนือ swing high ล่าสุด
                        recent_high = float(highs.max())
                        swing_sl_dist = max(0, recent_high - entry_price + swing_buffer)
                except Exception:
                    pass  # fallback to ATR only

            # ─── Step 3: Best-of Selection ───
            # เลือกค่าที่ไกลกว่า (ปลอดภัยกว่า) แต่ไม่กว้างเกิน ATR×3
            max_sl_dist = atr * 3.0  # hard cap ที่ ATR×3
            if swing_sl_dist > 0:
                # ใช้ค่าที่ไกลกว่าระหว่าง ATR กับ Swing (ปลอดภัยกว่า)
                sl_dist = max(atr_sl_dist, swing_sl_dist)
                sl_method = "swing" if swing_sl_dist >= atr_sl_dist else "atr"
            else:
                sl_dist = atr_sl_dist
                sl_method = "atr"

            # Cap ไม่ให้กว้างเกิน ATR×3
            sl_dist = min(sl_dist, max_sl_dist)

            if is_buy:
                sl_price = entry_price - sl_dist
            else:
                sl_price = entry_price + sl_dist

            logger.info("smart_sl_calculated", extra={
                "symbol": decision.symbol,
                "method": sl_method,
                "atr": round(atr, 2),
                "atr_mult": round(base_mult, 2),
                "atr_sl_dist": round(atr_sl_dist, 2),
                "swing_sl_dist": round(swing_sl_dist, 2),
                "final_sl_dist": round(sl_dist, 2),
                "sl_price": round(sl_price, 2),
                "regime": regime_tag,
            })

        # ถ้าไม่มี TP ให้คำนวณจาก RR
        if not tp_price and sl_price:
            dist = abs(entry_price - sl_price)
            tp_dist = dist * self.rr_target
            
            if is_buy:
                tp_price = entry_price + tp_dist
            else:
                tp_price = entry_price - tp_dist

        # 3. Normalize & Validate SL/TP (Broker Constraints)
        if not sl_price:
            return BlockReason.NO_STOP_LOSS

        # Rounding
        sl_price = round(sl_price, profile.digits)
        if tp_price:
            tp_price = round(tp_price, profile.digits)

        # Stops Level Check
        # ตรวจว่า SL/TP ใกล้ราคาปัจจุบันเกินไปหรือไม่
        # StopsLevel คือระยะต่ำสุดที่ broker ยอมให้วาง pending/sl/tp
        min_dist = max(profile.spread_avg * 1.5 * profile.point, 10 * profile.point) # heuristic fallback
        
        dist_to_sl = abs(entry_price - sl_price)
        if dist_to_sl < min_dist:
            logger.warning("sl_too_close", extra={
                "symbol": decision.symbol,
                "sl": sl_price,
                "entry": entry_price,
                "dist": dist_to_sl,
                "min": min_dist
            })
            return BlockReason.SL_TOO_CLOSE

        # 4. Calculate Risk Parity ATRs (if enabled)
        current_atr = None
        baseline_atr = None
        if getattr(self.settings, 'risk_parity_enabled', True) and candles is not None and not candles.empty:
            period = getattr(self.settings, 'risk_parity_atr_period', 14)
            baseline_period = getattr(self.settings, 'risk_parity_baseline_ema', 200)
            
            # calculate fast ATR (current volatility)
            current_atr = self._calculate_atr(candles, period)
            # calculate slow ATR (baseline volatility)
            baseline_atr = self._calculate_atr(candles, baseline_period)

        # 5. Calculate Lot Size
        # เรียก sizing.calculate_lot_size (ที่ refactor แล้วรับ stop_loss, risk parity ได้)
        plan_or_error = calculate_lot_size(
            decision=decision,
            profile=profile,
            account=account,
            settings=self.settings,
            entry_price=entry_price,
            stop_loss=sl_price,
            personality=personality,
            performance_metrics=performance_metrics,
            current_atr=current_atr,
            baseline_atr=baseline_atr,
        )

        if isinstance(plan_or_error, BlockReason):
            return plan_or_error
        
        order_plan: OrderPlan = plan_or_error
        
        # update TP ใน plan ถ้ามีการคำนวณใหม่
        order_plan.take_profit = tp_price

        # Update ghost protocol flag
        order_plan.ghost_protocol = decision.debug.get("ghost_protocol", False)
        
        # 5. Populate Trailing Configuration
        # ใส่ค่า config สำหรับ Trailing system ที่จะใช้อ่านทีหลัง
        if self.settings.trailing_stop_mode != "off":
            # คำนวณ ATR เพื่อใช้เป็นระยะ trail
            atr = self._calculate_atr(candles) if candles is not None else 0.0
            if atr > 0:
                trail_dist = max(self.min_sl_points * profile.point, atr * 1.5) # Trail 1.5 ATR
                order_plan.trailing_config = {
                    "enabled": True,
                    "activation_r": 1.0,      # เริ่มทำงานที่ +1R (Break Even)
                    "lock_r": 2.0,            # Lock profit ที่ +2R
                    "trail_dist": trail_dist,  # ระยะห่าง Trailing
                    "step": 10 * profile.point # ขยับทีละ 10 points
                }

        return order_plan

    def _calculate_atr(self, candles: pd.DataFrame, custom_period: int | None = None) -> float:
        """คำนวณ ATR แบบ RMA (Wilder's Smoothing) จาก candles."""
        try:
            period = custom_period if custom_period is not None else self.atr_period
            if candles is None or len(candles) < period + 1:
                return 0.0
            
            high = candles["high"].values
            low = candles["low"].values
            close = candles["close"].values
            
            # True Range
            tr = np.maximum(
                high[1:] - low[1:],
                np.maximum(
                    np.abs(high[1:] - close[:-1]),
                    np.abs(low[1:] - close[:-1]),
                ),
            )
            
            if len(tr) < period:
                return 0.0
                
            # Simple Average of TR (เหมือน TradingView RMA จะซับซ้อนกว่า แต่ SMA ก็ใช้ได้)
            # เพื่อความแม่นยำ ใช้ Wilder's Smoothing (RMA)
            # RMA[i] = (RMA[i-1] * (n-1) + TR[i]) / n
            
            rma = np.mean(tr[:period]) # First value is SMA
            for i in range(period, len(tr)):
                rma = (rma * (period - 1) + tr[i]) / period
                
            return float(rma)
        except Exception as e:
            logger.error("atr_calc_error", extra={"error": str(e)})
            return 0.0
