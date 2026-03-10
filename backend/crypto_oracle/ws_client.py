import asyncio
import json
import logging
import websockets
from typing import Callable, Dict, Any

from app.core.logging import get_logger
from .config import config

logger = get_logger("crypto_oracle.ws_client")

class BinanceWSClient:
    def __init__(self, symbol: str = config.SYMBOL):
        self.symbol = symbol.lower()
        self.url = config.BINANCE_FUTURES_WS_URL
        self.callbacks: list[Callable[[Dict[str, Any]], None]] = []
        self._running = False
        self._ws = None

    def add_callback(self, callback: Callable[[Dict[str, Any]], None]):
        self.callbacks.append(callback)

    def _get_streams(self) -> list[str]:
        # Streams we need for Liquidation Heatmap & Squeeze Detecion
        return [
            f"{self.symbol}@depth20@100ms",   # Orderbook depth
            f"{self.symbol}@markPrice@1s",    # Mark price & Funding rate
            f"{self.symbol}@aggTrade",        # Volume delta / CVD
            f"{self.symbol}@forceOrder"       # Liquidations
        ]

    async def _subscribe(self):
        streams = self._get_streams()
        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": streams,
            "id": 1
        }
        await self._ws.send(json.dumps(subscribe_msg))
        logger.info(f"Subscribed to streams: {streams}")

    async def start(self):
        self._running = True
        while self._running:
            try:
                logger.info(f"Connecting to Binance WS: {self.url}")
                async with websockets.connect(self.url) as ws:
                    self._ws = ws
                    await self._subscribe()
                    
                    async for message in ws:
                        if not self._running:
                            break
                        
                        data = json.loads(message)
                        
                        # Handle combined stream or single stream format
                        if "e" in data:
                            self._handle_message(data)
                        elif "id" in data:
                            # Response to subscribe
                            pass
                        
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket Connection Closed: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"WebSocket Error: {e}", exc_info=True)
                await asyncio.sleep(5)

    def _handle_message(self, data: Dict[str, Any]):
        for callback in self.callbacks:
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Error in WS callback: {e}", exc_info=True)

    def stop(self):
        self._running = False
        if self._ws:
            asyncio.create_task(self._ws.close())
        logger.info("Stopping Binance WS Client...")
