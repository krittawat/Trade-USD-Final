import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
from backend.trader.data.mapper import mapper
print(f'XAUUSD -> {mapper.to_broker("XAUUSD")}')
print(f'BTCUSD -> {mapper.to_broker("BTCUSD")}')
print(f'USOIL -> {mapper.to_broker("USOIL")}')
