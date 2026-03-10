import pandas as pd
import MetaTrader5 as mt5
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

sys.path.insert(0, './backend')
import app.analysis.indicators as ind
import pandas_ta as ta

mt5.initialize()
symbol = 'XAUUSDc'
end_date = datetime.now(timezone.utc)
start_date = end_date - timedelta(days=365)
rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, start_date, end_date)
df = pd.DataFrame(rates)
df['time'] = pd.to_datetime(df['time'], unit='s')

p = {'ma': 12, 'saf': 0.02, 'smax': 0.2, 'rsi': 14, 'macd_f': 12, 'macd_s': 26, 'macd_sig': 9}

df['ma'] = ind.ema(df['close'], length=p['ma'])
sar = ta.psar(df['high'], df['low'], df['close'], af=p['saf'], af0=p['saf'], max_af=p['smax'])
psar_cols = [c for c in sar.columns if c.startswith('PSAR_')]
df['psar'] = sar[psar_cols[0]]
df['rsi'] = ind.rsi(df['close'], length=p['rsi'])
macd_res = ind.macd(df['close'], fast=p['macd_f'], slow=p['macd_s'], signal=p['macd_sig'])
df['macd_line'] = macd_res[f"MACD_{p['macd_f']}_{p['macd_s']}_{p['macd_sig']}"]
df['macd_sig'] = macd_res[f"MACDs_{p['macd_f']}_{p['macd_s']}_{p['macd_sig']}"]

df = df.dropna().reset_index(drop=True)

buy_cond = (df['close'] > df['ma']) & (df['psar'] < df['close']) & (df['macd_line'] > df['macd_sig']) & (df['rsi'] <= 85)
sell_cond = (df['close'] < df['ma']) & (df['psar'] > df['close']) & (df['macd_line'] < df['macd_sig']) & (df['rsi'] >= 15)

df['signal'] = 0
df.loc[buy_cond, 'signal'] = 1
df.loc[sell_cond, 'signal'] = -1

print("Total DataFrame rows: ", len(df))
print('Buys:', (df['signal'] == 1).sum())
print('Sells:', (df['signal'] == -1).sum())
print('PSAR Sample:')
print(df[['close', 'ma', 'psar', 'macd_line', 'macd_sig', 'rsi']].tail(10))
