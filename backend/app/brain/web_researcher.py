"""
WebResearcher — ดึงข่าวการเงินจาก RSS feeds เพื่อวิเคราะห์ sentiment.

หน้าที่:
    1. ดึง headlines จาก RSS feeds หลายแหล่ง (Investing.com, FXStreet, DailyFX)
    2. วิเคราะห์ sentiment ของ headlines (bullish/bearish/neutral)
    3. สร้าง consensus direction สำหรับแต่ละ symbol
    4. ให้ confidence modifier สำหรับใช้ก่อนเปิดเทรด

กฎ RAM (8GB mode):
    - ไม่เก็บ full text ใน memory — เก็บแค่ headline + score
    - Cache ผลลัพธ์ 30 นาที
    - ถ้า fetch ไม่ได้ → return neutral (modifier = 1.0)

Safety:
    - ผลลัพธ์เป็น advisor เท่านั้น — ไม่ override Risk Engine
    - modifier ±10% max (0.90 to 1.10)
    - timeout 5 วินาที/request
"""

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── Config ───
HTTP_TIMEOUT = 5.0
CACHE_SECONDS = 1800  # 30 minutes
MAX_HEADLINES = 30

# ─── RSS Feed Sources ───
# ─── RSS Feed Sources ───
# Expanded with multiple sources for reliability
RSS_FEEDS = {
    "XAUUSD": [
        "https://news.google.com/rss/search?q=gold+market+news+today&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=XAUUSD+forecast+analysis&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F",
    ],
    "XAUUSDc": [
        "https://news.google.com/rss/search?q=gold+market+news+today&hl=en-US&gl=US&ceid=US:en",
        "https://news.google.com/rss/search?q=XAUUSD+forecast+analysis&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GC=F",
    ],
    "EURUSD": [
        "https://news.google.com/rss/search?q=EURUSD+euro+dollar+today&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=EURUSD=X",
    ],
    "EURUSDc": [
        "https://news.google.com/rss/search?q=EURUSD+euro+dollar+today&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=EURUSD=X",
    ],
    "GBPUSD": [
        "https://news.google.com/rss/search?q=GBPUSD+pound+dollar+today&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GBPUSD=X",
    ],
    "GBPUSDc": [
        "https://news.google.com/rss/search?q=GBPUSD+pound+dollar+today&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=GBPUSD=X",
    ],
    "BTCUSD": [
        "https://news.google.com/rss/search?q=bitcoin+crypto+market+today&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD",
    ],
    "BTCUSDc": [
        "https://news.google.com/rss/search?q=bitcoin+crypto+market+today&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD",
    ],
    "USDJPY": [
        "https://news.google.com/rss/search?q=USDJPY+yen+dollar+today&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=JPY=X",
    ],
    "USDJPYc": [
        "https://news.google.com/rss/search?q=USDJPY+yen+dollar+today&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=JPY=X",
    ],
    "XAGUSDc": [
        "https://news.google.com/rss/search?q=silver+market+news+today&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SI=F",
    ],
    "XAGUSD": [
        "https://news.google.com/rss/search?q=silver+market+news+today&hl=en-US&gl=US&ceid=US:en",
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SI=F",
    ],
}

# ─── Keyword dictionaries (expanded for headlines) ───
BULLISH_WORDS = {
    "rally", "surge", "soar", "jump", "rise", "climb", "gain", "break out",
    "breakout", "record high", "new high", "bullish", "buy", "long",
    "recovery", "rebound", "upside", "strong", "optimism", "optimistic",
    "demand", "dovish", "stimulus", "easing", "cut rates", "rate cut",
    "safe haven", "inflation hedge", "all-time high", "ath", "momentum",
    "higher", "up", "above", "support holds", "accumulate",
}

BEARISH_WORDS = {
    "drop", "fall", "plunge", "crash", "sell-off", "selloff", "decline",
    "slide", "slip", "tumble", "bearish", "sell", "short", "sink",
    "correction", "pullback", "risk-off", "weakness", "weak",
    "recession", "hawkish", "tightening", "rate hike", "hike rates",
    "overvalued", "bubble", "collapse", "breakdown", "lower", "down",
    "below", "resistance holds", "distribution",
}


@dataclass
class HeadlineItem:
    """ข่าว headline 1 รายการ."""
    title: str
    source: str = ""
    score: float = 0.0  # -1 to +1
    published: str = ""


@dataclass
class MarketKnowledge:
    """ความรู้ตลาดสำหรับ 1 symbol จาก internet."""
    symbol: str
    articles: list[HeadlineItem] = field(default_factory=list)
    overall_sentiment: float = 0.0   # -1.0 to +1.0
    consensus_direction: str = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL
    bullish_count: int = 0
    bearish_count: int = 0
    neutral_count: int = 0
    last_updated: float = 0.0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "overall_sentiment": round(self.overall_sentiment, 3),
            "consensus_direction": self.consensus_direction,
            "bullish_count": self.bullish_count,
            "bearish_count": self.bearish_count,
            "neutral_count": self.neutral_count,
            "article_count": len(self.articles),
            "top_headlines": [
                {"title": a.title, "score": round(a.score, 2)}
                for a in self.articles[:5]
            ],
        }


class WebResearcher:
    """
    ดึงข่าวจาก RSS feeds → วิเคราะห์ sentiment → ให้ confidence modifier.

    วิธีใช้:
        researcher = WebResearcher()
        knowledge = await researcher.search_market_knowledge("XAUUSD")
        modifier, reason = researcher.get_web_confidence_modifier("XAUUSD", "BUY")
    """

    def __init__(self) -> None:
        self.knowledge_cache: dict[str, MarketKnowledge] = {}
        self._last_fetch: dict[str, float] = {}

    # ────────────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────────────

    async def search_market_knowledge(self, symbol: str) -> MarketKnowledge:
        """
        ดึงข่าวจาก RSS feeds สำหรับ symbol.

        Returns:
            MarketKnowledge — รวม headlines + sentiment score
        """
        now = time.monotonic()
        cached = self.knowledge_cache.get(symbol)
        if cached and (now - cached.last_updated) < CACHE_SECONDS:
            return cached

        headlines: list[HeadlineItem] = []

        # Fetch from RSS feeds
        feeds = RSS_FEEDS.get(symbol, [])
        if not feeds:
            # Fallback: generic forex/market search
            feeds = [
                "https://news.google.com/rss/search?q=forex+market+today&hl=en-US&gl=US&ceid=US:en"
            ]

        for feed_url in feeds:
            try:
                items = await self._fetch_rss(feed_url)
                headlines.extend(items)
            except Exception as e:
                logger.debug("rss_fetch_error", extra={
                    "symbol": symbol, "url": feed_url[:60], "error": str(e),
                })

        # Score each headline
        bullish = 0
        bearish = 0
        neutral = 0
        for item in headlines[:MAX_HEADLINES]:
            item.score = self._score_headline(item.title)
            if item.score > 0.1:
                bullish += 1
            elif item.score < -0.1:
                bearish += 1
            else:
                neutral += 1

        # Overall sentiment
        total = bullish + bearish + neutral
        if total > 0:
            overall = (bullish - bearish) / total
        else:
            overall = 0.0

        # Consensus direction
        if overall > 0.2:
            direction = "BULLISH"
        elif overall < -0.2:
            direction = "BEARISH"
        else:
            direction = "NEUTRAL"

        knowledge = MarketKnowledge(
            symbol=symbol,
            articles=headlines[:MAX_HEADLINES],
            overall_sentiment=overall,
            consensus_direction=direction,
            bullish_count=bullish,
            bearish_count=bearish,
            neutral_count=neutral,
            last_updated=now,
        )
        self.knowledge_cache[symbol] = knowledge
        self._last_fetch[symbol] = now

        logger.info("web_knowledge_updated", extra={
            "symbol": symbol,
            "articles": len(headlines),
            "sentiment": round(overall, 3),
            "direction": direction,
        })

        return knowledge

    def get_web_confidence_modifier(
        self, symbol: str, direction: str
    ) -> tuple[float, str]:
        """
        คำนวณ confidence modifier จาก web sentiment.

        Rules:
            - ถ้า web sentiment สอดคล้องกับ direction → boost 1.05-1.10
            - ถ้า web sentiment ขัดกับ direction → reduce 0.90-0.95
            - ถ้าไม่มีข้อมูล → neutral 1.0

        Returns:
            (modifier, reason) — ใช้คูณ confidence ก่อนเทรด
        """
        knowledge = self.knowledge_cache.get(symbol)
        if not knowledge or not knowledge.articles:
            return 1.0, "No web data"

        sentiment = knowledge.overall_sentiment
        consensus = knowledge.consensus_direction

        # ถ้า web BULLISH + ทิศทาง BUY = aligned → boost
        # ถ้า web BEARISH + ทิศทาง BUY = opposed → reduce
        direction_upper = direction.upper()
        is_aligned = (
            (consensus == "BULLISH" and direction_upper == "BUY")
            or (consensus == "BEARISH" and direction_upper == "SELL")
        )
        is_opposed = (
            (consensus == "BULLISH" and direction_upper == "SELL")
            or (consensus == "BEARISH" and direction_upper == "BUY")
        )

        if is_aligned:
            # Boost proportional to sentiment strength (max +10%)
            modifier = 1.0 + min(0.10, abs(sentiment) * 0.15)
            reason = f"Web {consensus} aligns with {direction_upper} ({sentiment:+.2f})"
        elif is_opposed:
            # Reduce proportional to sentiment strength (max -10%)
            modifier = 1.0 - min(0.10, abs(sentiment) * 0.15)
            reason = f"Web {consensus} opposes {direction_upper} ({sentiment:+.2f})"
        else:
            modifier = 1.0
            reason = f"Web neutral ({sentiment:+.2f})"

        return round(modifier, 3), reason

    def get_knowledge_summary(self) -> dict:
        """สรุปข้อมูล knowledge ทั้งหมดที่ cache อยู่."""
        return {
            "symbols_cached": list(self.knowledge_cache.keys()),
            "total_articles": sum(
                len(k.articles) for k in self.knowledge_cache.values()
            ),
            "sentiments": {
                sym: {
                    "score": round(k.overall_sentiment, 3),
                    "direction": k.consensus_direction,
                }
                for sym, k in self.knowledge_cache.items()
            },
        }

    def get_status(self) -> dict:
        """สถานะของ WebResearcher."""
        return {
            "active": True,
            "symbols_tracked": len(self.knowledge_cache),
            "cache_age_seconds": {
                sym: round(time.monotonic() - k.last_updated, 0)
                for sym, k in self.knowledge_cache.items()
            },
        }

    # ────────────────────────────────────────────────────────────────
    # Internal
    # ────────────────────────────────────────────────────────────────

    async def _fetch_rss(self, url: str) -> list[HeadlineItem]:
        """Fetch RSS feed and extract headlines (regex parser, no lxml)."""
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
                "Accept-Language": "en-US,en;q=0.9",
            })
            resp.raise_for_status()

        text = resp.text
        items: list[HeadlineItem] = []

        # Parse <item><title>...</title></item> from RSS XML
        item_blocks = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
        for block in item_blocks[:MAX_HEADLINES]:
            title_match = re.search(
                r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block
            )
            source_match = re.search(r"<source.*?>(.*?)</source>", block)
            pub_match = re.search(r"<pubDate>(.*?)</pubDate>", block)

            if title_match:
                title = title_match.group(1).strip()
                # Remove HTML entities
                title = (
                    title.replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .replace("&quot;", '"')
                    .replace("&#39;", "'")
                )
                items.append(HeadlineItem(
                    title=title,
                    source=source_match.group(1) if source_match else "",
                    published=pub_match.group(1) if pub_match else "",
                ))

        return items

    def _score_headline(self, title: str) -> float:
        """คำนวณ sentiment score สำหรับ headline 1 รายการ."""
        lower = title.lower()
        words = set(lower.split())

        bull = 0
        bear = 0

        for kw in BULLISH_WORDS:
            if " " in kw:
                if kw in lower:
                    bull += 1
            elif kw in words:
                bull += 1

        for kw in BEARISH_WORDS:
            if " " in kw:
                if kw in lower:
                    bear += 1
            elif kw in words:
                bear += 1

        total = bull + bear
        if total == 0:
            return 0.0

        return max(-1.0, min(1.0, (bull - bear) / total))
