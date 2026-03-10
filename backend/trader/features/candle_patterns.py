# -*- coding: utf-8 -*-
"""
OPUS Candlestick Pattern Detector
Detects key reversal and continuation patterns for trend-following:
- Engulfing (Bullish/Bearish)
- Pin Bar (Hammer/Shooting Star)
- Three Soldiers / Three Crows
- Morning Star / Evening Star
- Doji at key levels
"""
import pandas as pd
import numpy as np


def detect_candle_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """Master function: adds pattern columns to DataFrame."""
    if len(df) < 3:
        return df

    df = _detect_engulfing(df)
    df = _detect_pin_bar(df)
    df = _detect_three_soldiers_crows(df)
    df = _detect_morning_evening_star(df)
    return df


def _detect_engulfing(df: pd.DataFrame) -> pd.DataFrame:
    """
    Bullish Engulfing: prev bearish + current bullish body fully covers prev body
    Bearish Engulfing: prev bullish + current bearish body fully covers prev body
    """
    df['bullish_engulfing'] = False
    df['bearish_engulfing'] = False

    for i in range(1, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i - 1]

        curr_body = curr['close'] - curr['open']
        prev_body = prev['close'] - prev['open']

        # Bullish Engulfing
        if (prev_body < 0 and curr_body > 0 and
            curr['open'] <= prev['close'] and
            curr['close'] >= prev['open'] and
            abs(curr_body) > abs(prev_body)):
            df.iloc[i, df.columns.get_loc('bullish_engulfing')] = True

        # Bearish Engulfing
        if (prev_body > 0 and curr_body < 0 and
            curr['open'] >= prev['close'] and
            curr['close'] <= prev['open'] and
            abs(curr_body) > abs(prev_body)):
            df.iloc[i, df.columns.get_loc('bearish_engulfing')] = True

    return df


def _detect_pin_bar(df: pd.DataFrame) -> pd.DataFrame:
    """
    Hammer (bullish pin bar): small body at top, long lower wick (>2x body)
    Shooting Star (bearish pin bar): small body at bottom, long upper wick (>2x body)
    """
    df['hammer'] = False
    df['shooting_star'] = False

    for i in range(len(df)):
        row = df.iloc[i]
        body = abs(row['close'] - row['open'])
        upper_wick = row['high'] - max(row['open'], row['close'])
        lower_wick = min(row['open'], row['close']) - row['low']
        total_range = row['high'] - row['low']

        if total_range == 0 or body == 0:
            continue

        # Hammer: lower wick > 2x body, upper wick < body
        if lower_wick > body * 2 and upper_wick < body * 0.5:
            df.iloc[i, df.columns.get_loc('hammer')] = True

        # Shooting Star: upper wick > 2x body, lower wick < body
        if upper_wick > body * 2 and lower_wick < body * 0.5:
            df.iloc[i, df.columns.get_loc('shooting_star')] = True

    return df


def _detect_three_soldiers_crows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Three White Soldiers: 3 consecutive bullish candles, each closing higher
    Three Black Crows: 3 consecutive bearish candles, each closing lower
    """
    df['three_soldiers'] = False
    df['three_crows'] = False

    for i in range(2, len(df)):
        c0, c1, c2 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]

        # Three Soldiers
        if (c0['close'] > c0['open'] and
            c1['close'] > c1['open'] and
            c2['close'] > c2['open'] and
            c1['close'] > c0['close'] and
            c2['close'] > c1['close']):
            df.iloc[i, df.columns.get_loc('three_soldiers')] = True

        # Three Crows
        if (c0['close'] < c0['open'] and
            c1['close'] < c1['open'] and
            c2['close'] < c2['open'] and
            c1['close'] < c0['close'] and
            c2['close'] < c1['close']):
            df.iloc[i, df.columns.get_loc('three_crows')] = True

    return df


def _detect_morning_evening_star(df: pd.DataFrame) -> pd.DataFrame:
    """
    Morning Star (bullish reversal): bearish candle → small body → bullish candle
    Evening Star (bearish reversal): bullish candle → small body → bearish candle
    """
    df['morning_star'] = False
    df['evening_star'] = False

    for i in range(2, len(df)):
        c0, c1, c2 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]

        body0 = abs(c0['close'] - c0['open'])
        body1 = abs(c1['close'] - c1['open'])
        body2 = abs(c2['close'] - c2['open'])

        avg_body = (body0 + body2) / 2
        if avg_body == 0:
            continue

        # Morning Star
        if (c0['close'] < c0['open'] and      # bearish
            body1 < body0 * 0.3 and            # small middle
            c2['close'] > c2['open'] and       # bullish
            c2['close'] > (c0['open'] + c0['close']) / 2):  # closes above midpoint
            df.iloc[i, df.columns.get_loc('morning_star')] = True

        # Evening Star
        if (c0['close'] > c0['open'] and
            body1 < body0 * 0.3 and
            c2['close'] < c2['open'] and
            c2['close'] < (c0['open'] + c0['close']) / 2):
            df.iloc[i, df.columns.get_loc('evening_star')] = True

    return df


def get_pattern_signal(latest: pd.Series) -> dict:
    """
    Summarize detected patterns on the latest bar.
    Returns: {"direction": "BUY"|"SELL"|None, "patterns": [...], "strength": 0-1}
    """
    buy_patterns = []
    sell_patterns = []

    if latest.get('bullish_engulfing', False):
        buy_patterns.append("Bullish Engulfing")
    if latest.get('hammer', False):
        buy_patterns.append("Hammer")
    if latest.get('three_soldiers', False):
        buy_patterns.append("Three Soldiers")
    if latest.get('morning_star', False):
        buy_patterns.append("Morning Star")

    if latest.get('bearish_engulfing', False):
        sell_patterns.append("Bearish Engulfing")
    if latest.get('shooting_star', False):
        sell_patterns.append("Shooting Star")
    if latest.get('three_crows', False):
        sell_patterns.append("Three Crows")
    if latest.get('evening_star', False):
        sell_patterns.append("Evening Star")

    if buy_patterns and not sell_patterns:
        return {"direction": "BUY", "patterns": buy_patterns, "strength": min(1.0, len(buy_patterns) * 0.3)}
    elif sell_patterns and not buy_patterns:
        return {"direction": "SELL", "patterns": sell_patterns, "strength": min(1.0, len(sell_patterns) * 0.3)}
    else:
        return {"direction": None, "patterns": [], "strength": 0}
