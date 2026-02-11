"""
Replay — สตรีมข้อมูลย้อนหลังผ่าน live pipeline.

หน้าที่:
    - ดึง candles จาก QuestDB
    - สตรีมทีละ window ผ่าน execution pipeline
    - ส่งผลลัพธ์ไปแสดงบน frontend (WebSocket)

กฎ RAM:
    - ใช้ windowed buffer — ไม่ load ข้อมูลทั้งหมด
"""

from app.core.logging import get_logger

logger = get_logger(__name__)


class ReplayStreamer:
    """
    สตรีม historical data ผ่าน execution pipeline.
    
    ผลลัพธ์ส่งไป frontend ผ่าน WebSocket (windowed).
    """

    def __init__(self, questdb_client=None, pipeline=None) -> None:
        self.questdb = questdb_client
        self.pipeline = pipeline
        self.window_size = 200  # จำนวนแท่งต่อ window

    async def replay(
        self,
        symbol: str,
        timeframe: str = "M5",
        start_date: str = "",
        end_date: str = "",
    ) -> None:
        """
        เริ่ม replay session.
        
        Args:
            symbol: สัญลักษณ์
            timeframe: ไทม์เฟรม
            start_date: วันที่เริ่ม (ISO format)
            end_date: วันที่จบ (ISO format)
        """
        logger.info("replay_start", extra={
            "symbol": symbol,
            "timeframe": timeframe,
            "start": start_date,
            "end": end_date,
        })

        # TODO: Implement:
        # 1. ดึง candles จาก QuestDB (windowed)
        # 2. Loop ทีละ window ส่งผ่าน pipeline
        # 3. ส่งผลไป frontend ผ่าน WebSocket

        logger.info("replay_complete", extra={"symbol": symbol})
