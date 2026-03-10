"""Full pipeline smoke test — Phase 3: verify all imports + persistence."""
import sys
import os
os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding='utf-8') if hasattr(sys.stdout, 'reconfigure') else None

print("=== Phase 3 Full Pipeline Test ===")

# 1. Core
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.mode import TradingMode
from app.core.errors import ConnectionError, OrderError
print("[OK] Core modules")

# 2. Domain
from app.domain.enums import Action, BlockReason, RegimeType, MarketSession, TradeStage
from app.domain.models import Decision, OrderPlan, GateResult, SymbolProfile, AccountState
print("[OK] Domain models")

# 3. MT5
from app.mt5.client import MT5Client, TIMEFRAME_MAP
from app.mt5.market_data import fetch_candles, fetch_ticks
print("[OK] MT5 layer")

# 4. Risk
from app.risk.gate import PreTradeGate
from app.risk.sizing import calculate_lot_size
from app.risk.postfill import PostFillGuard
from app.risk.guards import check_floating_dd, check_capital_floor, check_daily_loss
from app.risk.breakeven import should_move_to_breakeven, mark_be_moved
print("[OK] Risk engine (gate + guards + sizing + postfill + breakeven)")

# 5. Brain
from app.brain.regime import classify_regime
print("[OK] Brain / Regime classifier")

# 6. Strategy
from app.strategy.base import BaseStrategy
from app.strategy.factory import StrategyFactory
from app.strategy.templates.scalping import ScalpingStrategy
from app.strategy.templates.sniper import SniperStrategy
from app.strategy.templates.trend_rider import TrendRiderStrategy
print("[OK] Strategy factory + 3 templates")

# 7. Execution
from app.execution.pipeline import ExecutionPipeline
print("[OK] Execution pipeline")

# 8. Services
from app.services.session import get_current_session
from app.services.news_filter import NewsFilter
print("[OK] Services (session + news filter)")

# 9. DB
from app.db.sqlite import SQLiteStore
from app.db.questdb import QuestDBClient
print("[OK] DB (SQLite + QuestDB)")

# 10. Master Loop
from app.master_loop import MasterLoop
print("[OK] Master loop")

# 11. API routes
from app.api.routes.health import router as health_router
from app.api.routes.symbols import router as symbols_router
from app.api.routes.decisions import router as decisions_router
from app.api.routes.analytics import router as analytics_router
print("[OK] API routes (health + symbols + decisions + analytics)")

# 12. Factory wiring
factory = StrategyFactory()
factory.register(ScalpingStrategy())
factory.register(SniperStrategy())
factory.register(TrendRiderStrategy())
print(f"[OK] Factory: {len(factory._strategies)} strategies")

# 13. SQLite persistence test
import tempfile, os
settings = get_settings()
settings_dict = settings.model_dump()
# Use temp file for test
test_db = os.path.join(tempfile.gettempdir(), "test_trading.db")
settings.__dict__["sqlite_db_path"] = test_db
store = SQLiteStore(settings)
store.connect()
print("[OK] SQLite connected")

# Save decision trace
store.save_decision_trace(
    symbol="XAUUSDc",
    stage="signal",
    result="ok",
    action="BUY",
    confidence=0.72,
    strategy_name="scalping",
    reason="EMA crossover",
    regime="TRENDING_UP",
    session="LONDON",
)

# Query it back
decisions = store.get_decisions(symbol="XAUUSDc", limit=1)
assert len(decisions) == 1, f"Expected 1 decision, got {len(decisions)}"
assert decisions[0]["strategy_name"] == "scalping"
print("[OK] Decision trace saved + queried")

# Save trade
trade_id = store.save_trade(
    symbol="XAUUSDc",
    action="BUY",
    lot_size=0.01,
    entry_price=2850.5,
    stop_loss=2845.0,
    take_profit=2860.0,
    risk_usd=5.5,
    risk_pct=1.5,
    strategy_name="scalping",
)
assert trade_id > 0
print(f"[OK] Trade saved (id={trade_id})")

# Analytics (no closed trades yet, so empty)
analytics = store.get_analytics()
assert analytics["total_trades"] == 0  # no exit_price yet
print(f"[OK] Analytics: {analytics}")

# Runtime state
store.set_state("mode", "DRY_RUN")
mode_val = store.get_state("mode")
assert mode_val == "DRY_RUN"
print("[OK] Runtime state: set + get")

store.disconnect()
os.remove(test_db)
print("[OK] SQLite cleanup done")

# 14. News filter
nf = NewsFilter(30)
assert nf.is_safe("XAUUSDc") == True  # no events cached = safe
print("[OK] News filter (default safe)")

# 15. MT5 live test
import MetaTrader5 as mt5
import pandas as pd
mt5.initialize()
rates = mt5.copy_rates_from_pos("XAUUSDc", mt5.TIMEFRAME_M5, 0, 250)
if rates is not None:
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df.set_index("time", inplace=True)
    df.rename(columns={"tick_volume": "volume"}, inplace=True)

    regime = classify_regime(df)
    print(f"[OK] Regime: {regime.value}")

    profile = SymbolProfile(symbol="XAUUSDc", digits=3, point=0.001, contract_size=100)
    decision = factory.get_decision(df, profile, regime)
    print(f"[OK] Decision: {decision.action.value} | conf={decision.confidence} | {decision.strategy_name}")

    session = get_current_session()
    print(f"[OK] Session: {session.value}")

    # Gate test with daily loss
    gate = PreTradeGate(settings)
    account = AccountState(
        balance=100, equity=95, initial_balance=100,
        daily_pl=-6.0,  # -6% loss → should trigger daily loss block
    )
    gate_result = gate.check(
        decision=decision, profile=profile, account=account,
        mt5_connected=True, market_open=True,
        current_spread=0.5, current_session=session.value,
    )
    print(f"[OK] Gate: passed={gate_result.passed}")
    if not gate_result.passed:
        print(f"     Blocked: {[r.value for r in gate_result.reasons]}")
else:
    print("[!] No MT5 data")

mt5.shutdown()

print()
print("=" * 60)
print("PHASE 3: ALL TESTS PASSED")
print("=" * 60)
