import sys
from pathlib import Path
from datetime import datetime, timezone

# Add backend to path
sys.path.append(str(Path(__file__).resolve().parents[2]))

from app.services.session import get_current_session
from app.domain.enums import MarketSession

def test_session_logic():
    print("--- Testing Session Logic ---")
    
    # 1. Monday 12:00 UTC (Should be LONDON or NEW_YORK or OVERLAP)
    monday = datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    session = get_current_session(monday)
    print(f"Monday 12:00 UTC: {session.value}")
    assert session in [MarketSession.NEW_YORK, MarketSession.LONDON, MarketSession.OVERLAP]

    # 2. Saturday 12:00 UTC (Should be WEEKEND)
    saturday = datetime(2026, 3, 7, 12, 0, tzinfo=timezone.utc)
    session = get_current_session(saturday)
    print(f"Saturday 12:00 UTC: {session.value}")
    assert session == MarketSession.WEEKEND

    # 3. Sunday 10:00 UTC (Should be WEEKEND)
    sunday_morning = datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc)
    session = get_current_session(sunday_morning)
    print(f"Sunday 10:00 UTC: {session.value}")
    assert session == MarketSession.WEEKEND

    # 4. Sunday 23:00 UTC (Should be CLOSED or ASIA, but NOT WEEKEND)
    sunday_night = datetime(2026, 3, 8, 23, 0, tzinfo=timezone.utc)
    session = get_current_session(sunday_night)
    print(f"Sunday 23:00 UTC: {session.value}")
    assert session != MarketSession.WEEKEND

    print("\n✅ All Session Logic Tests Passed!")

if __name__ == "__main__":
    test_session_logic()
