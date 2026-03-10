import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import get_settings
from app.mt5.client import MT5Client

def main():
    settings = get_settings()
    client = MT5Client(settings)
    if not client.connect():
        print('Failed to connect to MT5')
        return
        
    symbols = ['XAUUSDc', 'XAGUSDc', 'BTCUSDc', 'USDJPYc']
    for sym in symbols:
        print(f'\n--- Fetching Profile for {sym} ---')
        profile = client.get_symbol_info(sym)
        if profile:
            print(f'Success: {sym}')
            print(f'Contract Size: {profile.contract_size}')
            print(f'Tick Size: {profile.point}')
            print(f'Volume Step: {profile.volume_step}')
            print(f'Min Volume: {profile.volume_min}')
            print(f'Spread Avg: {profile.spread_avg}')
            print(f'Digits: {profile.digits}')
        else:
            print(f'Failed to load profile for {sym}')
            
    client.disconnect()

if __name__ == '__main__':
    main()
