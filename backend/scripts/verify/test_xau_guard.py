import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from trader.risk.gate import RiskEngine
from trader.risk.opus_governor import OPUSGovernor, OPUSStatus

def test_xau_guard():
    engine = RiskEngine()
    gov = OPUSGovernor()
    
    # Case 1: Equity $100 (Below threshold)
    account_low = {"equity": 100, "balance": 100, "daily_pnl": 0, "consecutive_losses": 0}
    signal_xau = {"symbol": "XAUUSDm", "side": "BUY", "sl": 1900.0, "tp1": 2000.0}
    
    # Check Governor status
    status_low = gov.compute_status(account_low)
    print(f"Equity $100: Governor Status - Recommended: {status_low.recommended_symbol}")
    
    # Check Risk Gate
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
    result_high = engine.risk_gate(signal_xau, account_high, {"spread": 10}, opus_status=status_high)
    print(f"Equity $350: Risk Gate allowed? {result_high['allowed']}")
    
    assert result_high['allowed'] is True
    print("Test Passed: XAU Guard working correctly.")

if __name__ == "__main__":
    test_xau_guard()
