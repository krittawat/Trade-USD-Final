"""
Verify Auto Coach Logic (Behavior Detection).
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.append(str(Path(__file__).parents[3]))

from app.brain.auto_coach import AutoCoach, SessionData

def test_auto_coach():
    print("Testing Auto Coach...")
    coach = AutoCoach()

    # 1. Mock Data: Revenge Trading & Martingale
    print("\nCase 1: Revenge Trading & Martingale")
    trades = [
        {"id": 1, "pnl": -50.0, "lot_size": 0.1, "bars": 10},
        {"id": 2, "pnl": -100.0, "lot_size": 0.2, "bars": 5}, # Loss, Double Lot
        {"id": 3, "pnl": -200.0, "lot_size": 0.4, "bars": 2}, # Loss, Double Lot (Martingale)
        {"id": 4, "pnl": 10.0, "lot_size": 0.1, "bars": 20},
    ]
    
    session = SessionData(
        trades=trades,
        symbol="EURUSD",
        starting_balance=1000,
        ending_balance=660,
        max_drawdown_pct=34.0,
        max_drawdown_usd=350.0
    )
    
    report = coach.analyze(session)
    
    # Check Martingale Detection
    martingale = report.behavioral_violations.get("martingale_detected", {})
    if martingale.get("detected"):
        print("  PASS: Martingale detected.")
        print(f"  Instances: {len(martingale['instances'])}")
    else:
        print("  FAIL: Martingale NOT detected.")
        
    # Check Revenge Trading
    revenge = report.behavioral_violations.get("revenge_trading", {})
    if revenge.get("detected"):
         print("  PASS: Revenge Trading detected.")
    else:
         print("  FAIL: Revenge Trading NOT detected.")

    # Check Recommendations
    print("\nRecommendations:")
    corrections = report.coach_recommendations.get("rule_corrections", [])
    for c in corrections:
        print(f"  [{c['type']}] {c['rule']}")

    # 2. Mock Data: Overtrading
    print("\nCase 2: Overtrading")
    trades_over = [
        {"id": 10, "pnl": 10, "bars": 0},
        {"id": 11, "pnl": -5, "bars": 1}, # Overtrade
        {"id": 12, "pnl": 10, "bars": 1}, # Overtrade
        {"id": 15, "pnl": 10, "bars": 10},
    ]
    session_over = SessionData(trades=trades_over, symbol="GBPUSD", starting_balance=1000, ending_balance=1015)
    report_over = coach.analyze(session_over)
    
    overtrade = report_over.behavioral_violations.get("overtrading", {})
    if overtrade.get("detected"):
         print(f"  PASS: Overtrading detected ({overtrade['count']} counts).")
    else:
         print("  FAIL: Overtrading NOT detected.")

    # 3. Report Generation
    print("\nReport Preview:")
    text_report = coach.format_report_text(report)
    print(text_report[:200] + "...")

if __name__ == "__main__":
    test_auto_coach()
