import pandas as pd
import numpy as np
from app.analysis.structure import detect_structure

def test_structure_detection():
    # สร้างข้อมูลจำลอง (Synthetic Data)
    dates = pd.date_range(start="2026-01-01", periods=100, freq="h")
    
    # Uptrend with a Bullish FVG
    data = {
        'open': np.linspace(100, 110, 100),
        'high': np.linspace(101, 111, 100),
        'low': np.linspace(99, 109, 100),
        'close': np.linspace(100.5, 110.5, 100)
    }
    df = pd.DataFrame(data, index=dates)
    
    # Insert a Bullish FVG at the end
    # Candle i-2: High 108
    # Candle i: Low 109
    df.iloc[-3, df.columns.get_loc('high')] = 108.0
    df.iloc[-1, df.columns.get_loc('low')] = 109.0
    
    # Insert a Hammer (Pin Bar Bull) at the end
    df.iloc[-1, df.columns.get_loc('open')] = 110.0
    df.iloc[-1, df.columns.get_loc('close')] = 110.2
    df.iloc[-1, df.columns.get_loc('low')] = 109.0
    df.iloc[-1, df.columns.get_loc('high')] = 110.3
    
    result = detect_structure(df)
    
    print("--- Market Structure Test Results ---")
    print(f"Trend: {result['trend']}")
    print(f"FVG Bull Detected: {len(result['fvg_bull']) > 0}")
    print(f"BOS Bull Detected: {result['bos_bull']}")
    print(f"Patterns: {result['patterns']}")
    
    if len(result['fvg_bull']) > 0:
        print(f"Successfully detected FVG: {result['fvg_bull'][0]}")
    
    if result['patterns']['pin_bull']:
        print("Successfully detected Bullish Pin Bar (Hammer)!")

if __name__ == "__main__":
    test_structure_detection()
