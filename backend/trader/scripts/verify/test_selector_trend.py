import sys
import os
import pandas as pd
from unittest.mock import MagicMock, patch

# Add project root to path
sys.path.append('d:/VibeCode/Trade')
sys.path.append('d:/VibeCode/Trade/backend')

# Mock everything before importing selector
with patch('json.load') as mock_json:
    mock_json.return_value = {"strategy": {"enable_ai_brain": True, "enable_usoil_elite": True}}
    import backend.trader.strategy.selector as selector
    from backend.trader.strategy.selector import select_and_generate_signal

def test_selector_trend_gate():
    print("🧪 Testing Global Selector Trend Gate (Phase 2.5)...")
    
    df = pd.DataFrame({
        'close': [100.0] * 100,
        'high': [101.0] * 100,
        'low': [99.0] * 100,
        'ema_200': [100.0] * 100,
        'vol_ratio': [1.0] * 100
    })
    
    # Mock a candidate signal (BUY)
    mock_buy_signal = {
        'symbol': 'USOIL', 'side': 'BUY', 'entry_price': 100.0, 'sl': 99.0, 'tp1': 102.0,
        'confidence': 0.8, 'model': 'USOIL_ELITE', 'rationale': []
    }
    
    # Mock a candidate signal (SELL)
    mock_sell_signal = {
        'symbol': 'USOIL', 'side': 'SELL', 'entry_price': 100.0, 'sl': 101.0, 'tp1': 98.0,
        'confidence': 0.8, 'model': 'AI_BRAIN', 'rationale': []
    }
    
    # Strategies to mock
    signal_funcs = [
        'signal_trend_killer', 'signal_sniper_pro', 'signal_predicta_v4', 'signal_easy_trend',
        'signal_counter_trend', 'signal_smc_metals', 'signal_btc_whale', 'signal_fvg_logic',
        'signal_momentum_rider', 'signal_antigravity_alpha', 'signal_antigravity_alpha_v6',
        'signal_alpha_v6_smc', 'signal_micro_scalper', 'signal_gold_elite', 'signal_usoil_momentum',
        'signal_momentum_scalper_v2', 'signal_btc_mean_rev', 'signal_btc_stop_hunt_v2',
        'signal_btc_elite_v2', 'signal_btc_oracle', 'signal_aether_flow', 'signal_indices_ultimate',
        'signal_liquidity_hunter', 'signal_correlation_sniper', 'signal_usoil_elite', 'signal_ai_brain'
    ]
    
    # Apply patches
    patches = []
    for func in signal_funcs:
        p = patch.object(selector, func, return_value=None)
        patches.append(p)
        p.start()
        
    # Additional dependencies
    p_chop = patch('backend.trader.strategy.selector.is_market_choppy', return_value=(False, ""))
    p_quality = patch('backend.trader.brain.quality_filter.quality_filter.is_quality_signal', return_value=True)
    p_prob = patch('backend.trader.strategy.selector.calculate_trade_probability', return_value=80.0)
    p_pattern = patch('backend.trader.strategy.selector.analyze_patterns', return_value={'bullish_qml': False, 'bearish_qml': False})
    
    for p in [p_chop, p_quality, p_prob, p_pattern]:
        p.start()
        patches.append(p)

    try:
        # Scenario: HTF is BEARISH, but candidate is BUY
        selector.signal_usoil_elite.return_value = mock_buy_signal
        context_bear = {'symbol': 'USOIL', 'timeframe': 'M5', 'htf_ema_align': 'BEARISH'}
        
        # Test 1.1: Blocked when flag is False
        with patch.dict(selector._STRATEGY_CFG, {"allow_counter_trend_critical": False}):
            result = select_and_generate_signal(df, context_bear, events=[], current_bar=100)
            print(f"HTF Bearish + Buy Signal (Override: OFF) -> Result: {result.get('model') if result else 'Blocked ✅'}")
            assert result is None
        
        # Test 1.2: Allowed when flag is True
        with patch.dict(selector._STRATEGY_CFG, {"allow_counter_trend_critical": True}):
            result_ok = select_and_generate_signal(df, context_bear, events=[], current_bar=101)
            print(f"HTF Bearish + Buy Signal (Override: ON) -> Result: {result_ok.get('model') if result_ok else 'Blocked'} (Should Pass ✅)")
            assert result_ok is not None
            assert result_ok['side'] == 'BUY'
            
        print("✅ Global Selector Trend Gate Tests PASSED!")
    finally:
        for p in patches:
            p.stop()

if __name__ == "__main__":
    test_selector_trend_gate()
