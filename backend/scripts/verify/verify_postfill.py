"""
Verify PostFillGuard (Mock MT5).
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

sys.path.append(str(Path(__file__).parents[3]))

from app.risk.postfill import PostFillGuard
from app.mt5.client import MT5Client

async def test_postfill_guard():
    print("Testing PostFillGuard...")
    
    # Mock MT5
    mt5 = MagicMock(spec=MT5Client)
    guard = PostFillGuard(mt5)
    
    # 1. Test Naked Position -> Should Close
    print("Case 1: Naked Position (No SL) -> Should Close")
    mock_pos = MagicMock()
    mock_pos.ticket = 12345
    mock_pos.symbol = "EURUSD"
    mock_pos.sl = 0.0 # Naked
    
    mt5.get_positions.return_value = [mock_pos]
    
    await guard.verify_all_active()
    
    mt5.close_position.assert_called_with(12345)
    print("  PASS: close_position called for naked trade.")

    # 2. Test Safe Position -> No Action
    print("\nCase 2: Safe Position -> No Action")
    mt5.reset_mock()
    mock_pos_safe = MagicMock()
    mock_pos_safe.ticket = 12346
    mock_pos_safe.sl = 1.1000
    
    mt5.get_positions.return_value = [mock_pos_safe]
    
    await guard.verify_all_active()
    
    mt5.close_position.assert_not_called()
    print("  PASS: No action for valid trade.")

if __name__ == "__main__":
    asyncio.run(test_postfill_guard())
