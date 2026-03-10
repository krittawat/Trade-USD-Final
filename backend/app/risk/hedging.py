"""
Hedging Manager (Anti-Doi)
==========================
Autonomously locks risk when drawdown exceeds safety thresholds.
"The Shield" — Protects account from blowing up during extreme volatility.

Principles:
1.  **Monitor**: Checks Net Exposure & Drawdown every cycle.
2.  **Trigger**:
    - Account Drawdown % > threshold (e.g. 15%)
    - Symbol Floating Loss > threshold (e.g. -$500)
3.  **Action**: Opens opposite trade to neutralize delta (Net Lots = 0).
4.  **Safety**: Checks properly if already hedged to verify "Lock Effectiveness".
"""

import asyncio
import time
from typing import List, Dict, Optional
from app.core.logging import get_logger
from app.mt5.client import MT5Client
from app.core.config import Settings
import MetaTrader5 as mt5

logger = get_logger(__name__)

class HedgeManager:
    """
    Manages Auto-Hedging (Anti-Doi).
    """

    def __init__(self, mt5_client: MT5Client, settings: Settings):
        self.mt5 = mt5_client
        self.settings = settings
        self._last_hedge_time: Dict[str, float] = {}
        self._hedge_cooldown = 60.0  # 1 minute cooldown per symbol

    async def monitor_risks(self, positions: List[dict], account_equity: float, account_balance: float) -> None:
        """
        Check all positions and account health. Trigger hedge if critical.
        """
        if not self.settings.hedge_enable:
            return

        if not self.mt5.is_connected():
            return

        # 1. Account-Level Check (Drawdown %)
        # If Equity < Balance * (1 - threshold), we are in danger.
        dd_pct = 0.0
        if account_balance > 0:
            dd_pct = (1.0 - (account_equity / account_balance)) * 100.0

        is_critical_dd = dd_pct >= self.settings.hedge_threshold_dd_pct

        # Group positions by symbol
        symbol_map: Dict[str, List[dict]] = {}
        for pos in positions:
            sym = pos['symbol']
            if sym not in symbol_map:
                symbol_map[sym] = []
            symbol_map[sym].append(pos)

        # Check each symbol
        for symbol, pos_list in symbol_map.items():
            await self._check_symbol_hedge(symbol, pos_list, is_critical_dd)

    async def _check_symbol_hedge(self, symbol: str, positions: List[dict], is_critical_dd: bool) -> None:
        """
        Analyze a specific symbol for hedging requirements.
        """
        # Calculate Net Lots and PnL
        net_lots = 0.0
        total_pnl = 0.0
        buy_lots = 0.0
        sell_lots = 0.0

        for p in positions:
            vol = p['volume']
            profit = p['profit'] + p['swap']
            total_pnl += profit
            if p['type'] == 'BUY': # MT5 0=BUY
                net_lots += vol
                buy_lots += vol
            elif p['type'] == 'SELL': # MT5 1=SELL
                net_lots -= vol
                sell_lots += vol
        
        # If net lots is almost zero (already hedged), skip
        if abs(net_lots) < 0.01:
            return

        # Check Triggers
        # A. Global Account Drawdown Critical -> Hedge deepest loser? Or all?
        # For now, if Account is Critical, we hedge ANY symbol that contributing significantly to loss?
        # Or just hedge the net exposure of this symbol if it's losing money?
        
        # B. Symbol Specific Threshold
        # If total_pnl < negative_threshold (e.g. -100 USD)
        is_symbol_loss_critical = False
        if self.settings.hedge_threshold_usd < 0: # Configured as negative value e.g. -500
             if total_pnl <= self.settings.hedge_threshold_usd:
                 is_symbol_loss_critical = True
        elif self.settings.hedge_threshold_usd > 0: # Configured as positive e.g. 500 (meaning loss > 500)
             if total_pnl <= -self.settings.hedge_threshold_usd:
                 is_symbol_loss_critical = True

        trigger = is_critical_dd or is_symbol_loss_critical

        if not trigger:
            return

        # Check Cooldown
        if time.time() - self._last_hedge_time.get(symbol, 0) < self._hedge_cooldown:
            return

        # Calculate Hedge Action
        # We want to neutralize net_lots.
        # If net_lots > 0 (Long bias), we need to SELL.
        # If net_lots < 0 (Short bias), we need to BUY.
        
        hedge_ratio = self.settings.hedge_ratio # 1.0 normally
        qty_needed = abs(net_lots) * hedge_ratio
        
        # Round to 2 decimals (standard lot step, TODO: use symbol info for step)
        qty_needed = round(qty_needed, 2)
        if qty_needed < 0.01:
            return

        action_type = mt5.ORDER_TYPE_SELL if net_lots > 0 else mt5.ORDER_TYPE_BUY
        str_type = "SELL" if action_type == mt5.ORDER_TYPE_SELL else "BUY"

        logger.warning("hedge_triggered", extra={
            "symbol": symbol,
            "reason": "CRITICAL_DD" if is_critical_dd else "SYMBOL_LOSS",
            "dd_pct": f"{is_critical_dd} (Global)",
            "pnl": total_pnl,
            "net_lots": net_lots,
            "action": f"{str_type} {qty_needed} lots"
        })

        # Execute Hedge
        await self._execute_hedge(symbol, action_type, qty_needed)

    async def _execute_hedge(self, symbol: str, order_type: int, volume: float) -> None:
        """
        Execute the hedge order directly via MT5.
        Bypasses standard gate for speed, but checks basic connectivity.
        """
        price = 0.0
        # Get correct price
        tick = self.mt5.get_symbol_tick(symbol)
        if not tick:
            logger.error("hedge_failed_no_tick", extra={"symbol": symbol})
            return
            
        if order_type == mt5.ORDER_TYPE_BUY:
            price = tick.ask
        else:
            price = tick.bid

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": 20,
            "magic": 999999, # Special Magic for Hedge
            "comment": "Auto-Hedge Shield",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        # Sync call to order_send (fastest)
        result = await asyncio.to_thread(mt5.order_send, request)
        
        if result.retcode != mt5.TRADE_RETCODE_DONE:
             logger.error("hedge_execution_failed", extra={
                 "symbol": symbol,
                 "retcode": result.retcode,
                 "comment": result.comment
             })
        else:
             logger.info("hedge_executed_successfully", extra={
                 "symbol": symbol,
                 "ticket": result.order,
                 "volume": volume
             })
             self._last_hedge_time[symbol] = time.time()
             
             # Notify (if telegram available in context, or just log for now)
             # MasterLoop will see the new position next cycle.

