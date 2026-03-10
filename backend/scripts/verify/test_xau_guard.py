import sys
from pathlib import Path
from unittest.mock import patch

# Add project root to path so `backend.trader` imports work from any CWD.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.trader.risk.gate import RiskEngine
from backend.trader.risk.opus_governor import OPUSGovernor, OPUSStatus

def test_xau_guard():
    engine = RiskEngine()
    gov = OPUSGovernor()
    
    # Case 1: Equity $100 (Below threshold)
    account_low = {"equity": 100, "balance": 100, "daily_pnl": 0, "consecutive_losses": 0}
    # Ensure RR > minimum (1.2) so this test focuses on equity guard behavior.
    signal_xau = {
        "symbol": "XAUUSDm",
        "side": "BUY",
        "entry_price": 1950.0,
        "sl": 1900.0,
        "tp1": 2100.0,
    }
    
    # Check Governor status
    status_low = gov.compute_status(account_low)
    print(f"Equity $100: Governor Status - Recommended: {status_low.recommended_symbol}")
    
    # Check Risk Gate
    with patch("MetaTrader5.positions_get", return_value=[]), patch(
        "backend.trader.services.news_filter.news_filter.is_safe", return_value=True
    ):
        result_low = engine.risk_gate(signal_xau, account_low, {"spread": 10}, opus_status=status_low)
    print(f"Equity $100: Risk Gate allowed? {result_low['allowed']}")
    print(f"Reasons: {result_low['reasons']}")
    
    assert result_low['allowed'] is False
    assert any("LOCKED] XAU" in r for r in result_low['reasons'])

    # Case 2: Equity $350 (Above threshold)
    account_high = {"equity": 350, "balance": 350, "daily_pnl": 0, "consecutive_losses": 0}
    
    # Check Governor status
    status_high = gov.compute_status(account_high)
    print(f"\nEquity $350: Governor Status - Recommended: {status_high.recommended_symbol}")
    
    # Check Risk Gate
    with patch("MetaTrader5.positions_get", return_value=[]), patch(
        "backend.trader.services.news_filter.news_filter.is_safe", return_value=True
    ):
        result_high = engine.risk_gate(signal_xau, account_high, {"spread": 10}, opus_status=status_high)
    print(f"Equity $350: Risk Gate allowed? {result_high['allowed']}")
    
    assert result_high['allowed'] is True
    print("Test Passed: XAU Guard working correctly.")

if __name__ == "__main__":
    test_xau_guard()
