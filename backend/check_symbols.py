"""Check available forex symbols on MT5."""
import MetaTrader5 as mt5

mt5.initialize(r"C:\Program Files\MetaTrader 5\terminal64.exe")

# Common forex pairs to check
pairs = [
    "EURUSDc", "EURUSD",
    "GBPUSDc", "GBPUSD",
    "USDJPYc", "USDJPY",
    "AUDUSDc", "AUDUSD",
    "USDCHFc", "USDCHF",
    "USDCADc", "USDCAD",
    "NZDUSDc", "NZDUSD",
    "EURGBPc", "EURGBP",
    "EURJPYc", "EURJPY",
    "GBPJPYc", "GBPJPY",
    "XAUUSDc", "XAUUSD",
    "BTCUSDc", "BTCUSD",
]

print("Available forex symbols on this account:")
print(f"{'Symbol':15s} {'Spread':>8s} {'Min Lot':>8s} {'Trade':>6s}")
print("-" * 45)

for sym in pairs:
    info = mt5.symbol_info(sym)
    if info and info.visible:
        spread = info.spread
        min_lot = info.volume_min
        trade = "YES" if info.trade_mode == mt5.SYMBOL_TRADE_MODE_FULL else "NO"
        print(f"{sym:15s} {spread:8d} {min_lot:8.2f} {trade:>6s}")

mt5.shutdown()
