import json
import os
import logging
from datetime import datetime
from pathlib import Path

# Placeholder for real web researcher integration
# In a real scenario, this would call a LLM or Scraper to get sentiment
def get_mock_sentiment(symbol):
    sentiment_map = {
        "XAUUSD": {"bias": "BULLISH", "score": 0.8, "reason": "Geopolitical tensions rising in Middle East."},
        "BTCUSD": {"bias": "BULLISH", "score": 0.6, "reason": "Institutional inflows increasing after ETF approval."},
        "USOIL": {"bias": "NEUTRAL", "score": 0.0, "reason": "OPEC+ production cuts balanced by US shale growth."},
        "US30": {"bias": "BEARISH", "score": -0.4, "reason": "High interest rates impacting industrial growth."},
    }
    return sentiment_map.get(symbol.replace("m", ""), {"bias": "NEUTRAL", "score": 0.0, "reason": "No clear narrative."})

def update_narrative():
    symbols = ["XAUUSD", "BTCUSD", "USOIL", "US30", "USTEC", "EURUSD", "GBPUSD", "USDJPY"]
    narrative = {}
    
    for sym in symbols:
        sentiment = get_mock_sentiment(sym)
        narrative[sym] = sentiment
        
    output_path = Path("d:/VibeCode/Trade/backend/data/market_narrative.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w") as f:
        json.dump({
            "updated_at": datetime.utcnow().isoformat(),
            "narrative": narrative
        }, f, indent=4)
    
    print(f"✅ Market narrative updated at {output_path}")

if __name__ == "__main__":
    update_narrative()
