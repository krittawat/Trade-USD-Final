
import asyncio
from unittest.mock import MagicMock
from app.core.config import get_settings
from app.execution.pipeline import ExecutionPipeline
from app.domain.models import Decision, SymbolProfile, AccountState
from app.domain.enums import Action
from app.db.sqlite import SQLiteStore
import os

async def verify():
    print("=== Verifying Error Logging ===")
    
    # 1. Setup DB
    settings = get_settings()
    # Use a temp DB for verification to not pollute live DB
    settings.__dict__["sqlite_db_path"] = "backend/data/sqlite/verify_logging.db"
    
    if os.path.exists(settings.sqlite_db_path):
        os.remove(settings.sqlite_db_path)
        
    db = SQLiteStore(settings)
    db.connect()
    
    # 2. Mock MT5 to raise error
    mock_mt5 = MagicMock()
    mock_mt5.is_connected.return_value = True
    mock_mt5.is_market_open.return_value = True
    mock_mt5.get_current_spread.return_value = 10.0
    mock_mt5.count_positions.return_value = 0
    mock_mt5.get_current_price.return_value = (2000.0, 2000.1)
    # CRITICAL: Simulate error during order send
    mock_mt5.send_order.side_effect = Exception("SIMULATED_ORDER_FAILURE")
    
    # 3. Setup Pipeline
    # Force LIVE mode to attempt execution
    settings.__dict__["trading_mode"] = "LIVE"
    
    # Mock Gate to pass everything
    from app.domain.models import GateResult
    mock_gate = MagicMock()
    mock_gate.check.return_value = GateResult(passed=True, reasons=[], details={})

    pipeline = ExecutionPipeline(
        settings=settings,
        mt5_client=mock_mt5,
        gate=mock_gate,
        db=db
    )
    
    # 4. Create dummy inputs
    decision = Decision(
        action=Action.BUY,
        symbol="XAUUSDc",
        confidence=0.9,
        strategy_name="test_strat",
        reason="Test Reason",
        stop_loss=1990.0,  # Valid SL
        take_profit=2010.0
    )
    profile = SymbolProfile(symbol="XAUUSDc")
    account = AccountState(balance=1000, equity=1000)
    
    # 5. Execute
    print("Executing pipeline...")
    result = await pipeline.execute(decision, profile, account)
    
    # 6. Verify result
    print(f"Pipeline Result: {result['result']}")
    print(f"Pipeline Reason: {result['reason']}")
    
    # Force flush buffer
    db.flush_traces()
    
    # 7. Check DB Persistence
    print("Checking DB persistence...")
    traces = db.get_decisions(symbol="XAUUSDc", limit=1)
    
    if not traces:
        print("FAILED: No traces found")
        return
        
    trace = traces[0]
    print(f"DB Result: {trace['result']}")
    print(f"DB Reason: {trace['reason']}")
    print(f"DB Details: {trace['details']}")
    
    if trace['result'] == 'error' and "SIMULATED_ORDER_FAILURE" in str(trace['details']):
        print("\nSUCCESS: Error successfully persisted to DB!")
    else:
        print("\nFAILED: Error details not found in DB")

    db.disconnect()
    if os.path.exists(settings.sqlite_db_path):
        os.remove(settings.sqlite_db_path)

if __name__ == "__main__":
    asyncio.run(verify())
