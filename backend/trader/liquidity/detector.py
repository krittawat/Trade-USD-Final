import pandas as pd
import numpy as np

def detect_liquidity_events(df: pd.DataFrame, config: dict) -> list:
    """
    Detects EQH/EQL pools, sweeps, and re-acceptances.
    Enhanced with volume spike confirmation + Bull/Bear Power alignment.
    """
    events = []
    if len(df) < 50:
        return events
        
    latest_ts = df.index[-1] if not 'time' in df.columns else df['time'].iloc[-1]
    latest = df.iloc[-1]
    
    # Volume & Power context
    has_vol_spike = bool(latest.get('vol_spike', False))
    has_vol_dryup = bool(latest.get('vol_dryup', False))
    vol_ratio = float(latest.get('vol_ratio', 1.0))
    bull_power = float(latest.get('bull_power', 0))
    bear_power = float(latest.get('bear_power', 0))
    net_power = float(latest.get('net_power', 0))

    points_thresh = config.get("eqh_eql_threshold_points", 20)
    lookback = config.get("sweep_lookback_bars", 50)
    
    recent_highs = df['high'].tail(lookback).values
    recent_lows = df['low'].tail(lookback).values
    
    # 1. EQH/EQL Pools
    max_high = np.max(recent_highs)
    highs_near_max = [h for h in recent_highs if max_high - h <= (points_thresh * 0.0001)]
    if len(highs_near_max) >= 2:
        events.append({
            "type": "EQH_POOL",
            "timestamp": str(latest_ts),
            "direction": "SELL_LIQUIDITY",
            "key_levels": [max_high],
            "score": 0.8,
            "invalidation_level": max_high + (points_thresh * 0.0001 * 2)
        })
        
    # 2. Sweep Detection (enhanced with volume)
    #    Volume spike on sweep = high-conviction smart money move
    if latest['high'] > max_high and latest['close'] < max_high:
        base_score = 0.85
        # Volume spike boosts score
        if has_vol_spike:
            base_score += 0.10
        # Bear power alignment (sellers stepping in after sweep)
        if bear_power < 0 and net_power < 0:
            base_score += 0.05
        events.append({
            "type": "SWEEP",
            "timestamp": str(latest_ts),
            "direction": "SHORT_SIGNAL",
            "key_levels": [max_high],
            "score": min(1.0, base_score),
            "invalidation_level": latest['high'],
            "vol_confirmed": has_vol_spike,
        })
        
    min_low = np.min(recent_lows)
    if latest['low'] < min_low and latest['close'] > min_low:
        base_score = 0.85
        if has_vol_spike:
            base_score += 0.10
        if bull_power > 0 and net_power > 0:
            base_score += 0.05
        events.append({
            "type": "SWEEP",
            "timestamp": str(latest_ts),
            "direction": "LONG_SIGNAL",
            "key_levels": [min_low],
            "score": min(1.0, base_score),
            "invalidation_level": latest['low'],
            "vol_confirmed": has_vol_spike,
        })

    # 3. Displacement + Volume = maximum conviction
    if latest.get('displacement_down', False) and 'SWEEP' in [e['type'] for e in events if e.get('direction') == 'SHORT_SIGNAL']:
        score = 0.95 + (0.05 if has_vol_spike else 0)
        events.append({
            "type": "DISPLACEMENT_CONFIRMED",
            "timestamp": str(latest_ts),
            "direction": "SHORT_SIGNAL",
            "key_levels": [latest['close']],
            "score": min(1.0, score),
            "invalidation_level": latest['high'],
            "vol_confirmed": has_vol_spike,
        })
        
    if latest.get('displacement_up', False) and 'SWEEP' in [e['type'] for e in events if e.get('direction') == 'LONG_SIGNAL']:
        score = 0.95 + (0.05 if has_vol_spike else 0)
        events.append({
            "type": "DISPLACEMENT_CONFIRMED",
            "timestamp": str(latest_ts),
            "direction": "LONG_SIGNAL",
            "key_levels": [latest['close']],
            "score": min(1.0, score),
            "invalidation_level": latest['low'],
            "vol_confirmed": has_vol_spike,
        })

    # 4. Volume Dry-Up Alert (pre-breakout compression signal)
    if has_vol_dryup and latest.get('compression_ratio', 1) < 0.6:
        events.append({
            "type": "VOL_DRYUP_COMPRESSION",
            "timestamp": str(latest_ts),
            "direction": "NEUTRAL",
            "key_levels": [latest['close']],
            "score": 0.6,
            "invalidation_level": 0,
        })
        
    return events
