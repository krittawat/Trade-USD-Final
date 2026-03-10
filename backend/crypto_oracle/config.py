import os
from pydantic_settings import BaseSettings

class CryptoOracleConfig(BaseSettings):
    BINANCE_FUTURES_WS_URL: str = "wss://fstream.binance.com/ws"
    SYMBOL: str = "btcusdt"
    
    # Model Parameters
    LIQUIDATION_LEVERAGE_LEVELS: list[int] = [100, 50, 25, 10]
    MAINTENANCE_MARGIN_RATE: float = 0.004  # 0.4% for BTC on Binance
    
    # Squeeze Detection Thresholds
    HIGH_FUNDING_RATE_THRESHOLD: float = 0.0001 # 0.01%
    EXTREME_FUNDING_RATE_THRESHOLD: float = 0.0003 # 0.03%
    
    class Config:
        env_file = ".env"
        extra = "ignore"

config = CryptoOracleConfig()
