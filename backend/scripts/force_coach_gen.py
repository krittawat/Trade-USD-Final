import asyncio
import sys
import os
from pathlib import Path

# Add backend to sys.path
sys.path.append(str(Path(__file__).parent.parent.parent / "backend"))

from app.brain.auto_coach import AutoCoach, SessionData
from app.db.sqlite import SQLiteStore
from app.core.config import Settings

async def main():
    print("🚀 Forcing Auto Coach Report Generation (Thai)...")
    
    # Mock data to ensure we have something to report
    coach = AutoCoach()
    
    # Create a dummy session with some 'bad' behavior to trigger advice
    from datetime import datetime
    
    # Mock trades
    dummy_trades = [
        {"profit_usd": -50, "entry_time": "2024-02-12 10:00:00", "pnl": -50, "action": "BUY"},
        {"profit_usd": -50, "entry_time": "2024-02-12 10:01:00", "pnl": -50, "action": "BUY"}, # Violation: Revenge/Cooldown
        {"profit_usd": 100, "entry_time": "2024-02-12 10:05:00", "pnl": 100, "action": "SELL"},
    ]
    
    session = SessionData(
        trades=dummy_trades,
        symbol="XAUUSD",
        starting_balance=10000,
        ending_balance=10000
    )
    
    print("🧠 Analyzing session...")
    report = coach.analyze(session)
    
    # Generate Thai text
    report_text = coach.format_report_text(report)
    
    # Save to logs
    log_path = Path("d:/VibeCode/Trade/logs/coach_report.md")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(report_text, encoding="utf-8")
    
    print(f"✅ Report saved to: {log_path}")
    print("\nPreview:")
    print(report_text[:200] + "...")

if __name__ == "__main__":
    asyncio.run(main())
