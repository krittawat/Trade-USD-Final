import asyncio
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Optional

from app.core.logging import get_logger
from app.core.mode import TradingMode
from app.domain.models import AccountState, SymbolProfile
from app.mt5.market_data import TIMEFRAME_MAP

logger = get_logger(__name__)

class ReplayStreamer:
    """
    เล่นข้อมูลย้อนหลัง → execution pipeline.
    
    ใช้สำหรับการพัฒนาและปรับจูนบอทให้ฉลาดขึ้นโดยการจำลองเหตุการณ์ในอดีต (Market Replay).
    """

    def __init__(self, db, pipeline, analytics_db=None) -> None:
        self.db = db
        self.pipeline = pipeline
        self.analytics_db = analytics_db
        self.is_running = False
        self.current_symbol = None
        self.progress = 0.0
        self._stop_requested = False

    async def replay(
        self,
        symbol: str,
        timeframe: str = "M5",
        start_date: str = "",
        end_date: str = "",
        speed: float = 1.0,  # Delay between bars in seconds
        window_size: int = 500,
    ) -> None:
        """
        เล่นข้อมูลย้อนหลังสำหรับ symbol โดยรันผ่าน execution pipeline ทีละบาร์.
        """
        self.is_running = True
        self._stop_requested = False
        self.current_symbol = symbol
        
        logger.info("replay_start", extra={
            "symbol": symbol,
            "timeframe": timeframe,
            "start": start_date,
            "end": end_date,
            "speed": speed
        })

        try:
            # 1. Load historical data from DuckDB (pre-downloaded OHLCV)
            # Fallback to mt5 copy_rates if DB is empty? Replay usually expects stored data.
            df = await self._fetch_historical_data(symbol, timeframe, start_date, end_date)
            if df is None or len(df) < window_size:
                logger.error("replay_insufficient_data", extra={"symbol": symbol, "len": len(df) if df is not None else 0})
                return

            total_bars = len(df)
            
            # 2. Iterate through data in sliding windows (Replay Loop)
            for i in range(window_size, total_bars):
                if self._stop_requested:
                    logger.info("replay_stopped_by_user", extra={"symbol": symbol})
                    break
                
                # Update progress
                self.progress = (i / total_bars) * 100
                
                # Get current window
                window = df.iloc[i - window_size : i + 1]
                current_bar_ts = window.index[-1]
                
                # 3. Simulate Account State and Profile
                # For replay, we use static or snapshot account values
                account = AccountState(
                    balance=10000.0,
                    equity=10000.0,
                    free_margin=10000.0,
                    currency="USD"
                )
                
                profile = SymbolProfile(
                    symbol=symbol,
                    pip_value=1.0, # Placeholder, should be fetched from profile store
                    volume_min=0.01,
                    volume_step=0.01,
                    volume_max=100.0,
                    spread_threshold=500,
                    session_template="24x7"
                )

                # 4. Trigger Pipeline in REPLAY mode
                # The pipeline itself MUST be aware of the mode
                from app.strategy.factory import StrategyFactory
                from app.domain.models import RegimeContext
                
                # Run alpha logic on current window
                strategy_factory = StrategyFactory(self.db)
                decision = await strategy_factory.get_decision(symbol, window)
                
                if decision:
                    # In REPLAY, we bypass actual MT5 connection and use Simulator/Mock adapter
                    # Pipeline handles persistence to SQLite brain.db automatically
                    await self.pipeline.execute(
                        decision=decision,
                        profile=profile,
                        account=account,
                        candles=window,
                        regime=decision.tags.get("regime", "UNKNOWN"),
                        session="REPLAY"
                    )

                # 5. Delay based on speed
                if speed > 0:
                    await asyncio.sleep(speed)

            logger.info("replay_complete", extra={"symbol": symbol, "bars_processed": total_bars})
            
        except Exception as e:
            logger.error("replay_error", extra={"symbol": symbol, "error": str(e)}, exc_info=True)
        finally:
            self.is_running = False
            self.progress = 0.0

    async def _fetch_historical_data(self, symbol: str, timeframe: str, start: str, end: str) -> Optional[pd.DataFrame]:
        """ดึงข้อมูลจาก Analytics DB (DuckDB) หรือ SQLite fallback."""
        if self.analytics_db:
            try:
                df = self.analytics_db.get_candles(symbol, timeframe)
                if df is not None and not df.empty:
                    # Sync time format
                    if 'time' in df.columns:
                        if isinstance(df['time'].iloc[0], (int, np.integer, float)):
                            unit = 's' if df['time'].iloc[0] < 1e11 else 'ms'
                            df['time'] = pd.to_datetime(df['time'], unit=unit)
                        else:
                            df['time'] = pd.to_datetime(df['time'])
                        df.set_index('time', inplace=True)
                    return df
            except Exception as e:
                logger.warning("replay_duckdb_fetch_failed", extra={"error": str(e)})

        # Fallback direct MT5 fetch
        from app.mt5.market_data import fetch_candles
        try:
            return fetch_candles(symbol, timeframe, count=5000)
        except Exception as e:
            logger.error("replay_mt5_fetch_failed", extra={"symbol": symbol, "error": str(e)})
            return None

    def stop(self):
        """ร้องขอให้หยุดการเล่นข้อมูล."""
        self._stop_requested = True
