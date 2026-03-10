"""
Market-Adaptive Backtest & Self-Optimization Engine (Blueprint V2)
GG Antigravity Edition

Simulates Blueprint specifications:
- Data Source: MT5 (Proxy for Binance OHLCV 1H/15M)
- Initial Capital: $100
- Risk Management: 1-2% Fixed Fractional
- Goal: Sharpe Ratio > 2.0
"""

import MetaTrader5 as mt5
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import itertools
from app.core.logging import get_logger
logger = get_logger(__name__)

from app.strategy.templates.gg_adaptive_weaponry import GGAdaptiveWeaponry
from app.domain.models import SymbolProfile

def fetch_data(symbol: str, timeframe: int, days: int = 365) -> pd.DataFrame:
    if not mt5.initialize():
        logger.error("MT5 init failed")
        return pd.DataFrame()
        
    utc_to = datetime.now()
    utc_from = utc_to - timedelta(days=days)
    
    rates = mt5.copy_rates_range(symbol, timeframe, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        logger.error(f"No data fetched for {symbol}")
        return pd.DataFrame()
        
    df = pd.DataFrame(rates)
    df['time'] = pd.to_datetime(df['time'], unit='s')
    df.set_index('time', inplace=True)
    return df

def calculate_sharpe(returns: list, risk_free_rate: float = 0.0) -> float:
    """Calculate annualized Sharpe Ratio."""
    if len(returns) < 2:
        return 0.0
    r = np.array(returns)
    mean_ret = np.mean(r)
    std_ret = np.std(r) if np.std(r) > 0 else 1e-9
    # Assume 252 trading days for standard annualized Sharpe, though crypto is 365
    return (mean_ret - risk_free_rate) / std_ret * np.sqrt(252)

def simulate(df: pd.DataFrame, strat: GGAdaptiveWeaponry, profile: SymbolProfile, risk_pct: float = 0.015):
    """Event-driven simulation applying 1.5% Fractional Risk."""
    initial_capital = 100.0
    equity = initial_capital
    peak_equity = equity
    max_dd = 0.0
    
    trades = []
    trade_returns = []
    
    in_trade = False
    entry_price = 0.0
    action = None
    sl = 0.0
    tp = 0.0
    lot_size = 0.0
    
    for i in range(200, len(df)):
        window = df.iloc[i-200:i]
        
        # Check if in trade and if SL/TP is hit
        if in_trade:
            h, l = df.iloc[i]['high'], df.iloc[i]['low']
            trade_closed = False
            pnl_usd = 0.0
            
            if action == "BUY":
                if l <= sl:
                    trade_closed, pnl_usd = True, (sl - entry_price) * lot_size
                elif h >= tp:
                    trade_closed, pnl_usd = True, (tp - entry_price) * lot_size
            elif action == "SELL":
                if h >= sl:
                    trade_closed, pnl_usd = True, (entry_price - sl) * lot_size
                elif l <= tp:
                    trade_closed, pnl_usd = True, (entry_price - tp) * lot_size
            
            if trade_closed:
                # Deduct commissions/spread proxy (e.g. $7 per lot flat + slippage buffer)
                commissions = abs(lot_size) * 7.0 
                pnl_usd -= commissions
                
                equity += pnl_usd
                ret = pnl_usd / (equity - pnl_usd) if (equity - pnl_usd) > 0 else 0
                trade_returns.append(ret)
                
                trades.append({
                    "pnl": pnl_usd, 
                    "type": "win" if pnl_usd > 0 else "loss",
                    "ret": ret
                })
                in_trade = False
                
                if equity > peak_equity:
                    peak_equity = equity
                dd = (peak_equity - equity) / peak_equity
                if dd > max_dd:
                    max_dd = dd
            continue
            
        # Get Decision
        decision = strat.analyze(window, profile)
        if decision.action.value in ["BUY", "SELL"]:
            in_trade = True
            action = decision.action.value
            entry_price = df.iloc[i]['close']
            sl = decision.stop_loss
            tp = decision.take_profit
            
            # Risk Sizing Rule (1-2% Fixed Fractional)
            # Risk_USD = Equity * risk_pct
            # Lot = Risk_USD / (SL_Distance * Contract_Size)
            risk_usd = equity * risk_pct
            sl_dist = abs(entry_price - sl)
            # Prevent DivZero
            if sl_dist < profile.point:
                sl_dist = profile.point * 10
                
            lot_size = risk_usd / (sl_dist * profile.contract_size)
            # Clamp limits to MT5 minimum 0.01 typically
            lot_size = max(0.01, lot_size)

    wins = [t for t in trades if t['type'] == 'win']
    losses = [t for t in trades if t['type'] == 'loss']
    
    win_rate = len(wins) / len(trades) if len(trades) > 0 else 0
    total_profit = sum([t['pnl'] for t in wins]) if wins else 0
    total_loss = abs(sum([t['pnl'] for t in losses])) if losses else 0
    
    profit_factor = total_profit / total_loss if total_loss > 0 else 0
    recovery_factor = (equity - initial_capital) / (max_dd * initial_capital) if max_dd > 0 else 0
    sharpe = calculate_sharpe(trade_returns)
    
    return {
        "trades": len(trades),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_dd_pct": max_dd * 100,
        "recovery_factor": recovery_factor,
        "net_profit": equity - initial_capital,
        "sharpe": sharpe
    }

def optimize_parameters():
    """Parameter Optimizer"""
    logger.info("Initializing Parameter Optimization...")
    symbol = "BTCUSDm" # Simulating Binance pair logic
    tf = mt5.TIMEFRAME_M15
    df = fetch_data(symbol, tf, days=180) 
    
    if df.empty:
        return
        
    profile = SymbolProfile(symbol=symbol, asset_class="crypto", active=True, max_spread=30.0, stop_level=20.0, point=0.01, contract_size=1)
    
    best_sharpe = 0
    best_params = {}
    
    # Grid Search
    param_grid = {
        'ema_period': [15, 20],
        'ema_trend_slow': [50, 100],
        'rsi_period': [10, 14]
    }
    
    keys = param_grid.keys()
    combinations = list(itertools.product(*param_grid.values()))
    
    logger.info(f"Generated {len(combinations)} Parameter Combinations to test.")
    
    for combo in combinations:
        strat = GGAdaptiveWeaponry()
        p = dict(zip(keys, combo))
        strat.ema_period = p['ema_period']
        strat.ema_trend_slow = p['ema_trend_slow']
        strat.rsi_period = p['rsi_period']
        
        logger.info(f"Testing iteration: {p}")
        res = simulate(df, strat, profile, risk_pct=0.015) # Testing 1.5% Risk
        
        if res['sharpe'] > best_sharpe and res['trades'] > 20: 
            best_sharpe = res['sharpe']
            best_params = p
            
    logger.info("Optimization Complete.")
    logger.info(f"Best Params: {best_params} | Best Sharpe: {best_sharpe:.2f}")

if __name__ == "__main__":
    logger.info("Starting GG Antigravity Market-Adaptive Backtest (V2 Blueprint)")
    
    # Simulating MT5 data fetching at 1H representing Binance data scale.
    symbol = "BTCUSDm" 
    df = fetch_data(symbol, mt5.TIMEFRAME_H1, days=365)
    
    if not df.empty:
        strat = GGAdaptiveWeaponry()
        profile = SymbolProfile(symbol=symbol, asset_class="crypto", active=True, max_spread=30.0, stop_level=20.0, point=0.01, contract_size=1)
        
        logger.info("Simulating Blueprint Strategy for 1 Year ($100 Capital | 1.5% Risk)...")
        results = simulate(df, strat, profile, risk_pct=0.015)
        
        logger.info(f"--- Simulation Results ({symbol} | H1) ---")
        logger.info(f"Total Trades: {results['trades']}")
        logger.info(f"Win Rate: {results['win_rate']*100:.2f}%")
        logger.info(f"Profit Factor: {results['profit_factor']:.2f}")
        logger.info(f"Max Drawdown: {results['max_dd_pct']:.2f}%")
        logger.info(f"Sharpe Ratio: {results['sharpe']:.2f}")
        logger.info(f"Recovery Factor: {results['recovery_factor']:.2f}")
        logger.info(f"Net Profit: ${results['net_profit']:.2f}")

        # Blueprint Update 2: AI Self-Correction in Backtest
        if results['max_dd_pct'] > 20.0:
            logger.warning("------------- AI SELF-CORRECTION TRIGGERED -------------")
            logger.warning("Max Drawdown exceeded 20% limit. Suggestions for optimization:")
            logger.warning("  1. Tighten Weapon A (CDC): Decrease ATR Trailing Stop from 2.0 to 1.5")
            logger.warning("  2. Filter Range: Require ADX < 15 instead of 20 for Volatile Range")
            logger.warning("  3. Filter Sniper: Use 4-Candle confirmation for FVG instead of 3")
            logger.warning("  4. Risk: Reduce Max Risk per trade to 1.0% or 0.5% during high correlation")
            logger.warning("---------------------------------------------------------")
