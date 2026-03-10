"""
QuestDB Client — เชื่อมต่อ QuestDB สำหรับ time-series data.

หน้าที่:
    - ดึงข้อมูล OHLCV (แท่งเทียน) และ ticks
    - ดึง/อัปเดต Symbol Profile (แหล่งข้อมูลหลัก)
    - Ingest ข้อมูลผ่าน ILP (InfluxDB Line Protocol)
    - Health check

หมายเหตุ:
    - ใช้ HTTP REST API (port 9000) สำหรับ query
    - ใช้ ILP (port 9009) สำหรับ ingest ข้อมูล
    - JVM heap ต้องจำกัดไว้ (-Xms256m -Xmx512m)
"""

import socket
from datetime import datetime, timezone

import httpx

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class QuestDBClient:
    """
    QuestDB client wrapper.
    Uses httpx for HTTP REST queries and ILP protocol for ingestion.
    """

    def __init__(self, settings: Settings) -> None:
        self.host = settings.questdb_host
        self.http_port = settings.questdb_http_port
        self.ilp_port = settings.questdb_ilp_port
        self.pg_port = settings.questdb_pg_port
        self._connected = False

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.http_port}"

    async def connect(self) -> None:
        """เชื่อมต่อ QuestDB — ตรวจสอบว่า service ทำงานอยู่."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{self.base_url}/exec", params={"query": "SELECT 1"})
                self._connected = r.status_code == 200
        except Exception as e:
            logger.warning("questdb_connect_failed", extra={"error": str(e)})
            self._connected = False

        logger.info("questdb_connect", extra={
            "host": self.host, "port": self.http_port,
            "connected": self._connected,
        })

    async def health_check(self) -> bool:
        """ตรวจสอบว่า QuestDB ยังทำงานอยู่."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{self.base_url}/exec", params={"query": "SELECT 1"})
                self._connected = r.status_code == 200
                return self._connected
        except Exception:
            self._connected = False
            return False

    async def query(self, sql: str) -> list[dict]:
        """Execute a SQL query and return results."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"{self.base_url}/exec", params={"query": sql})
                if r.status_code == 200:
                    data = r.json()
                    columns = [col["name"] for col in data.get("columns", [])]
                    rows = data.get("dataset", [])
                    return [dict(zip(columns, row)) for row in rows]
        except Exception as e:
            logger.error("questdb_query_error", extra={"error": str(e), "sql": sql[:100]})
        return []

    async def get_symbol_profiles(self) -> list[dict]:
        """ดึง symbol profiles ทั้งหมดจาก QuestDB (source of truth)."""
        return await self.query(
            "SELECT * FROM symbol_profiles WHERE is_active = true ORDER BY symbol"
        )

    async def ingest_candles(self, symbol: str, candles: list[dict]) -> None:
        """
        บันทึกแท่งเทียนลง QuestDB ผ่าน ILP (line protocol).
        ใช้ TCP socket ตรง — ไม่ต้องพึ่ง library.
        """
        if not candles:
            return

        try:
            lines = []
            for c in candles:
                ts_ns = int(c.get("timestamp", 0)) * 1_000_000_000  # seconds → nanoseconds
                line = (
                    f"candles,symbol={symbol} "
                    f"open={c['open']},high={c['high']},low={c['low']},"
                    f"close={c['close']},volume={c.get('volume', 0)}i "
                    f"{ts_ns}"
                )
                lines.append(line)

            payload = "\n".join(lines) + "\n"

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(3.0)
                sock.connect((self.host, self.ilp_port))
                sock.sendall(payload.encode("utf-8"))

            logger.debug("ingest_candles", extra={
                "symbol": symbol, "count": len(candles),
            })
        except Exception as e:
            logger.error("ingest_candles_error", extra={
                "symbol": symbol, "error": str(e),
            })

    async def create_ticks_table(self) -> bool:
        """
        Create 'ticks' table if not exists.
        Partitioned by DAY for efficient time-series storage.
        """
        sql = """
        CREATE TABLE IF NOT EXISTS ticks (
            ts TIMESTAMP,
            symbol SYMBOL,
            bid DOUBLE,
            ask DOUBLE,
            last DOUBLE,
            volume LONG,
            flags INT
        ) TIMESTAMP(ts) PARTITION BY DAY WAL;
        """
        return await self.execute_ddl(sql)

    async def ingest_ticks(self, symbol: str, ticks: list[dict]) -> None:
        """
        Ingest ticks via ILP (InfluxDB Line Protocol).
        """
        if not ticks:
            return

        try:
            lines = []
            for t in ticks:
                # time is usually in seconds (float) or datetime in df
                # We expect dict from fetch_ticks_since which might be raw from mt5 or df.to_dict('records')
                # Let's assume input matches what we format in market_data
                
                # If timestamp is datetime, convert to ns
                ts_val = t.get("time")
                if isinstance(ts_val, datetime):
                    ts_ns = int(ts_val.replace(tzinfo=timezone.utc).timestamp() * 1_000_000_000)
                elif isinstance(ts_val, (int, float)):
                     ts_ns = int(ts_val * 1_000_000_000)
                else:
                    continue # Skip invalid time

                line = (
                    f"ticks,symbol={symbol} "
                    f"bid={t.get('bid', 0.0)},ask={t.get('ask', 0.0)},"
                    f"last={t.get('last', 0.0)},volume={int(t.get('volume', 0))}i,"
                    f"flags={int(t.get('flags', 0))}i "
                    f"{ts_ns}"
                )
                lines.append(line)

            if not lines:
                return

            payload = "\n".join(lines) + "\n"

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(2.0)
                sock.connect((self.host, self.ilp_port))
                sock.sendall(payload.encode("utf-8"))

            logger.debug("ingest_ticks", extra={
                "symbol": symbol, "count": len(ticks),
            })
        except Exception as e:
            # Silent fail for high-freq data to avoid log spam, but log debug
            logger.debug("ingest_ticks_error", extra={"symbol": symbol, "error": str(e)})

    async def cleanup_old_data(self, retention_days: int = 7) -> None:
        """
        Drop partitions older than retention_days.
        QuestDB organizes data by DAY partition (set in create table).
        DROP PARTITION is efficient.
        """
        if retention_days <= 0:
            return

        try:
            # Drop partitions older than N days
            # Syntax: ALTER TABLE ticks DROP PARTITION WHERE timestamp < dateadd('d', -N, now())
            # Note: QuestDB DROP PARTITION operates on partition keys.
            # Using WHERE clause with timestamp is supported in newer versions or use iterative drop.
            # Efficient way: ALTER TABLE ticks DROP PARTITION WHERE ts < dateadd('d', -?, now())
            
            sql = f"ALTER TABLE ticks DROP PARTITION WHERE ts < dateadd('d', -{retention_days}, now())"
            
            logger.info("questdb_cleanup_start", extra={"retention_days": retention_days})
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(f"{self.base_url}/exec", params={"query": sql})
                if r.status_code == 200:
                    logger.info("questdb_cleanup_success")
                else:
                    logger.warning("questdb_cleanup_failed", extra={"status": r.status_code, "response": r.text})

        except Exception as e:
            logger.error("questdb_cleanup_error", extra={"error": str(e)})

    async def execute_ddl(self, sql: str) -> bool:
        """Execute DDL (CREATE TABLE, etc)."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.base_url}/exec", params={"query": sql})
                return r.status_code == 200
        except Exception as e:
            logger.error("questdb_ddl_error", extra={"error": str(e)})
            return False

    async def disconnect(self) -> None:
        """ปิดการเชื่อมต่อ."""
        self._connected = False
        logger.info("questdb_disconnected")
