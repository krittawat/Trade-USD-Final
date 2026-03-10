import MetaTrader5 as mt5
import pandas as pd
import time
from typing import Optional
from .mapper import mapper
from .time_utils import time_utils
import logging

logger = logging.getLogger("opus_logger")

class DataFetcher:
    def __init__(self):
        self.connected = False

    def connect(self) -> bool:
        """Initialize connection to MT5 terminal."""
        if not mt5.initialize():
            logger.error(f"MT5 initialize() failed, error code: {mt5.last_error()}")
            return False
        self.connected = True
        logger.info("Connected to MT5 successfully.")
        return True

    def disconnect(self):
        """Shutdown connection."""
        if self.connected:
            mt5.shutdown()
            self.connected = False

    def get_rates(self, symbol: str, timeframe: int, bars: int) -> Optional[pd.DataFrame]:
        """
        Fetches historical OHLCV data. 
        `symbol` should be standard, it will be mapped to broker format.
        """
        broker_symbol = mapper.to_broker(symbol)
        
        if not self.connected:
            if not self.connect():
                return None
        
        # Ensure symbol is selected / visible in Market Watch
        if not mt5.symbol_select(broker_symbol, True):
            logger.error(f"Failed to select symbol {broker_symbol} in Market Watch.")
            return None
                
        # Retry logic for "Terminal: Call failed" (-1)
        rates = None
        for attempt in range(3):
            rates = mt5.copy_rates_from_pos(broker_symbol, timeframe, 0, bars)
            if rates is not None and len(rates) > 0:
                break
            
            err = mt5.last_error()
            logger.warning(f"Attempt {attempt+1} failed for {broker_symbol} (TF={timeframe}): {err}")
            time.sleep(0.5)
        
        if rates is None or len(rates) == 0:
            logger.error(f"Failed to fetch rates for {broker_symbol} after retries, error: {mt5.last_error()}")
            return None

        # Convert to DataFrame
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        
        # Verify integrity
        if df.isnull().values.any():
            logger.warning(f"Null values detected in {broker_symbol} rates. Dropping.")
            df.dropna(inplace=True)
            
        # Add session data
        df = time_utils.add_session_features(df)
        
        return df

fetcher = DataFetcher()
