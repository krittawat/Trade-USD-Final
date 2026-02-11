"""
QuestDB Client — เชื่อมต่อ QuestDB สำหรับ time-series data.

หน้าที่:
    - ดึงข้อมูล OHLCV (แท่งเทียน) และ ticks
    - ดึง/อัปเดต Symbol Profile (แหล่งข้อมูลหลัก)
    - Ingest ข้อมูลผ่าน ILP (InfluxDB Line Protocol)
    - Health check

หมายเหตุ:
    - ใช้ PostgreSQL wire protocol (port 8812) สำหรับ query
    - ใช้ ILP (port 9009) สำหรับ ingest ข้อมูล
    - JVM heap ต้องจำกัดไว้ (-Xms256m -Xmx512m)
"""

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class QuestDBClient:
    """
    QuestDB client wrapper.
    
    ใช้ questdb Python package สำหรับ ILP ingestion
    และ httpx/psycopg2 สำหรับ query ผ่าน PG wire protocol.
    """

    def __init__(self, settings: Settings) -> None:
        self.host = settings.questdb_host
        self.http_port = settings.questdb_http_port
        self.ilp_port = settings.questdb_ilp_port
        self.pg_port = settings.questdb_pg_port
        self._connected = False

    async def connect(self) -> None:
        """เชื่อมต่อ QuestDB — ตรวจสอบว่า service ทำงานอยู่."""
        # TODO: implement ตรวจ health endpoint
        logger.info("questdb_connect", extra={"host": self.host, "port": self.http_port})
        self._connected = True

    async def health_check(self) -> bool:
        """ตรวจสอบว่า QuestDB ยังทำงานอยู่."""
        # TODO: GET http://{host}:{http_port}/exec?query=select+1
        return self._connected

    async def get_symbol_profiles(self) -> list[dict]:
        """ดึง symbol profiles ทั้งหมดจาก QuestDB (source of truth)."""
        # TODO: SELECT * FROM symbol_profiles WHERE is_active = true
        logger.info("fetch_symbol_profiles")
        return []

    async def ingest_candles(self, symbol: str, candles: list[dict]) -> None:
        """
        บันทึกแท่งเทียนลง QuestDB ผ่าน ILP.
        
        ใช้ windowed buffer — ไม่เก็บข้อมูลทั้งหมดใน RAM.
        """
        # TODO: ใช้ questdb.ingress.Sender
        logger.debug("ingest_candles", extra={"symbol": symbol, "count": len(candles)})

    async def disconnect(self) -> None:
        """ปิดการเชื่อมต่อ."""
        self._connected = False
        logger.info("questdb_disconnected")
