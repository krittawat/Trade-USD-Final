import sys
from pathlib import Path

# Add project root to sys.path
sys.path.append(str(Path(__file__).parent.parent.parent))

from app.domain.models import AccountState, Decision, SymbolProfile, Action
from app.core.config import Settings
from app.risk.sizing import calculate_lot_size
from app.core.mode_resolver import RuntimeMode
from app.core.currency_adapter import CurrencyAdapter

# Setup
settings = Settings()
settings.account_currency = "USC"
settings.lot_mode = "CENT"
settings.max_risk_per_trade_pct = 2.0

# 1. Adapter logic (Misconfigured as USD account, but Cent lots)
mode = RuntimeMode(
    account_currency="USD",
    lot_mode="CENT",
    symbol_suffix="c",
    cent_multiplier=1.0  # Incorrect for a cent account!
)
adapter = CurrencyAdapter(settings, mode)

# 2. MT5 Client logic
balance_raw = 5000.0 # 5000 USC
balance_usd = adapter.normalize_money(balance_raw)
equity_usd = adapter.normalize_money(balance_raw)

account = AccountState(
    balance=balance_usd,
    equity=equity_usd,
    margin=0.0,
    free_margin=equity_usd,
    floating_pl=0.0,
    open_positions=0,
    daily_pl=0.0,
    initial_balance=balance_usd,
    leverage=2000
)

# 3. Decision & Profile
decision = Decision(
    symbol="EURUSD",
    action=Action.BUY,
    score=90,
    confidence=0.9,
    reason="test",
    strategy_name="Test",
    stop_loss=1.18022,
    take_profit=1.18178,
    tags=[],
)

# Assuming EURUSDc contract size varies. Let's test 100,000
profile = SymbolProfile(
    symbol="EURUSD",
    contract_size=100000,
    volume_min=0.01,
    volume_max=200.0,
    volume_step=0.01,
    point=0.00001,
    digits=5,
    spread_avg=10,
    spread_max_allowed=30,
    is_active=True
)

entry_price = 1.18097

plan = calculate_lot_size(
    decision=decision,
    profile=profile,
    account=account,
    settings=settings,
    entry_price=entry_price
)

print(f"Equity USD: {account.equity}")
print(f"Plan: {plan}")

if hasattr(plan, 'lot_size'):
    broker_vol = adapter.to_broker_lots(plan.lot_size)
    print(f"Broker Volume sent to MT5: {broker_vol}")
