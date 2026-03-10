import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np
from backend.trader.strategy.usoil_momentum import signal_usoil_momentum

def test_htf_filter():
    # Create mock data (50+ bars)
    df = pd.DataFrame({
        'close': [100.0] * 60,
        'open': [100.0] * 60,
        'high': [101.0] * 60,
        'low': [99.0] * 60,
        'ema_200': [90.0] * 60,
        'ema_9': [95.0] * 60,
        'plus_di': [30.0] * 60,
        'minus_di': [10.0] * 60,
        'adx': [30.0] * 60,
        'is_uptrend': [True] * 60,
        'is_downtrend': [False] * 60,
        'vol_ratio': [1.0] * 60,
        'body_ratio': [0.5] * 60,
        'atr': [1.0] * 60,
        'roc_5': [0.5] * 60,
        'force_index': [1.0] * 60,
        'obv_bullish': [True] * 60
    })

    # Test Case 1: BULLISH signal with BULLISH HTF (Should ALLOW)
    context_ok = {'symbol': 'USOILm', 'htf_ema_align': 'BULLISH'}
    sig_ok = signal_usoil_momentum(df, context_ok)
    print(f"Test 1 (Bull/Bull): {'PASS' if sig_ok and sig_ok['side'] == 'BUY' else 'FAIL'}")

    # Test Case 2: BULLISH signal with BEARISH HTF (Should BLOCK)
    context_block = {'symbol': 'USOILm', 'htf_ema_align': 'BEARISH'}
    sig_block = signal_usoil_momentum(df, context_block)
    print(f"Test 2 (Bull/Bear): {'PASS' if sig_block is None else 'FAIL'}")

    # Create BEARISH mock data
    df_bear = df.copy()
    df_bear['ema_200'] = 110.0
    df_bear['ema_9'] = 105.0 # close is 100, so close < ema_fast (95 in prev mock was wrong)
    df_bear['is_uptrend'] = False
    df_bear['is_downtrend'] = True
    df_bear['plus_di'] = 10.0
    df_bear['minus_di'] = 30.0
    df_bear['roc_5'] = -0.5
    df_bear['force_index'] = -1.0
    df_bear['obv_bullish'] = False

    # Test Case 3: BEARISH signal with BEARISH HTF (Should ALLOW)
    context_ok_bear = {'symbol': 'USOILm', 'htf_ema_align': 'BEARISH'}
    sig_ok_bear = signal_usoil_momentum(df_bear, context_ok_bear)
    print(f"Test 3 (Bear/Bear): {'PASS' if sig_ok_bear and sig_ok_bear['side'] == 'SELL' else 'FAIL'}")

    # Test Case 4: BEARISH signal with BULLISH HTF (Should BLOCK)
    context_block_bear = {'symbol': 'USOILm', 'htf_ema_align': 'BULLISH'}
    sig_block_bear = signal_usoil_momentum(df_bear, context_block_bear)
    print(f"Test 4 (Bear/Bull): {'PASS' if sig_block_bear is None else 'FAIL'}")
    
    # Test Case 5: Pullback Logic - Price > EMA 9 (Should be LIMIT)
    df_pullback = df.copy()
    df_pullback['ema_9'] = 98.0 # Price is 100, so close > ema_9
    sig_limit = signal_usoil_momentum(df_pullback, context_ok)
    print(f"Test 5 (Wait for Pullback): {'PASS' if sig_limit and sig_limit['entry_type'] == 'LIMIT' else 'FAIL'}")
    
    # Test Case 6: Pullback Logic - Price <= EMA 9 (Should be MARKET)
    df_touch = df.copy()
    df_touch['ema_9'] = 100.0 # Price is 100, exactly at EMA
    sig_market = signal_usoil_momentum(df_touch, context_ok)
    print(f"Test 6 (At Pullback Level): {'PASS' if sig_market and sig_market['entry_type'] == 'MARKET' else 'FAIL'}")

if __name__ == "__main__":
    test_htf_filter()
