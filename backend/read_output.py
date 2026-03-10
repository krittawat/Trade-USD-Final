with open('backtest_output.txt', 'r', encoding='utf-16le', errors='replace') as f:
    text = f.read()
lines = text.split('\n')
for line in lines:
    if "SUMMARY:" in line or "XAUUSDc" in line or "BTCUSDc" in line or "XAGUSDc" in line or "TOTAL" in line or "===" in line or "---" in line or "Trades:" in line or "Strategy:" in line:
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode('ascii', 'ignore').decode('ascii'))
