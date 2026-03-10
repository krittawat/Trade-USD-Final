import sys
import os
import pandas as pd
import numpy as np
from unittest.mock import MagicMock

# Add project root to path
sys.path.append('d:/VibeCode/Trade')

from backend.trader.strategy.ai_brain_strategy import signal_ai_brain

def test_trend_alignment():
    print("🧪 Testing ALPHA_V5_AI Trend Alignment Logic...")
    
    # Create a dummy dataframe with enough data for indicators
    df = pd.DataFrame({
        'close': [100.0] * 100,
        'open': [100.0] * 100,
        'high': [101.0] * 100,
        'low': [99.0] * 100,
        'pd_zone': ['DISCOUNT'] * 100,
        'fvg_bull': [1] * 100,
        'fvg_bear': [0] * 100,
        'ob_bull': [1] * 100,
        'ob_bear': [0] * 100
    })
    
    # Mock the brain predictor
    import backend.trader.strategy.ai_brain_strategy as ai_strat
    original_get_brain = ai_strat.get_brain
    
    mock_brain = MagicMock()
    # Mock predict_next_move to return BUY with high confidence
    mock_brain.predict_next_move.return_value = ("BUY", 0.9)
    ai_strat.get_brain = lambda symbol: mock_brain
    
    try:
        # Case 1: BUY signal + HTF BULLISH (Should PASS)
        context_bull = {'symbol': 'TEST', 'htf_ema_align': 'BULLISH'}
        signal_bull = signal_ai_brain(df, context_bull)
        print(f"BULL Signal: {signal_bull['side'] if signal_bull else 'None'}")
        assert signal_bull is not None
        assert signal_bull['side'] == 'BUY'
        assert 'HTF: BULLISH' in str(signal_bull['rationale'])
        
        # Case 2: BUY signal + HTF BEARISH (Should be BLOCKED)
        context_bear = {'symbol': 'TEST', 'htf_ema_align': 'BEARISH'}
        signal_bear = signal_ai_brain(df, context_bear)
        print(f"BEAR Signal with BUY intention: {signal_bear['side'] if signal_bear else 'None (Blocked)'}")
        assert signal_bear is None
        
        # Case 3: SELL intention + HTF BULLISH (Should be BLOCKED)
        mock_brain.predict_next_move.return_value = ("SELL", 0.9)
        # Setup df for sell
        df['pd_zone'] = 'PREMIUM'
        df['fvg_bull'] = 0
        df['fvg_bear'] = 1
        df['ob_bull'] = 0
        df['ob_bear'] = 1
        
        signal_sell_blocked = signal_ai_brain(df, context_bull)
        print(f"BULL Signal with SELL intention: {signal_sell_blocked['side'] if signal_sell_blocked else 'None (Blocked)'}")
        assert signal_sell_blocked is None
        
        # Case 4: SELL intention + HTF BEARISH (Should PASS)
        signal_sell_pass = signal_ai_brain(df, context_bear)
        print(f"BEAR Signal with SELL intention: {signal_sell_pass['side'] if signal_sell_pass else 'None'}")
        assert signal_sell_pass is not None
        assert signal_sell_pass['side'] == 'SELL'
        assert 'HTF: BEARISH' in str(signal_sell_pass['rationale'])

        print("✅ All Trend Alignment Tests PASSED!")
        
    finally:
        # Restore original function
        ai_strat.get_brain = original_get_brain

if __name__ == "__main__":
    test_trend_alignment()
