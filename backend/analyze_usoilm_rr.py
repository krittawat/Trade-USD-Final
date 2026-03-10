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

# Get Symbol Info for calculations
info = mt5.symbol_info(target)
if info is None:
    print(f"Symbol {target} not found")
    mt5.shutdown()
    quit()

point = info.point
tick_value = info.trade_tick_value
tick_size = info.trade_tick_size
contract_size = info.trade_contract_size

# Current Price
bid = info.bid
ask = info.ask

data = []
total_risk_usd = 0
total_reward_usd = 0
total_profit_usd = 0

for p in positions:
    # Risk (Distance from Open to SL)
    # Trade Type: 0 = Buy, 1 = Sell
    if p.type == 1: # SELL
        risk_pts = (p.sl - p.price_open) / point if p.sl > 0 else 0
        reward_pts = (p.price_open - p.tp) / point if p.tp > 0 else 0
        current_profit_pts = (p.price_open - bid) / point
    else: # BUY
        risk_pts = (p.price_open - p.sl) / point if p.sl > 0 else 0
        reward_pts = (p.tp - p.price_open) / point if p.tp > 0 else 0
        current_profit_pts = (bid - p.price_open) / point

    # USD Conversion (Using formula: Lots * ContractSize * (PriceDiff in pts * Point))
    risk_usd = p.volume * contract_size * (risk_pts * point) if risk_pts > 0 else 0
    reward_usd = p.volume * contract_size * (reward_pts * point) if reward_pts > 0 else 0
    profit_usd = p.profit # Directly from MT5

    rr = reward_usd / risk_usd if risk_usd > 0 else 0
    
    total_risk_usd += risk_usd
    total_reward_usd += reward_usd
    total_profit_usd += profit_usd

    data.append({
        "Ticket": p.ticket,
        "Type": "SELL" if p.type == 1 else "BUY",
        "Volume": p.volume,
        "Open": p.price_open,
        "SL": p.sl,
        "TP": p.tp,
        "Risk_USD": round(risk_usd, 2),
        "Reward_USD": round(reward_usd, 2),
        "RR": round(rr, 2),
        "Profit_USD": round(p.profit, 2)
    })

df = pd.DataFrame(data)
print(f"\n--- {target} Risk/Reward Analysis ---")
print(df.to_string(index=False))

print(f"\n--- Aggregated Stats ---")
print(f"Total Risk Explorure: ${round(total_risk_usd, 2)}")
print(f"Total Potential Reward: ${round(total_reward_usd, 2)}")
print(f"Current Floating P/L: ${round(total_profit_usd, 2)}")
print(f"Portfolio R/R: {round(total_reward_usd / total_risk_usd, 2) if total_risk_usd > 0 else 'N/A'}")

# ATR Check
rates = mt5.copy_rates_from_pos(target, mt5.TIMEFRAME_H1, 0, 14)
if rates is not None:
    df_atr = pd.DataFrame(rates)
    tr = np.maximum(df_atr['high'] - df_atr['low'], 
                    np.maximum(abs(df_atr['high'] - df_atr['close'].shift(1)), 
                               abs(df_atr['low'] - df_atr['close'].shift(1))))
    atr = tr.mean()
    print(f"ATR (H1): {round(atr, 3)} pts")
    # Recommended SL distance for SELL (Current Price + 1.5 * ATR)
    rec_sl = bid + (1.5 * atr)
    print(f"Suggested SL for SELL positions (Price + 1.5*ATR): {round(rec_sl, 3)}")

mt5.shutdown()
