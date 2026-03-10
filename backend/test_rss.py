import asyncio
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from app.brain.web_researcher import WebResearcher

async def main():
    researcher = WebResearcher()
    print("Fetching XAUUSDc...")
    knowledge = await researcher.search_market_knowledge("XAUUSDc")
    print(f"Articles: {len(knowledge.articles)}")
    print(f"Sentiment: {knowledge.overall_sentiment}")
    print(f"Direction: {knowledge.consensus_direction}")
    for i, a in enumerate(knowledge.articles[:3]):
        print(f" {i+1}. {a.title} (score: {a.score})")

if __name__ == "__main__":
    asyncio.run(main())
