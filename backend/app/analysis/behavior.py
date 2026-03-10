from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from app.core.logging import get_logger

logger = get_logger(__name__)

class BehavioralAnalyzer:
    """
    Analyzes trade history to extract behavioral metrics.
    """
    def __init__(self, history_deals: list[dict]):
        self.raw_deals = history_deals
        self.df = self._prepare_dataframe(history_deals)
        
    def _prepare_dataframe(self, deals: list[dict]) -> pd.DataFrame:
        if not deals:
            return pd.DataFrame()
            
        df = pd.DataFrame(deals)
        # Ensure timestamp is datetime
        df['time'] = pd.to_datetime(df['time'], unit='s')
        
        # Sort by time
        df = df.sort_values('time')
        
        # Filter only EXIT deals (profit/loss realized) for PnL analysis
        # Entry=1 (OUT), Entry=2 (INOUT) usually imply closure or reversal
        # Note: MT5 implementation might vary, but typically deal.entry=1 is 'OUT'
        # We will assume entry=1 is the closing deal for simple PnL calc
        return df

    def calculate_basic_stats(self) -> dict:
        """
        Calculate Win Rate, Profit Factor, Total Trades, etc.
        """
        if self.df.empty:
            return {}
            
        # Filter for closed deals (realized PnL)
        # Usually deal type BUY/SELL and entry OUT/INOUT
        closed_deals = self.df[self.df['entry'].isin([1, 2])].copy()
        
        if closed_deals.empty:
            return {"total_trades": 0}
            
        total_trades = len(closed_deals)
        wins = closed_deals[closed_deals['profit'] > 0]
        losses = closed_deals[closed_deals['profit'] < 0]
        
        win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0.0
        
        gross_profit = wins['profit'].sum()
        gross_loss = abs(losses['profit'].sum())
        
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 99.99
        
        net_profit = closed_deals['profit'].sum()
        
        return {
            "total_trades": total_trades,
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "net_profit": round(net_profit, 2),
            "avg_win": round(wins['profit'].mean(), 2) if not wins.empty else 0,
            "avg_loss": round(losses['profit'].mean(), 2) if not losses.empty else 0,
        }

    def calculate_overtrading_score(self, outlier_threshold_std=2.0) -> dict:
        """
        Detect overtrading by analyzing trade frequency per hour.
        High score implies deviation from normal frequency.
        """
        if self.df.empty:
            return {"score": 0.0, "details": "No Data"}
            
        # Group by hour
        # Use simple floor to hour
        self.df['hour_key'] = self.df['time'].dt.strftime('%Y-%m-%d %H:00')
        trades_per_hour = self.df.groupby('hour_key').size()
        
        if len(trades_per_hour) < 5:
            return {"score": 0.0, "details": "Not enough data for overtrading analysis"}
            
        mean_trades = trades_per_hour.mean()
        std_trades = trades_per_hour.std()
        
        # Calculate deviation of recent hours
        last_24h = trades_per_hour.tail(24) # approx recent
        # Check if any recent hour is > mean + 2*std
        overtrade_hours = last_24h[last_24h > (mean_trades + outlier_threshold_std * std_trades)]
        
        score = 0.0
        if not overtrade_hours.empty:
            # Score 0-100 based on magnitude
            max_deviation = (overtrade_hours.max() - mean_trades) / std_trades
            score = min(100.0, max_deviation * 20.0) # Arbitrary scaling
            
        return {
            "score": round(score, 1),
            "mean_hourly_trades": round(mean_trades, 1),
            "max_hourly_trades": int(trades_per_hour.max()),
            "overtrading_detected": not overtrade_hours.empty
        }

    def calculate_revenge_trading(self, time_window_min=5) -> dict:
        """
        Detect revenge trading: Opening new trades shortly after a LOSS.
        """
        if self.df.empty:
            return {"score": 0.0}

        # Identify closing deals that were LOSSES
        losses = self.df[(self.df['entry'].isin([1, 2])) & (self.df['profit'] < 0)].sort_values('time')
        
        if losses.empty:
            return {"score": 0.0, "details": "No losses to analyze"}
            
        # Identify "Entries" (New positions)
        entries = self.df[self.df['entry'] == 0].sort_values('time')
        
        revenge_count = 0
        total_losses = len(losses)
        
        for _, loss_deal in losses.iterrows():
            loss_time = loss_deal['time']
            # Look for entries within X minutes AFTER loss
            window_end = loss_time + timedelta(minutes=time_window_min)
            
            subsequent_entries = entries[
                (entries['time'] > loss_time) & 
                (entries['time'] <= window_end)
            ]
            
            if not subsequent_entries.empty:
                revenge_count += 1
                
        score = (revenge_count / total_losses * 100) if total_losses > 0 else 0
        
        return {
            "score": round(score, 1),
            "revenge_trades_count": revenge_count,
            "total_losses": total_losses,
            "details": f"{revenge_count} revenge entries detected within {time_window_min}m of a loss"
        }

    def calculate_tilt_metric(self) -> dict:
        """
        Detect Tilt: Check if lot size INCREASES significantly after a loss.
        """
        if self.df.empty:
            return {"is_tilted": False}
            
        # We need a sequence of (Result, Next_Lot_Size)
        # This is tricky because Deals are separate.
        # Simplification: Sort all deals by time.
        # Iterate: If Deal is EXIT & LOSS -> Record Time.
        # Find NEXT Entry -> Check Lot Size. Compare to Average Lot Size.
        
        losses = self.df[(self.df['entry'].isin([1, 2])) & (self.df['profit'] < 0)].sort_values('time')
        entries = self.df[self.df['entry'] == 0].sort_values('time')
        
        if losses.empty or entries.empty:
            return {"is_tilted": False}
            
        avg_lot = entries['volume'].mean()
        tilt_instances = 0
        
        for _, loss in losses.iterrows():
            # Find next entry
            next_entries = entries[entries['time'] > loss['time']]
            if next_entries.empty:
                continue
                
            next_trade = next_entries.iloc[0]
            
            # If next trade lot is > 2x average (or 2x previous), flag it
            # Let's use > 1.5x avg for now
            if next_trade['volume'] > (avg_lot * 1.5):
                tilt_instances += 1
                
        return {
            "tilt_instances": tilt_instances,
            "avg_lot": round(avg_lot, 2),
            "is_tilted": tilt_instances > 2 # Arbitrary threshold
        }
