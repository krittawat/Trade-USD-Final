"""
Verification Script for Phase D: Dashboard Data
Test the /api/status endpoint response for:
1. Regime Context (Actionable, Score)
2. Risk Metrics (Loss Streak, Win Streak, Max DD)
"""

import sys
import os
import asyncio
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

# Adjust path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../backend"))

from app.api.main import app
from app.api.routes import status
from app.master_loop import MasterLoop
from app.domain.models import RegimeContext, RegimeType

client = TestClient(app)

def test_dashboard_api_structure():
    # 1. Mock MasterLoop State
    mock_loop = MagicMock(spec=MasterLoop)
    
    # Mock Coach Report
    mock_loop.latest_coach_report = {
        "performance_summary": {
            "current_loss_streak": 2,
            "current_win_streak": 0,
            "max_drawdown_pct": 6.5
        }
    }
    
    # Mock Regime Contexts
    mock_loop.regime_contexts = {
        "XAUUSD": RegimeContext(
            regime=RegimeType.TRENDING_UP,
            actionable=True,
            score=0.85,
            reason="Strong Trend",
            details={"adx": 35.0}
        )
    }
    
    # Inject into app state
    app.state.master_loop = mock_loop
    
    # 2. Call API
    response = client.get("/api/status")
    data = response.json()
    
    print("\n--- API Response Verification ---")
    
    # Check Risk Metrics
    risk = data.get("risk_metrics", {})
    print(f"Risk Metrics: {risk}")
    assert risk["loss_streak"] == 2
    assert risk["max_dd_pct"] == 6.5
    print("[PASS] Risk Metrics present")
    
    # Check Regime
    regimes = data.get("regimes", {})
    print(f"Regimes: {regimes}")
    assert "XAUUSD" in regimes
    xau = regimes["XAUUSD"]
    assert xau["actionable"] == True
    assert xau["score"] == 0.85
    print("[PASS] Regime Context present")
    
    print("\n✅ Dashboard Data Structure Verified")

if __name__ == "__main__":
    test_dashboard_api_structure()
