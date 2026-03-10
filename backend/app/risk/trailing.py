"""
Trailing Stop Manager — บริหารจัดการ SL ระหว่างถือ Position.

หน้าที่:
1. Break-Even: ย้าย SL มาที่จุดคุ้มทุนเมื่อกำไรถึงระยะ (เช่น +1R).
2. Profit Lock: ล็อคกำไรขั้นต่ำเมื่อราคาไปไกลแล้ว (เช่น +2R lock +1R).
3. Trailing Stop: เลื่อน SL ตามราคาเมื่อกำไรเพิ่มขึ้นเรื่อยๆ.
4. Spam Protection: ป้องกันการส่งคำสั่งถี่เกินไป.

Policy Default:
- Activation: +1.0 R -> Move to BE + Buffer
- Lock Step 1: +2.0 R -> Lock +1.0 R
- Lock Step 2: +3.0 R -> Lock +2.0 R (stepwise trailing)
- Trailing Mode: Stepwise or Dynamic (ATR based)
"""

import asyncio
import time
from typing import Dict, Optional, Any
from dataclasses import dataclass, field

from app.core.config import Settings
from app.core.logging import get_logger
from app.mt5.client import MT5Client
from app.domain.models import SymbolProfile

logger = get_logger(__name__)

@dataclass
class TrailingConfig:
    mode: str = "off"
    atr_multiplier: float = 3.0
    fixed_points: float = 0.0
    activation_r: float = 1.0
    step_r: float = 0.5
    chandelier_period: int = 14
    adaptive_min_mult: float = 1.0
    adaptive_max_mult: float = 3.0
    adaptive_ramp_r: float = 3.0
    created_at: float = field(default_factory=time.time)

@dataclass
class TrailingState:
    peak_price: float = 0.0
    last_sl: float = 0.0
    activated: bool = False
    last_step_r: float = 0.0
    created_at: float = field(default_factory=time.time)

class TrailingManager:
    """
    ผู้จัดการ Trailing Stop สำหรับทุก position ที่เปิดอยู่.
    Supports both Hardcoded Logic (Gold Ratchet) and Configurable Logic (API).
    """

    def __init__(self, mt5_client: MT5Client, settings: Settings):
        self.mt5 = mt5_client
        self.settings = settings
        
        # Cache สถานะล่าสุดเพื่อลดการ spam log/modify
        self._last_modified_time: Dict[int, float] = {}
        self._min_modify_interval = 5.0  # seconds
        
        # API Configurable State
        self._configs: Dict[int, TrailingConfig] = {}
        self._states: Dict[int, TrailingState] = {}
        
        # Default policies
        self.be_activation_r = 1.0
        self.be_buffer_points = 50 
        self.lock_r_trigger = 2.0
        self.lock_r_target = 1.2
        self.trailing_step_r = 0.5

    def set_trailing(self, ticket: int, config: TrailingConfig) -> None:
        """ตั้ง trailing config ให้ ticket (Override default logic)."""
        try:
            self._configs[ticket] = config
            if ticket not in self._states:
                self._states[ticket] = TrailingState()
            
            mode = getattr(config, 'mode', 'unknown')
            logger.info("trailing_set_override", extra={
                "ticket": ticket, "mode": mode
            })
        except Exception as e:
            logger.error("trailing_set_error", extra={"ticket": ticket, "error": str(e), "type": str(type(e))})
            raise e

    def remove_trailing(self, ticket: int) -> None:
        self._configs.pop(ticket, None)
        self._states.pop(ticket, None)
        logger.info("trailing_removed", extra={"ticket": ticket})

    def get_status(self, ticket: int) -> dict:
        cfg = self._configs.get(ticket)
        state = self._states.get(ticket)
        if not cfg:
            return {"active": False, "mode": "default/ratchet"}
        return {
            "active": True,
            "mode": cfg.mode,
            "peak": state.peak_price if state else 0.0,
            "activated": state.activated if state else False
        }

    async def process_all_positions(self):
        """
        Loop ตรวจสอบทุก position และปรับ SL ตามเหมาะสม.
        """
        # Note: mt5.get_positions returns list of Dict, not objects.
        positions = self.mt5.get_positions()
        if not positions:
            return

        for pos in positions:
            try:
                ticket = pos['ticket']
                symbol = pos['symbol']
                price_current = pos['price_current']
                price_open = pos['price_open']
                sl = pos['sl']
                tp = pos['tp']
                # mt5.client returns "BUY" or "SELL" string
                is_buy = (pos['type'] == 'BUY')

                # Skip if recently modified
                last_time = self._last_modified_time.get(ticket, 0)
                if time.time() - last_time < self._min_modify_interval:
                    continue
                
                # Setup point value
                point = 0.001 if "JPY" in symbol else 0.00001
                if "XAU" in symbol: point = 0.01
                elif "BTC" in symbol: point = 1.0
                
                risk_points = 500 * point 
                
                if is_buy:
                    profit_points = price_current - price_open
                    sl_dist = price_open - sl if sl > 0 else risk_points
                else: # SELL
                    profit_points = price_open - price_current
                    sl_dist = sl - price_open if sl > 0 else risk_points
                
                if sl_dist <= 0: sl_dist = risk_points
                current_r = profit_points / sl_dist
                
                new_sl = None
                reason = ""
                
                # Check for Override Config
                config = self._configs.get(ticket)
                if config and config.mode != "off":
                    state = self._states.setdefault(ticket, TrailingState())
                    
                    # Update Peak
                    if is_buy:
                        if price_current > state.peak_price or state.peak_price == 0:
                            state.peak_price = price_current
                    else: # SELL
                        if price_current < state.peak_price or state.peak_price == 0:
                            state.peak_price = price_current
                            
                    # Calculate Logic
                    if config.mode == "dynamic" or config.mode == "atr":
                         # Approx ATR from SL dist if real ATR not available
                         atr = sl_dist 
                         trail_dist = atr * config.atr_multiplier
                         
                         if config.activation_r > 0 and current_r < config.activation_r:
                             pass # Not activated
                         else:
                             if is_buy:
                                 target = state.peak_price - trail_dist
                                 # Monotonic check
                                 if target > sl and target < price_current:
                                     new_sl = target
                                     reason = f"Dynamic Trailing (Act {config.activation_r}R)"
                             else:
                                 target = state.peak_price + trail_dist
                                 if (target < sl or sl == 0) and target > price_current:
                                     new_sl = target
                                     reason = f"Dynamic Trailing (Act {config.activation_r}R)"
                else:
                    # Default Logic
                    if "XAU" in symbol or "GOLD" in symbol:
                         new_sl, reason = self._apply_ratchet_logic_values(
                             is_buy, price_current, price_open, sl, current_r, sl_dist, point
                         )
                    else:
                        # Forex Default
                        if current_r >= self.be_activation_r:
                            buffer = self.be_buffer_points * point
                            if is_buy:
                                target = price_open + buffer
                                if sl < target:
                                    new_sl = target
                                    reason = "BE Default"
                            else:
                                target = price_open - buffer
                                if sl > target or sl == 0:
                                    new_sl = target
                                    reason = "BE Default"

                # Execute
                if new_sl:
                    digits = 2 if "XAU" in symbol else 5 
                    if "JPY" in symbol: digits = 3
                    
                    new_sl = round(new_sl, digits)
                    
                    if abs(new_sl - sl) < (10 * point):
                        return

                    logger.info("trailing_modify", extra={
                        "ticket": ticket,
                        "old_sl": sl,
                        "new_sl": new_sl,
                        "reason": reason
                    })
                    
                    res = self.mt5.modify_sl(ticket, new_sl=new_sl, new_tp=tp)
                    # MT5Client.modify_sl checks retcode internally and returns bool
                    if res:
                        self._last_modified_time[ticket] = time.time()

            except Exception as e:
                logger.error("trailing_process_error", extra={"ticket": pos.get('ticket', 'unknown') if isinstance(pos, dict) else 'unknown', "error": str(e)})

    def _apply_ratchet_logic_values(self, is_buy, current_price, entry_price, sl_price, current_r, sl_dist, point):
        """
        Refactored to take direct values instead of pos object.
        """
        new_sl = None
        reason = ""
        buffer = self.be_buffer_points * point
        
        if current_r >= 1.0:
            if is_buy:
                target = entry_price + buffer
                if sl_price < target: 
                    new_sl = target
                    reason = "Ratchet Step 1 (BE)"
            else:
                target = entry_price - buffer
                if sl_price > target or sl_price == 0: 
                    new_sl = target
                    reason = "Ratchet Step 1 (BE)"
        
        return new_sl, reason

    # Kept for compatibility if used elsewhere, but redirected
    def _apply_ratchet_logic(self, pos, current_r, entry_price, sl_dist, point):
        is_buy = (pos['type'] == 'BUY') if isinstance(pos, dict) else (pos.type == 0)
        sl = pos['sl'] if isinstance(pos, dict) else pos.sl
        current = pos['price_current'] if isinstance(pos, dict) else pos.price_current
        return self._apply_ratchet_logic_values(is_buy, current, entry_price, sl, current_r, sl_dist, point)

    def check_time_decay(self, pos_ref, elapsed_minutes: float, current_r: float) -> dict | None:
        # pos_ref is a SimpleNamespace/object with .ticket (passed from MasterLoop)
        if current_r >= 1.0: return None
        if elapsed_minutes >= 90 and current_r < 0.5:
            return {"action": "CLOSE", "reason": "TimeDecay", "ticket": pos_ref.ticket}
        return None

    def check_pulse_exit(self, pos_ref, rsi_value: float, direction: str, current_r: float) -> dict | None:
        # pos_ref is a SimpleNamespace/object with .ticket (passed from MasterLoop)
        return None
