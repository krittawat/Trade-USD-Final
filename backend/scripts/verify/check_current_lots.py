import sys
import os
from pathlib import Path

# Fix paths
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.domain.models import AccountState, Decision, SymbolProfile, Action
from app.core.config import Settings
from app.risk.sizing import calculate_lot_size
from app.core.mode_resolver import RuntimeMode
from app.core.currency_adapter import CurrencyAdapter
from app.domain.enums import BlockReason

def check_lots():
    # Setup exact current state
    settings = Settings()
    settings.account_currency = "USC"
    settings.lot_mode = "CENT"
    settings.max_risk_per_trade_pct = 2.0
    
    mode = RuntimeMode(
        account_currency="USC",
        lot_mode="CENT",
        symbol_suffix="c",
        cent_multiplier=100.0,
    )
    adapter = CurrencyAdapter(settings, mode)
    
    # Approx current real balance
    equity = 6002.0
    equity_usd = adapter.normalize_money(equity)
    
    account = AccountState(
        balance=equity_usd,
        equity=equity_usd,
        margin=0.0,
        free_margin=equity_usd,
        floating_pl=0.0,
        open_positions=0,
        daily_pl=0.0,
        initial_balance=equity_usd,
        leverage=2000,
    )
    
    print(f"💰 Current Account Equity: {equity} USC => ${equity_usd:.2f} USD")
    print(f"⚠️ Max Base Risk (2%): ${equity_usd * 0.02:.2f} USD\\n")

    # Define pairs with normal stop loss distances
    # XAUUSD: avg sl distance 3.0 (300 points)
    # XAGUSD: avg sl distance 0.30
    # BTCUSD: avg sl distance 500.0
    # EURUSD: avg sl distance 0.00200
    pairs = [
        ("XAUUSDc", 100, 3.0, 2),
        ("XAGUSDc", 5000, 0.30, 3),
        ("BTCUSDc", 1, 500.0, 2),
        ("EURUSDc", 100000, 0.00200, 5)
    ]
    
    for sym_name, contract_size, sl_dist, digits in pairs:
        profile = SymbolProfile(
            symbol=sym_name,
            contract_size=contract_size,
            volume_min=0.01,
            volume_max=200.0,
            volume_step=0.01,
            point=10**(-digits),
            digits=digits,
            spread_avg=20,
            is_active=True,
        )
        
        entry_price = 1000.0 if sym_name != "EURUSDc" else 1.10000
        stop_loss = entry_price - sl_dist
        
        decision = Decision(
            symbol=sym_name,
            action=Action.BUY,
            score=90,
            confidence=0.9,
            reason="check",
            strategy_name="check",
            stop_loss=stop_loss,
            take_profit=entry_price + sl_dist*2,
        )
        
        # Test max allowed lot (using actual settings)
        plan = calculate_lot_size(
            decision=decision,
            profile=profile,
            account=account,
            settings=settings,
            entry_price=entry_price
        )
        
        if isinstance(plan, BlockReason):
            print(f"- {sym_name:8s}: {plan.name} (Risk ceiling too small to afford 0.01 lot)")
        else:
            broker_lot = adapter.to_broker_lots(plan.lot_size)
            print(f"- {sym_name:8s}: NORMAL LOT = {broker_lot:.2f} (Risk {plan.risk_pct:.2f}%, ${plan.risk_usd:.2f})")
        
        # What is the theoretical max lot at 2% risk config?
        # risk_usd = equity_usd * 0.02
        # lot_size = risk_usd / (sl_dist * contract_size)
        theoretical_lot = (equity_usd * 0.02) / (sl_dist * contract_size)
        if theoretical_lot < 0.01:
            broker_lot_theoretical = adapter.to_broker_lots(0.0) # would be blocked
            print(f"           [Min Lot 0.01 requires Risk USD: ${0.01 * contract_size * sl_dist:.2f} | We only have ${equity_usd * 0.02:.2f}]")
        else:
            print(f"           [Max Theoretical at 2% risk = {adapter.to_broker_lots(theoretical_lot):.2f} broker lots]")
            
        
if __name__ == '__main__':
    check_lots()
