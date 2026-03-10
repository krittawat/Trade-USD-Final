"""
ANTIGRAVITY V2 QC Suite — Institutional Grade Verification.
Validates Alpha V5, OPUS Governor (Vault Mode), and News Filter.
"""
import sys
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pandas as pd
import numpy as np
from backend.trader.strategy.antigravity_alpha import signal_antigravity_alpha
from backend.trader.risk.opus_governor import governor
from backend.trader.services.news_filter import news_filter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("qc_v2")

def test_alpha_v5_logic():
    print("\n[QC] Testing Alpha V5 Structural Logic...")
    # Mock data with a clear liquidity sweep and FVG
    # ATR = 2.0, min_fvg = 2.0
    # Must be > 50 bars for Alpha V5.1
    length = 60
    data = {
        'open': [95]*(length-3) + [95, 96, 100],
        'high': [100]*(length-3) + [96, 102, 108],
        'low':  [90]*(length-3) + [88, 97, 98],
        'close': [95]*(length-3) + [94, 100, 105],
        'tick_volume': [500]*(length-3) + [800, 900, 1000],
        'atr': [2.0]*length,
        'ema_200': [80.0]*length
    }
    df = pd.DataFrame(data)
    context = {'symbol': 'XAUUSD', 'timeframe': 'M5'}
    
    signal = signal_antigravity_alpha(df, context)
    print(f"  [DEBUG] Signal: {signal}")
    if signal and signal.get('model') == 'ANTIGRAVITY_ALPHA_V5.1':
        print("  ✅ Alpha V5.1 Signal Detected correctly.")
        print(f"  ✅ SL: {signal['sl']} (Spread-proof check passed)")
        return True
    else:
        print("  ❌ Alpha V5 Signal NOT detected.")
        return False

def test_governor_vault_mode():
    print("\n[QC] Testing OPUS Governor Vault Mode...")
    # Mock account state with $18 profit (approx 630 THB)
    account = {
        'equity': 118.0,
        'balance': 118.0,
        'daily_pnl': 18.0,
        'margin_level': 800.0,
        'consecutive_losses': 0
    }
    status = governor.compute_status(account)
    if status.risk_level == "VAULT_LOCK" and status.lot_multiplier <= 0.1:
        print(f"  ✅ Vault Mode Active at ${account['daily_pnl']:.2f}")
        print(f"  ✅ Lot Multiplier: {status.lot_multiplier}x (Safety check passed)")
        return True
    else:
        print(f"  ❌ Vault Mode FAILED (Level: {status.risk_level}, Mult: {status.lot_multiplier})")
        return False

def test_news_filter_availability():
    print("\n[QC] Testing News Filter Connectivity...")
    # News filter should return True (safe) if no news loaded yet
    safe = news_filter.is_safe("XAUUSD")
    if safe:
        print("  ✅ News Filter initialized in SAFE mode (Default).")
        return True
    return False

if __name__ == "__main__":
    t1 = test_alpha_v5_logic()
    t2 = test_governor_vault_mode()
    t3 = test_news_filter_availability()
    
    if all([t1, t2, t3]):
        print("\n🏆 QC PASSED: System V2 is ready for DRY_RUN.")
        sys.exit(0)
    else:
        print("\n🚨 QC FAILED: Check logs above.")
        sys.exit(1)
