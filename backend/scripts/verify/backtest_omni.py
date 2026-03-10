import asyncio
import sys
import argparse
import pandas as pd
from datetime import datetime

from app.mt5.market_data import fetch_candles
from app.mt5.client import MT5Client
from app.strategy.factory import StrategyFactory
from app.domain.models import SymbolProfile, AccountState, RegimeContext
from app.domain.enums import RegimeType, Action

async def run_omni_backtest():
    from app.core.config import get_settings
    settings = get_settings()
    mt5 = MT5Client(settings)
    if not mt5.connect():
        print("Failed to connect to MT5")
        return

    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", type=str, default="XAUUSDc")
    parser.add_argument("--strategy", type=str, default="candlestick_structure")
    parser.add_argument("--days", type=int, default=30)
    args, _ = parser.parse_known_args()

    symbol = args.symbol
    print(f"Fetching candles for {symbol} (M5)...")
    candles = await asyncio.to_thread(fetch_candles, symbol, "M5", 3000)
    
    if candles is None or len(candles) < 100:
        print("Not enough data")
        return

    # Fetch H1 for MTF support
    print(f"Fetching H1 candles for {symbol}...")
    h1_candles = await asyncio.to_thread(fetch_candles, symbol, "H1", 500)
    
    profile = SymbolProfile(symbol=symbol)
    factory = StrategyFactory()
    factory.auto_register()
    
    strategy_name = args.strategy
    strategy = factory._strategies.get(strategy_name)
    if not strategy:
        print(f"Strategy {strategy_name} not found")
        return
        
    print(f"\n--- Running Backtest for Omni-Directional Smart Trading ({symbol}) ---")
    
    trades = []
    wins = 0
    losses = 0
    total_profit = 0.0
    
    # Simulate step-by-step
    for i in range(200, len(candles)):
        current_candles = candles.iloc[:i]
        last_row = current_candles.iloc[-1]
        
        # Simulate H1 match for MTF
        current_time = last_row.name
        try:
            current_h1 = h1_candles[h1_candles.index <= current_time]
        except Exception:
            current_h1 = None
            
        kwargs = {"h1_candles": current_h1}
        decision = strategy.analyze(current_candles, profile, RegimeType.UNKNOWN, **kwargs)
        
        if decision.action != Action.HOLD:
            # Check if MTF Engine accepts it
            from app.brain.mtf_confluence import MTFConfluenceEngine
            mtf_engine = MTFConfluenceEngine()
            
            candles_by_tf = {"M5": current_candles}
            if current_h1 is not None and not current_h1.empty:
                candles_by_tf["H1"] = current_h1
                
            is_reversal = False
            if decision.tags and any("reversal" in str(t).lower() or "choch" in str(t).lower() for t in decision.tags):
                is_reversal = True
                
            mtf_result = mtf_engine.score(candles_by_tf, direction=decision.action.value, is_reversal=is_reversal)
            
            # If MTF total > 0 and confidence > 0.65 (Strong Setup)
            if mtf_result.total > 20 and decision.confidence >= 0.65:
                # Determine outcome based on simplistic future lookahead for backtest demo purposes
                # 100 candles on M5 is ~8.3 hours, enough time for a bounce trade to mature
                future_candles = candles.iloc[i:i+100]
                entry_price = float(last_row['close'])
                sl = decision.stop_loss
                tp = decision.take_profit
                
                # Default to whatever the profit is at the end of the window if neither SL/TP is hit
                outcome = "TIMEOUT"
                profit = 0.0
                
                # Iterate sequentially to see what gets hit first
                for idx, future_row in future_candles.iterrows():
                    high_val = float(future_row['high'])
                    low_val = float(future_row['low'])

                    if decision.action == Action.BUY:
                        if low_val <= sl:
                            outcome = "LOSS"
                            profit = -abs(entry_price - sl)
                            # print(f"DEBUG: Hit SL {sl} at low {low_val} (Entry: {entry_price})")
                            break
                        if high_val >= tp:
                            outcome = "WIN"
                            profit = abs(tp - entry_price)
                            # print(f"DEBUG: Hit TP {tp} at high {high_val} (Entry: {entry_price})")
                            break
                    elif decision.action == Action.SELL:
                        if high_val >= sl:
                            outcome = "LOSS"
                            profit = -abs(entry_price - sl)
                            # print(f"DEBUG: Hit SL {sl} at high {high_val} (Entry: {entry_price})")
                            break
                        if low_val <= tp:
                            outcome = "WIN"
                            profit = abs(entry_price - tp)
                            # print(f"DEBUG: Hit TP {tp} at low {low_val} (Entry: {entry_price})")
                            break
                            
                # If we timed out, check floating profit at the end
                if outcome == "TIMEOUT" and not future_candles.empty:
                    final_close = float(future_candles.iloc[-1]['close'])
                    if decision.action == Action.BUY:
                        profit = final_close - entry_price
                        outcome = "WIN" if profit > 0 else "LOSS"
                    else:
                        profit = entry_price - final_close
                        outcome = "WIN" if profit > 0 else "LOSS"
                        
                trades.append({
                    "time": current_time,
                    "action": decision.action.value,
                    "type": "REVERSAL" if is_reversal else "CONTINUATION",
                    "reason": decision.reason,
                    "entry": entry_price,
                    "sl": sl,
                    "tp": tp,
                    "outcome": outcome,
                    "profit": profit
                })
                
                if outcome == "WIN":
                    wins += 1
                else:
                    losses += 1
                total_profit += profit

    print("\n--- Backtest Results ---")
    print(f"Total Trades: {len(trades)}")
    if len(trades) > 0:
        win_rate = (wins / len(trades)) * 100
        print(f"Wins: {wins}, Losses: {losses}")
        print(f"Win Rate: {win_rate:.2f}%")
        print(f"Est. Profit (Raw Pips/Pts): {total_profit:.2f}")
        
        reversals = [t for t in trades if t["type"] == "REVERSAL"]
        if reversals:
            rev_wins = sum(1 for t in reversals if t["outcome"] == "WIN")
            print(f"\nOmni-Directional (Reversal) Trades: {len(reversals)}")
            print(f"Reversal Win Rate: {(rev_wins/len(reversals))*100:.2f}%")
            print("--- Sample Reversal Trades ---")
            for t in reversals[:3]:
                 print(f"[{t['time']}] {t['action']} (Reversal) -> {t['outcome']} | Reason: {t['reason']}")

        continuations = [t for t in trades if t["type"] == "CONTINUATION"]
        if continuations:
            print("\n--- Sample Continuation Trades ---")
            for t in continuations[:5]:
                 print(f"[{t['time']}] {t['action']} -> {t['outcome']} | Reason: {t['reason']}")
                 
    else:
        print("No trades found.")

if __name__ == "__main__":
    asyncio.run(run_omni_backtest())
