import os
import re

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content
    
    # 1. Replace imports
    content = re.sub(r'import pandas_ta as ta\n', 'import app.analysis.indicators as ind\n', content)
    content = re.sub(r'import pandas_ta\n', 'import app.analysis.indicators as ind\n', content)
    
    # 2. Replace basic df.ta calls
    # EMA: df.ta.ema(length=X) -> ind.ema(df['close'], length=X)
    content = re.sub(r'df\.ta\.ema\(length=(\d+)\)', r"ind.ema(df['close'], \1)", content)
    content = re.sub(r'df\.ta\.ema\((\d+)\)', r"ind.ema(df['close'], \1)", content)
    content = re.sub(r'df\["close"\]\.ta\.ema\(length=(\d+)\)', r"ind.ema(df['close'], \1)", content)
    
    # RSI: df.ta.rsi(length=X) -> ind.rsi(df['close'], length=X)
    content = re.sub(r'df\.ta\.rsi\(length=(\d+)\)', r"ind.rsi(df['close'], \1)", content)
    content = re.sub(r'df\.ta\.rsi\((\d+)\)', r"ind.rsi(df['close'], \1)", content)
    
    # ATR: df.ta.atr(length=X) -> ind.atr(df['high'], df['low'], df['close'], length=X)
    content = re.sub(r'df\.ta\.atr\(length=(\d+)\)', r"ind.atr(df['high'], df['low'], df['close'], \1)", content)
    content = re.sub(r'df\.ta\.atr\((\d+)\)', r"ind.atr(df['high'], df['low'], df['close'], \1)", content)
    
    # MACD: df.ta.macd(fast=X, slow=Y, signal=Z)
    content = re.sub(r'df\.ta\.macd\(fast=(\d+),\s*slow=(\d+),\s*signal=(\d+)\)', r"ind.macd(df['close'], \1, \2, \3)", content)
    
    # BBANDS
    content = re.sub(r'df\.ta\.bbands\(length=(\d+),\s*std=(\d+\.?\d*)\)', r"ind.bbands(df['close'], \1, \2)", content)
    
    if content != original_content:
        # Check if we need to add import if it wasn't there but we replaced ta. calls
        if 'import app.analysis.indicators' not in content and 'ind.' in content:
            # Add to top of file after other imports
            import_block = "import pandas as pd\nimport app.analysis.indicators as ind\n"
            content = content.replace("import pandas as pd\n", import_block, 1)
            
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated: {filepath}")

def main():
    start_dir = r"d:\VibeCode\Trade\backend\app"
    for root, dirs, files in os.walk(start_dir):
        for file in files:
            if file.endswith('.py') and file != 'indicators.py':
                process_file(os.path.join(root, file))

if __name__ == "__main__":
    main()
