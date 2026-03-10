from datetime import timedelta
import pandas as pd
from app.core.logging import get_logger

logger = get_logger(__name__)

class PatternRecognizer:
    """
    Detects specific behavioral patterns in trade history.
    """
    def __init__(self, history_deals: list[dict]):
        self.raw_deals = history_deals
        # Convert to DataFrame for easier analysis
        self.df = pd.DataFrame(history_deals)
        if not self.df.empty:
            self.df['time'] = pd.to_datetime(self.df['time'], unit='s')
            self.df = self.df.sort_values('time')
            
    def analyze_all(self) -> dict:
        return {
            "loss_chasing": self.detect_loss_chasing(),
            "martingale": self.detect_martingale(),
            "session_discipline": self.detect_session_discipline_breach()
        }

    def detect_loss_chasing(self) -> dict:
        """
        Detect rapid-fire trades after a loss to 'make it back'.
        Pattern: Loss -> Entry < 2min -> Loss -> Entry < 2min
        """
        if self.df.empty: return {"detected": False}
        
        # Filter exits and entries
        exits = self.df[self.df['entry'].isin([1, 2])].copy()
        entries = self.df[self.df['entry'] == 0].copy()
        
        if exits.empty: return {"detected": False}
        
        chasing_sequences = 0
        # Iterate through losses
        losses = exits[exits['profit'] < 0]
        
        for i, loss in losses.iterrows():
            # Check if there was an entry immediately after
            loss_time = loss['time']
            next_entry = entries[
                (entries['time'] > loss_time) & 
                (entries['time'] < loss_time + timedelta(minutes=2))
            ]
            
            if not next_entry.empty:
                chasing_sequences += 1
                
        return {
            "detected": chasing_sequences > 0,
            "count": chasing_sequences,
            "description": "Rapid re-entry after loss (< 2 mins)"
        }

    def detect_martingale(self) -> dict:
        """
        Detect doubling of lot size after loss.
        """
        if self.df.empty: return {"detected": False}
        
        # This requires reconstructing the sequence of Entry(Lot) -> Exit(Profit)
        # Simplified: Look at sorted entries. If Vol[i] ~ 2*Vol[i-1] AND Prev Trade was Loss...
        # We need to map entries to their exits to know if previous was loss.
        # This is complex with partial closes / multi positions.
        
        # Heuristic: Check sequential entries. If Volume doubles 2 times in a row, flag it.
        
        entries = self.df[self.df['entry'] == 0].sort_values('time')
        if len(entries) < 3: return {"detected": False}
        
        martingale_instances = 0
        previous_vol = entries.iloc[0]['volume']
        scale_streak = 0
        
        for i in range(1, len(entries)):
            current_vol = entries.iloc[i]['volume']
            
            # Check for ~2x multiplier (1.8x to 2.2x)
            if 1.8 * previous_vol <= current_vol <= 2.2 * previous_vol:
                scale_streak += 1
            else:
                scale_streak = 0
                
            if scale_streak >= 2: # Doubled twice in a row (e.g. 0.1 -> 0.2 -> 0.4)
                martingale_instances += 1
                
            previous_vol = current_vol
            
        return {
            "detected": martingale_instances > 0,
            "count": martingale_instances,
            "description": "Volume doubling chain >= 2 times"
        }
        
    def detect_session_discipline_breach(self) -> dict:
        """
        Detect trading outside standard hours or during 'Toxic' sessions (e.g. 23:00 - 01:00 thin liquidity).
        """
        if self.df.empty: return {"detected": False}
        
        # Define Toxic Hours (Local Time 7 is provided, need to adjust based on user timezone or server time)
        # Assuming Data is UTC.
        # Toxic: Rollover (21:00 - 22:00 UTC approx)
        
        entries = self.df[self.df['entry'] == 0].copy()
        entries['hour'] = entries['time'].dt.hour
        
        # Example: Trading during rollover 21:00-22:00 UTC
        rollover_trades = entries[entries['hour'].isin([21, 22])] # Adjust as needed
        
        return {
            "detected": not rollover_trades.empty,
            "count": len(rollover_trades),
            "description": "Trades executed during high-spread rollover hours"
        }
