# -*- coding: utf-8 -*-
"""
AntiChop Filter — Market Quality Pre-Filter
ตัวกรองสภาวะตลาด Sideway/Chop ก่อนเปิดเทรด

ported จาก D:\\Trade\\gold-risk-engine\\backend\\app\\strategy\\antichop.py

FILTERS:
1. ATR Compression — current ATR < 75% of rolling avg → choppy
2. EMA 50 Slope — flat slope → no directional momentum
3. ADX < 22 — weak trend intensity
4. Price Range Congestion — tight range last 10 bars → congestion
"""
import numpy as np
import pandas as pd
import logging

logger = logging.getLogger("antichop_filter")

# Tunable thresholds
ATR_COMPRESSION_RATIO = 0.75
MIN_EMA_SLOPE_PCT = 0.00004  # Price-relative slope (works for all instruments)
MIN_ADX = 18
RANGE_LOOKBACK = 10
CONGESTION_PCT = 0.001  # 0.1% range = too tight


def is_market_choppy(df: pd.DataFrame) -> tuple:
    """
    Returns (is_choppy: bool, reason: str).
    Call BEFORE any strategy to reject chop conditions.
    """
    if len(df) < 60:
        return False, ""  # Not enough data to judge

    latest = df.iloc[-1]
    close = float(latest['close'])

    # 1. ATR Compression
    if 'atr' in df.columns:
        current_atr = float(latest.get('atr', 0))
        avg_atr = df['atr'].rolling(window=30).mean().iloc[-1]
        if avg_atr > 0 and current_atr < (avg_atr * ATR_COMPRESSION_RATIO):
            return True, f"Low Volatility (ATR {current_atr:.4f} < {avg_atr * ATR_COMPRESSION_RATIO:.4f})"

    # 2. EMA Slope (EMA 50)
    if 'ema_slow' in df.columns and len(df) >= 10:
        ema_series = df['ema_slow']
        slope = (float(ema_series.iloc[-1]) - float(ema_series.iloc[-10])) / 10
        # V2: Price-relative slope — works for Gold $3700, Silver $30, BTC $90000
        slope_pct = abs(slope) / close if close > 0 else 0
        if slope_pct < MIN_EMA_SLOPE_PCT:
            return True, f"Flat Trend (EMA Slope {slope:.6f}, PCT={slope_pct:.6f})"

    # 3. ADX Chop Filter
    if 'adx' in df.columns:
        adx_val = float(latest.get('adx', 0))
        if not np.isnan(adx_val) and adx_val < MIN_ADX:
            return True, f"Weak Trend Intensity (ADX {adx_val:.1f})"

    # 4. Price Range Congestion
    if len(df) >= RANGE_LOOKBACK:
        last_n = df.tail(RANGE_LOOKBACK)
        range_high = float(last_n['high'].max())
        range_low = float(last_n['low'].min())
        if close > 0:
            range_pct = (range_high - range_low) / close
            if range_pct < CONGESTION_PCT:
                return True, f"Price Congestion (Range {range_pct:.4%})"

    return False, ""
