"""
SocialSentiment — Scrape sentiment จาก social media (Reddit, Twitter).

แหล่งข้อมูล:
    1. Reddit — r/forex, r/wallstreetbets, r/Gold (JSON API, ไม่ต้องใช้ key)
    2. Twitter/X — Nitter RSS feeds (public instances, ไม่ต้อง API key)

เทคนิค:
    - Keyword-based scoring (ไม่ใช้ LLM — ประหยัด RAM)
    - นับ bullish/bearish keywords → คำนวณ score
    - Cache ผลลัพธ์ 15 นาที
    - ถ้า source ล้มเหลว → return neutral (0.0)

กฎ RAM (8GB mode):
    - ไม่เก็บ full text ใน memory
    - เก็บแค่ scores ล่าสุด
    - Rate limit: max 1 req/source/15 min

Safety:
    - ผลลัพธ์เป็น advisor เท่านั้น
    - ไม่ override Risk Engine
    - timeout 5 วินาที/request
"""

import re
import time
from datetime import datetime, timezone

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ─── Config ───
HTTP_TIMEOUT = 5.0
CACHE_SECONDS = 900  # 15 minutes
MAX_POSTS_PER_SOURCE = 25  # limit posts to process

# ─── Keyword dictionaries ───
BULLISH_KEYWORDS = {
    "bull", "bullish", "buy", "long", "moon", "pump", "breakout",
    "rally", "surge", "support", "bounce", "recovery", "uptrend",
    "higher", "accumulate", "golden cross", "dip buy", "strong",
    "opportunity", "undervalued", "growth", "calls",
}

BEARISH_KEYWORDS = {
    "bear", "bearish", "sell", "short", "crash", "dump", "breakdown",
    "resistance", "decline", "drop", "correction", "downtrend",
    "lower", "distribution", "death cross", "overvalued", "puts",
    "recession", "risk", "bubble", "falling", "plunge", "tank",
}

# ─── Symbol-to-keyword mapping for relevance filtering ───
SYMBOL_KEYWORDS = {
    "XAUUSDc": ["gold", "xauusd", "xau", "precious metal", "bullion"],
    "XAUUSD": ["gold", "xauusd", "xau", "precious metal", "bullion"],
    "EURUSDc": ["eurusd", "eur", "euro", "ecb", "european"],
    "EURUSD": ["eurusd", "eur", "euro", "ecb", "european"],
    "GBPUSDc": ["gbpusd", "gbp", "pound", "sterling", "boe"],
    "GBPUSD": ["gbpusd", "gbp", "pound", "sterling", "boe"],
    "USDJPYc": ["usdjpy", "jpy", "yen", "boj", "japanese"],
    "USDJPY": ["usdjpy", "jpy", "yen", "boj", "japanese"],
    "AUDUSDc": ["audusd", "aud", "aussie", "rba", "australian"],
    "AUDUSD": ["audusd", "aud", "aussie", "rba", "australian"],
    "USDCADc": ["usdcad", "cad", "loonie", "canadian", "boc"],
    "USDCAD": ["usdcad", "cad", "loonie", "canadian", "boc"],
    "NZDUSDc": ["nzdusd", "nzd", "kiwi", "rbnz", "new zealand"],
    "NZDUSD": ["nzdusd", "nzd", "kiwi", "rbnz", "new zealand"],
    "BTCUSDc": ["bitcoin", "btc", "btcusd", "crypto", "satoshi"],
    "BTCUSD": ["bitcoin", "btc", "btcusd", "crypto", "satoshi"],
}

# ─── Reddit subreddits per asset type ───
REDDIT_SUBS = {
    "forex": ["forex", "Forex_Rates"],
    "gold": ["Gold", "Silverbugs", "WallStreetSilver"],
    "crypto": ["Bitcoin", "CryptoCurrency", "CryptoMarkets"],
    "general": ["wallstreetbets", "stocks", "investing"],
}

SYMBOL_SUBREDDIT_MAP = {
    "XAUUSDc": ["gold", "forex", "general"],
    "XAUUSD": ["gold", "forex", "general"],
    "EURUSDc": ["forex", "general"],
    "EURUSD": ["forex", "general"],
    "GBPUSDc": ["forex", "general"],
    "GBPUSD": ["forex", "general"],
    "USDJPYc": ["forex", "general"],
    "USDJPY": ["forex", "general"],
    "AUDUSDc": ["forex", "general"],
    "AUDUSD": ["forex", "general"],
    "USDCADc": ["forex", "general"],
    "USDCAD": ["forex", "general"],
    "NZDUSDc": ["forex", "general"],
    "NZDUSD": ["forex", "general"],
    "BTCUSDc": ["crypto", "general"],
    "BTCUSD": ["crypto", "general"],
}

# ─── Nitter instances (public, may rotate) ───
NITTER_INSTANCES = [
    "https://nitter.poast.org",
    "https://nitter.privacydev.net",
]


class SocialSentiment:
    """
    Social media sentiment scraper (Reddit + Twitter).

    ผลลัพธ์: score -1.0 (bearish) to +1.0 (bullish) per symbol.
    ถ้า source ล้มเหลว → return 0.0 (neutral).
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, float]] = {}  # symbol → (score, timestamp)
        self._reddit_cache: dict[str, tuple[list, float]] = {}  # sub_key → (posts, timestamp)

    async def get_scores(self, symbols: list[str]) -> dict[str, float]:
        """
        Fetch social sentiment scores สำหรับทุก symbol.

        Returns:
            dict: {symbol: score} โดย score อยู่ระหว่าง -1.0 ถึง 1.0
        """
        results = {}
        now = time.monotonic()

        for symbol in symbols:
            # Check cache
            cached = self._cache.get(symbol)
            if cached and (now - cached[1]) < CACHE_SECONDS:
                results[symbol] = cached[0]
                continue

            try:
                score = await self._score_symbol(symbol)
                self._cache[symbol] = (score, now)
                results[symbol] = score
            except Exception as e:
                logger.debug("social_score_error", extra={
                    "symbol": symbol, "error": str(e),
                })
                results[symbol] = 0.0

        return results

    async def _score_symbol(self, symbol: str) -> float:
        """คำนวณ sentiment score สำหรับ 1 symbol."""
        all_texts = []

        # ─── 1. Reddit ───
        sub_categories = SYMBOL_SUBREDDIT_MAP.get(symbol, ["general"])
        for cat in sub_categories:
            subs = REDDIT_SUBS.get(cat, [])
            for sub in subs[:2]:  # max 2 subs per category
                try:
                    posts = await self._fetch_reddit(sub)
                    all_texts.extend(posts)
                except Exception:
                    pass

        # ─── 2. Twitter/Nitter (best-effort) ───
        sym_keywords = SYMBOL_KEYWORDS.get(symbol, [])
        if sym_keywords:
            try:
                tweets = await self._fetch_nitter(sym_keywords[0])
                all_texts.extend(tweets)
            except Exception:
                pass

        if not all_texts:
            return 0.0

        # ─── Filter by relevance ───
        relevance_keywords = set(SYMBOL_KEYWORDS.get(symbol, []))
        if relevance_keywords:
            relevant_texts = []
            for text in all_texts:
                lower = text.lower()
                if any(kw in lower for kw in relevance_keywords):
                    relevant_texts.append(text)
            # ถ้าไม่มี relevant → ใช้ทั้งหมด (general sentiment)
            if relevant_texts:
                all_texts = relevant_texts

        # ─── Score ───
        return self._compute_score(all_texts)

    async def _fetch_reddit(self, subreddit: str) -> list[str]:
        """Fetch hot posts จาก Reddit (public JSON, ไม่ต้อง API key)."""
        now = time.monotonic()

        # Check cache
        cached = self._reddit_cache.get(subreddit)
        if cached and (now - cached[1]) < CACHE_SECONDS:
            return cached[0]

        url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={MAX_POSTS_PER_SOURCE}"
        headers = {"User-Agent": "AntigravityTrader/1.0 (market research)"}

        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        texts = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            title = post.get("title", "")
            selftext = post.get("selftext", "")[:200]  # limit text length
            if title:
                texts.append(f"{title} {selftext}".strip())

        self._reddit_cache[subreddit] = (texts, now)
        return texts

    async def _fetch_nitter(self, search_term: str) -> list[str]:
        """Fetch tweets via Nitter RSS (public, best-effort)."""
        for instance in NITTER_INSTANCES:
            try:
                url = f"{instance}/search/rss?f=tweets&q={search_term}&e-nativeretweets=on"
                async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        # Parse RSS XML (simple regex extraction)
                        return self._parse_rss_titles(resp.text)
            except Exception:
                continue
        return []

    def _parse_rss_titles(self, rss_xml: str) -> list[str]:
        """Extract titles from RSS XML (simple regex, no lxml dependency)."""
        titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", rss_xml)
        if not titles:
            titles = re.findall(r"<title>(.*?)</title>", rss_xml)
        return titles[:MAX_POSTS_PER_SOURCE]

    def _compute_score(self, texts: list[str]) -> float:
        """
        Keyword-based sentiment scoring.

        Returns:
            float: -1.0 (strong bearish) to +1.0 (strong bullish)
        """
        if not texts:
            return 0.0

        total_bullish = 0
        total_bearish = 0

        for text in texts:
            words = set(text.lower().split())
            # Also check 2-word phrases
            text_lower = text.lower()

            for kw in BULLISH_KEYWORDS:
                if " " in kw:
                    if kw in text_lower:
                        total_bullish += 1
                elif kw in words:
                    total_bullish += 1

            for kw in BEARISH_KEYWORDS:
                if " " in kw:
                    if kw in text_lower:
                        total_bearish += 1
                elif kw in words:
                    total_bearish += 1

        total = total_bullish + total_bearish
        if total == 0:
            return 0.0

        # Score = (bull - bear) / total, capped to [-1, 1]
        score = (total_bullish - total_bearish) / total
        return max(-1.0, min(1.0, score))
