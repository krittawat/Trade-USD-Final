import MetaTrader5 as mt5
import pandas as pd
import numpy as np

if not mt5.initialize():
    print(f"MT5 initialize failed")
    quit()

target = "USOILm"
positions = mt5.positions_get(symbol=target)

if not positions:
    print(f"No open positions for {target}")
    mt5.shutdown()
    quit()

info = mt5.symbol_info(target)
if info is None:
    print(f"Symbol {target} not found")
    mt5.shutdown()
    quit()

point = info.point
contract_size = info.trade_contract_size
bid = info.bid

total_risk_usd = 0
total_reward_usd = 0
total_profit_usd = 0
rows = []

for p in positions:
    if p.type == 1: # SELL
        risk_dist = (p.sl - p.price_open) if p.sl > 0 else 0
        reward_dist = (p.price_open - p.tp) if p.tp > 0 else 0
    else: # BUY
        risk_dist = (p.price_open - p.sl) if p.sl > 0 else 0
        reward_dist = (p.tp - p.price_open) if p.tp > 0 else 0

    risk_usd = p.volume * contract_size * risk_dist if risk_dist > 0 else 0
    reward_usd = p.volume * contract_size * reward_dist if reward_dist > 0 else 0
    
    total_risk_usd += risk_usd
    total_reward_usd += reward_usd
    total_profit_usd += p.profit

    rows.append(f"T:{p.ticket} | {'SELL' if p.type==1 else 'BUY'} | L:{p.volume} | SL:{p.sl} | Rsk:${round(risk_usd,1)} | Rew:${round(reward_usd,1)} | P/L:${round(p.profit,1)}")

print(f"\n--- {target} Analysis ---")
for r in rows: print(r)
print(f"--------------------------")
print(f"TOTAL RISK:   ${round(total_risk_usd, 2)}")
print(f"TOTAL REWARD: ${round(total_reward_usd, 2)}")
print(f"CURRENT P/L:  ${round(total_profit_usd, 2)}")
print(f"PORTFOLIO RR: {round(total_reward_usd/total_risk_usd, 2) if total_risk_usd > 0 else 'N/A'}")

rates = mt5.copy_rates_from_pos(target, mt5.TIMEFRAME_H1, 0, 14)
if rates is not None:
    df_atr = pd.DataFrame(rates)
    tr = np.maximum(df_atr['high'] - df_atr['low'], np.maximum(abs(df_atr['high'] - df_atr['close'].shift(1)), abs(df_atr['low'] - df_atr['close'].shift(1))))
    atr = tr.mean()
    print(f"ATR(H1): {round(atr, 3)}")
    print(f"REC SL (SELL): {round(bid + 1.5*atr, 3)}")

mt5.shutdown()
