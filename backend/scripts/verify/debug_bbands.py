
import pandas as pd
import pandas_ta as ta
import numpy as np

def debug_bbands():
    df = pd.DataFrame({
        "close": np.random.normal(100, 10, 100)
    })
    
    bb = ta.bbands(df["close"], length=20, std=2.0)
    print("Columns returned by ta.bbands:")
    print(bb.columns.tolist())
    
    if "BBL_20_2.0" in bb.columns:
        print("✅ Access key 'BBL_20_2.0' is VALID.")
    else:
        print("❌ Access key 'BBL_20_2.0' is INVALID.")

if __name__ == "__main__":
    debug_bbands()
