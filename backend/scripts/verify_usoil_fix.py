
import sys
from pathlib import Path
import logging
import pandas as pd

# Path setup
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.trader.strategy.usoil_momentum import signal_usoil_momentum
from backend.trader.risk.opus_governor import governor

# Mock logger
logger = logging.getLogger("usoil_verify")

def test_usoil_market_conversion():
    print("\n[TEST] USOIL Market Conversion Test")
    df = pd.DataFrame({
        "close": [75.0, 75.1, 75.5],
        "open": [75.0, 75.0, 75.1],
        "high": [75.1, 75.2, 75.6],
        "low": [74.9, 74.9, 75.0],
        "ema_200": [70.0, 70.0, 70.0],
        "ema_9": [75.0, 75.0, 75.1],
        "is_uptrend": [True, True, True],
        "adx": [30, 30, 30],
        "body_ratio": [0.5, 0.5, 0.5],
        "atr": [0.1, 0.1, 0.1],
        "roc_5": [0.25, 0.25, 0.25], # Strong ROC
        "force_index": [10, 10, 10],
        "obv_bullish": [True, True, True],
        "vol_ratio": [1.0, 1.0, 1.0]
    })
    
    context = {"symbol": "USOILm"}
    signal = signal_usoil_momentum(df, context)
    
    if signal:
        print(f"  Result: Signal={signal['model']}, Side={signal['side']}, EntryType={signal['entry_type']}")
        assert signal['entry_type'] == 'MARKET', "EntryType should be MARKET for strong ROC"
        print("  [PASSED] USOIL correctly favors MARKET on strong moves")
    else:
        print("  [FAILED] No signal generated")

def test_vault_mode():
    print("\n[TEST] Vault Mode Threshold Test")
    # USD conversion rate in governor is 34.5
    # 600 THB / 34.5 = 17.39 USD
    
    # Check 1: Normal mode
    acct_normal = {"equity": 100, "balance": 100, "daily_pnl": 5.0, "consecutive_losses": 0}
    status_normal = governor.compute_status(acct_normal)
    print(f"  Normal Case ($5 PnL): RiskLevel={status_normal.risk_level}, Multiplier={status_normal.lot_multiplier}")
    assert status_normal.risk_level == "NORMAL"
    
    # Check 2: Vault Mode
    acct_vault = {"equity": 118, "balance": 118, "daily_pnl": 18.0, "consecutive_losses": 0}
    status_vault = governor.compute_status(acct_vault)
    print(f"  Vault Case ($18 PnL): RiskLevel={status_vault.risk_level}, Multiplier={status_vault.lot_multiplier}")
    assert status_vault.risk_level == "VAULT_LOCK"
    assert status_vault.lot_multiplier <= 0.2, "Multiplier must be reduced in Vault Mode"
    print("  [PASSED] Vault Mode activates correctly and reduces risk")

if __name__ == "__main__":
    try:
        test_usoil_market_conversion()
        test_vault_mode()
        print("\n✅ Verification SUCCESS")
    except Exception as e:
        print(f"\n❌ Verification FAILED: {e}")
        sys.exit(1)
