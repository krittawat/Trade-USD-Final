"""
SentimentAggregator — รวบรวม sentiment จากหลายแหล่งข้อมูลภายนอก.

แหล่งข้อมูล:
    1. Fear & Greed Index (alternative.me) — ดัชนีความกลัว/โลภ crypto+market
    2. TradingView Technical Rating — สัญญาณทางเทคนิค (BUY/SELL/NEUTRAL)
    3. Social Sentiment — คะแนนจาก Reddit/Twitter (จาก social_sentiment.py)

กฎ:
    - ผลัพธ์ทั้งหมดเป็น advisor เท่านั้น — ไม่ override Risk Engine
    - ทุก source มี timeout 5 วินาที
    - ถ้า source ล้มเหลว → ใช้ neutral (0.0)
    - Cache ผลลัพธ์ตาม refresh interval (ไม่ fetch ซ้ำถี่เกิน)
    - RAM safe: เก็บแค่ snapshot ล่าสุดต่อ symbol

วิธีใช้:
    aggregator = SentimentAggregator(memory_store=memory, settings=settings)
    await aggregator.refresh()
    sentiment = aggregator.get_sentiment("XAUUSDc")
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── Config ───
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
TV_RATINGS_URL = "https://scanner.tradingview.com/forex/scan"
HTTP_TIMEOUT = 5.0  # seconds per request
DEFAULT_REFRESH_MINUTES = 15

# ─── TradingView symbol mapping ───
TV_SYMBOL_MAP = {
    "XAUUSDc": "FX_IDC:XAUUSD",
    "XAUUSD": "FX_IDC:XAUUSD",
    "EURUSDc": "FX:EURUSD",
    "EURUSD": "FX:EURUSD",
    "GBPUSDc": "FX:GBPUSD",
    "GBPUSD": "FX:GBPUSD",
    "USDJPYc": "FX:USDJPY",
    "USDJPY": "FX:USDJPY",
    "AUDUSDc": "FX:AUDUSD",
    "AUDUSD": "FX:AUDUSD",
    "USDCADc": "FX:USDCAD",
    "USDCAD": "FX:USDCAD",
    "NZDUSDc": "FX:NZDUSD",
    "NZDUSD": "FX:NZDUSD",
    "BTCUSDc": "BITSTAMP:BTCUSD",
    "BTCUSD": "BITSTAMP:BTCUSD",
}


@dataclass
class MarketSentiment:
    """สรุป sentiment ทุก source สำหรับ 1 symbol."""
    fear_greed_value: int = 50             # 0-100 (0=Extreme Fear, 100=Extreme Greed)
    fear_greed_label: str = "Neutral"
    tv_rating: str = "NEUTRAL"             # STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
    tv_score: float = 0.0                  # -1.0 to 1.0
    social_score: float = 0.0              # -1.0 to 1.0
    composite_score: float = 0.0           # -1.0 to 1.0 (weighted average)
    sources_available: int = 0
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "fear_greed_value": self.fear_greed_value,
            "fear_greed_label": self.fear_greed_label,
            "tv_rating": self.tv_rating,
            "tv_score": self.tv_score,
            "social_score": self.social_score,
            "composite_score": round(self.composite_score, 3),
            "sources_available": self.sources_available,
            "updated_at": self.updated_at.isoformat(),
        }

    @property
    def bias(self) -> str:
        """สรุป bias ง่ายๆ."""
        if self.composite_score > 0.3:
            return "BULLISH"
        elif self.composite_score < -0.3:
            return "BEARISH"
        return "NEUTRAL"


class SentimentAggregator:
    """
    รวบรวม market sentiment จากหลายแหล่ง → composite score.

    ทุก source เป็น best-effort:
        - ถ้า fetch ไม่ได้ → ใช้ neutral
        - ถ้า parse ไม่ได้ → ใช้ neutral
        - ไม่มีสิทธิ์ override Risk Engine
    """

    def __init__(self, memory_store=None, settings=None) -> None:
        self.memory = memory_store
        self.settings = settings
        self._fear_greed: dict = {}  # cached F&G data
        self._tv_ratings: dict[str, dict] = {}  # symbol → {rating, score}
        self._social_scores: dict[str, float] = {}  # symbol → score
        self._sentiments: dict[str, MarketSentiment] = {}  # symbol → MarketSentiment
        self._last_refresh: float = 0.0
        self._social_sentiment = None  # lazy init

        # Config
        self._refresh_minutes = DEFAULT_REFRESH_MINUTES
        self._fg_enabled = True
        self._tv_enabled = True
        self._social_enabled = True

        if settings:
            self._refresh_minutes = getattr(settings, 'sentiment_refresh_minutes', DEFAULT_REFRESH_MINUTES)
            self._fg_enabled = getattr(settings, 'fear_greed_enabled', True)
            self._tv_enabled = getattr(settings, 'tradingview_enabled', True)
            self._social_enabled = getattr(settings, 'social_sentiment_enabled', True)

    # ────────────────────────────────────────────────────────────────
    # refresh() — ดึงข้อมูลใหม่จากทุก source
    # ────────────────────────────────────────────────────────────────

    async def refresh(self, symbols: list[str] | None = None) -> int:
        """
        Refresh sentiment จากทุก source.

        Args:
            symbols: รายชื่อ symbols ที่ต้องการ (None = ใช้ cache เดิม)

        Returns:
            จำนวน sources ที่ fetch สำเร็จ
        """
        now = time.monotonic()
        if now - self._last_refresh < self._refresh_minutes * 60:
            return 0  # ยังไม่ถึงเวลา refresh

        self._last_refresh = now
        sources_ok = 0

        # ─── 1. Fear & Greed Index ───
        if self._fg_enabled:
            try:
                fg = await self._fetch_fear_greed()
                if fg:
                    self._fear_greed = fg
                    sources_ok += 1
            except Exception as e:
                logger.debug("fear_greed_fetch_error", extra={"error": str(e)})

        # ─── 2. TradingView Technical Ratings ───
        if self._tv_enabled and symbols:
            try:
                ratings = await self._fetch_tv_ratings(symbols)
                if ratings:
                    self._tv_ratings.update(ratings)
                    sources_ok += 1
            except Exception as e:
                logger.debug("tv_ratings_fetch_error", extra={"error": str(e)})

        # ─── 3. Social Sentiment ───
        if self._social_enabled and symbols:
            try:
                scores = await self._fetch_social_scores(symbols)
                if scores:
                    self._social_scores.update(scores)
                    sources_ok += 1
            except Exception as e:
                logger.debug("social_sentiment_fetch_error", extra={"error": str(e)})

        # ─── Compose final sentiment per symbol ───
        if symbols:
            for sym in symbols:
                self._sentiments[sym] = self._compose(sym, sources_ok)

                # Persist to brain.db
                if self.memory:
                    try:
                        self.memory.save_sentiment(sym, self._sentiments[sym])
                    except Exception:
                        pass

        if sources_ok > 0:
            logger.info("sentiment_refreshed", extra={
                "sources_ok": sources_ok,
                "symbols": len(symbols or []),
                "fg_value": self._fear_greed.get("value", "N/A"),
            })

        return sources_ok

    # ────────────────────────────────────────────────────────────────
    # get_sentiment() — ดึงผลลัพธ์ sentiment
    # ────────────────────────────────────────────────────────────────

    def get_sentiment(self, symbol: str) -> MarketSentiment:
        """ดึง cached sentiment สำหรับ symbol. ถ้าไม่มี → return neutral."""
        return self._sentiments.get(symbol, MarketSentiment())

    def get_all_sentiments(self) -> dict[str, dict]:
        """ดึง sentiment ทุก symbol (สำหรับ API dashboard)."""
        return {sym: s.to_dict() for sym, s in self._sentiments.items()}

    # ────────────────────────────────────────────────────────────────
    # Internal: fetch data from each source
    # ────────────────────────────────────────────────────────────────

    async def _fetch_fear_greed(self) -> dict:
        """Fetch Fear & Greed Index จาก alternative.me."""
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(FEAR_GREED_URL)
            resp.raise_for_status()
            data = resp.json()

        if "data" in data and len(data["data"]) > 0:
            entry = data["data"][0]
            return {
                "value": int(entry.get("value", 50)),
                "label": entry.get("value_classification", "Neutral"),
                "timestamp": entry.get("timestamp", ""),
            }
        return {}

    async def _fetch_tv_ratings(self, symbols: list[str]) -> dict[str, dict]:
        """Fetch TradingView technical analysis ratings."""
        # Map symbols to TV format
        tv_symbols = []
        sym_map = {}
        for sym in symbols:
            tv_sym = TV_SYMBOL_MAP.get(sym)
            if tv_sym:
                tv_symbols.append(tv_sym)
                sym_map[tv_sym] = sym

        if not tv_symbols:
            return {}

        # TradingView Scanner API — public endpoint
        payload = {
            "symbols": {"tickers": tv_symbols},
            "columns": [
                "Recommend.All",      # Overall recommendation (-1 to 1)
                "Recommend.MA",       # Moving Average recommendation
                "Recommend.Other",    # Oscillators recommendation
            ],
        }

        results = {}
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.post(TV_RATINGS_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()

            for item in data.get("data", []):
                tv_sym = item.get("s", "")
                values = item.get("d", [])
                if tv_sym in sym_map and len(values) >= 1:
                    score = float(values[0]) if values[0] is not None else 0.0
                    rating = self._score_to_rating(score)
                    orig_sym = sym_map[tv_sym]
                    results[orig_sym] = {"rating": rating, "score": score}
        except Exception as e:
            logger.debug("tv_scan_api_error", extra={"error": str(e)})

        return results

    async def _fetch_social_scores(self, symbols: list[str]) -> dict[str, float]:
        """Fetch social sentiment scores (lazy-init SocialSentiment)."""
        if self._social_sentiment is None:
            from app.brain.social_sentiment import SocialSentiment
            self._social_sentiment = SocialSentiment()

        return await self._social_sentiment.get_scores(symbols)

    # ────────────────────────────────────────────────────────────────
    # Internal: compose MarketSentiment from all sources
    # ────────────────────────────────────────────────────────────────

    def _compose(self, symbol: str, sources_ok: int) -> MarketSentiment:
        """รวม scores จากทุก source เป็น composite."""
        # Fear & Greed → normalize to -1.0 to 1.0
        fg_value = self._fear_greed.get("value", 50)
        fg_label = self._fear_greed.get("label", "Neutral")
        fg_normalized = (fg_value - 50) / 50.0  # 0→-1, 50→0, 100→+1

        # TradingView
        tv_data = self._tv_ratings.get(symbol, {})
        tv_rating = tv_data.get("rating", "NEUTRAL")
        tv_score = tv_data.get("score", 0.0)

        # Social
        social_score = self._social_scores.get(symbol, 0.0)

        # ─── Weighted composite ───
        # 40% TradingView (most directly relevant)
        # 30% Fear & Greed (broad market mood)
        # 30% Social (crowd wisdom)
        weights = []
        scores = []

        if tv_score != 0.0:
            weights.append(0.4)
            scores.append(tv_score)
        if fg_value != 50:
            weights.append(0.3)
            scores.append(fg_normalized)
        if social_score != 0.0:
            weights.append(0.3)
            scores.append(social_score)

        if weights:
            total_weight = sum(weights)
            composite = sum(w * s for w, s in zip(weights, scores)) / total_weight
        else:
            composite = 0.0

        return MarketSentiment(
            fear_greed_value=fg_value,
            fear_greed_label=fg_label,
            tv_rating=tv_rating,
            tv_score=round(tv_score, 3),
            social_score=round(social_score, 3),
            composite_score=round(composite, 3),
            sources_available=sources_ok,
            updated_at=datetime.now(timezone.utc),
        )

    @staticmethod
    def _score_to_rating(score: float) -> str:
        """Map TradingView score to rating label."""
        if score >= 0.5:
            return "STRONG_BUY"
        elif score >= 0.1:
            return "BUY"
        elif score <= -0.5:
            return "STRONG_SELL"
        elif score <= -0.1:
            return "SELL"
        return "NEUTRAL"
