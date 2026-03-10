"""
Test Auto Coach AI — unit tests with synthetic trade data.

Tests:
    1. Behavioral diagnostics (overtrading, revenge trading detection)
    2. Regime fit analysis (loss concentration in RANGING)
    3. Execution quality (SL noise detection)
    4. Report JSON structure (all required keys present)
    5. Edge cases (no trades, single trade, all winners)

No MT5 / DB dependency — uses synthetic data only.
"""

import sys
from pathlib import Path

# Ensure backend is on path
backend_root = Path(__file__).resolve().parent.parent.parent
if str(backend_root) not in sys.path:
    sys.path.insert(0, str(backend_root))

import pytest

from app.brain.auto_coach import AutoCoach, SessionData, CoachReport


# ════════════════════════════════════════
# Helpers
# ════════════════════════════════════════

def make_trade(
    trade_id: int,
    pnl: float,
    regime: str = "TRENDING_UP",
    reason: str = "TP",
    bars: int = 10,
    rr: float = 1.0,
    sl: float = 100.0,
    tp: float = 110.0,
    entry: float = 105.0,
    exit_price: float = 110.0,
    action: str = "BUY",
    entry_time: str = "2023-10-27 10:00:00",
    lot_size: float = 0.01,
) -> dict:
    """Create a synthetic trade dict matching BacktestResult.trades format."""
    return {
        "id": trade_id,
        "action": action,
        "entry": entry,
        "exit": exit_price,
        "sl": sl,
        "tp": tp,
        "pnl": pnl,
        "rr": rr,
        "reason": reason,
        "bars": bars,
        "regime": regime,
        "entry_time": entry_time,
        "lot_size": lot_size,
    }


def make_session(trades: list[dict], **kwargs) -> SessionData:
    """Create a SessionData from trades list."""
    total_pnl = sum(t["pnl"] for t in trades)
    return SessionData(
        trades=trades,
        symbol=kwargs.get("symbol", "XAUUSDc"),
        strategy_name=kwargs.get("strategy_name", "TEST_STRATEGY"),
        starting_balance=kwargs.get("starting_balance", 10000.0),
        ending_balance=kwargs.get("ending_balance", 10000.0 + total_pnl),
        max_drawdown_pct=kwargs.get("max_drawdown_pct", 2.0),
        max_drawdown_usd=kwargs.get("max_drawdown_usd", 200.0),
    )


# ════════════════════════════════════════
# Test: Insufficient Data
# ════════════════════════════════════════

class TestInsufficientData:
    def test_no_trades(self):
        coach = AutoCoach()
        session = make_session([])
        report = coach.analyze(session)
        assert report.performance_summary["status"] == "ข้อมูลไม่เพียงพอ (INSUFFICIENT_DATA)"
        assert "ต้องการอย่างน้อย 3 ไม้" in report.performance_summary["message"]

    def test_single_trade(self):
        coach = AutoCoach()
        trade = make_trade(1, 10)
        session = make_session([trade])
        report = coach.analyze(session)
        assert report.performance_summary["status"] == "ข้อมูลไม่เพียงพอ (INSUFFICIENT_DATA)"

    def test_two_trades(self):
        coach = AutoCoach()
        t1 = make_trade(1, 10)
        t2 = make_trade(2, -5)
        session = make_session([t1, t2])
        report = coach.analyze(session)
        assert report.performance_summary["status"] == "ข้อมูลไม่เพียงพอ (INSUFFICIENT_DATA)"


# ════════════════════════════════════════
# Test: Performance Summary
# ════════════════════════════════════════

class TestPerformanceSummary:
    def test_basic_metrics(self):
        trades = [
            make_trade(1, 100.0),
            make_trade(2, -50.0),
            make_trade(3, 75.0),
            make_trade(4, -25.0),
            make_trade(5, 60.0),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)
        perf = report.performance_summary

        assert perf["status"] == "OK"
        assert perf["total_trades"] == 5
        assert perf["winning_trades"] == 3
        assert perf["losing_trades"] == 2
        assert perf["net_pnl"] == 160.0
        assert perf["win_rate"] == 60.0
        assert perf["profit_factor"] > 1.0

    def test_all_winners(self):
        trades = [make_trade(i, 50.0) for i in range(1, 6)]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)
        perf = report.performance_summary

        assert perf["winning_trades"] == 5
        assert perf["losing_trades"] == 0
        assert perf["win_rate"] == 100.0

    def test_all_losers(self):
        trades = [make_trade(i, -50.0) for i in range(1, 6)]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)
        perf = report.performance_summary

        assert perf["winning_trades"] == 0
        assert perf["losing_trades"] == 5
        assert perf["win_rate"] == 0.0


# ════════════════════════════════════════
# Test: Behavioral Diagnostics
# ════════════════════════════════════════

class TestBehavioralDiagnostics:
    def test_overtrading_detected(self):
        """Trades with consecutive IDs (within 3 bars) → overtrading."""
        trades = [
            make_trade(1, 50.0),
            make_trade(2, -30.0),   # gap = 1 bar → overtrading
            make_trade(3, -20.0),   # gap = 1 bar → overtrading
            make_trade(10, 40.0),   # gap = 7 bars → OK
            make_trade(11, -10.0),  # gap = 1 bar → overtrading
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        ot = report.behavioral_violations["overtrading"]
        assert ot["detected"] is True
        assert ot["count"] >= 2  # At least 2 clustered pairs

    def test_no_overtrading(self):
        """Trades spaced far apart → no overtrading."""
        trades = [
            make_trade(1, 50.0),
            make_trade(10, -30.0),   # gap = 9
            make_trade(20, -20.0),   # gap = 10
            make_trade(30, 40.0),    # gap = 10
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        ot = report.behavioral_violations["overtrading"]
        assert ot["detected"] is False

    def test_revenge_trading_detected(self):
        """2+ consecutive losses → trades during streak flagged as revenge."""
        trades = [
            make_trade(1, -50.0),
            make_trade(5, -30.0),   # 2nd loss → streak
            make_trade(10, -20.0),  # this is taken during streak → revenge
            make_trade(15, 80.0),   # win resets
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        rt = report.behavioral_violations["revenge_trading"]
        assert rt["detected"] is True
        assert rt["count"] >= 1

    def test_no_revenge_trading(self):
        """Alternating W/L → no revenge."""
        trades = [
            make_trade(1, 50.0),
            make_trade(5, -30.0),
            make_trade(10, 40.0),
            make_trade(15, -20.0),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        rt = report.behavioral_violations["revenge_trading"]
        assert rt["detected"] is False

    def test_strategy_drift_detected(self):
        """All trades in RANGING regime losing → drift detected."""
        trades = [
            make_trade(1, -50.0, regime="RANGING"),
            make_trade(5, -30.0, regime="RANGING"),
            make_trade(10, -20.0, regime="RANGING"),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        sd = report.behavioral_violations["strategy_drift"]
        assert sd["detected"] is True
        assert any(r["regime"] == "RANGING" for r in sd["losing_regimes"])


# ════════════════════════════════════════
# Test: Regime Fit Analysis
# ════════════════════════════════════════

class TestRegimeFit:
    def test_regime_breakdown(self):
        trades = [
            make_trade(1, 100.0, regime="TRENDING_UP"),
            make_trade(5, 80.0, regime="TRENDING_UP"),
            make_trade(10, -60.0, regime="RANGING"),
            make_trade(15, -40.0, regime="RANGING"),
            make_trade(20, 50.0, regime="HIGH_VOLATILITY"),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        rp = report.regime_performance
        assert "TRENDING_UP" in rp
        assert "RANGING" in rp
        assert rp["TRENDING_UP"]["wins"] == 2
        assert rp["RANGING"]["losses"] == 2

    def test_loss_concentration_warning(self):
        """Most losses in RANGING → concentration warning."""
        trades = [
            make_trade(1, 100.0, regime="TRENDING_UP"),
            make_trade(5, -80.0, regime="RANGING"),
            make_trade(10, -60.0, regime="RANGING"),
            make_trade(15, -50.0, regime="RANGING"),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        warnings = report.regime_performance.get("_loss_concentration_warning", [])
        assert len(warnings) > 0
        assert warnings[0]["regime"] == "RANGING"


# ════════════════════════════════════════
# Test: Execution Quality
# ════════════════════════════════════════

class TestExecutionQuality:
    def test_sl_noise_detection(self):
        """Multiple quick SL hits → stop_too_tight = True."""
        trades = [
            make_trade(1, -50.0, reason="SL", bars=1),
            make_trade(5, -30.0, reason="SL", bars=2),
            make_trade(10, -20.0, reason="SL", bars=1),
            make_trade(15, 80.0, reason="TP", bars=15),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        eq = report.execution_quality
        assert eq["sl_noise"]["stop_too_tight"] is True
        assert eq["sl_noise"]["quick_sl_hits"] == 3

    def test_adequate_sl(self):
        """No quick SL hits → stop adequate."""
        trades = [
            make_trade(1, -50.0, reason="SL", bars=10),
            make_trade(5, 80.0, reason="TP", bars=15),
            make_trade(10, -20.0, reason="SIGNAL_REVERSE", bars=8),
            make_trade(15, 60.0, reason="TP", bars=12),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        eq = report.execution_quality
        assert eq["sl_noise"]["stop_too_tight"] is False

    def test_exit_reason_distribution(self):
        trades = [
            make_trade(1, -50.0, reason="SL", bars=5),
            make_trade(5, 80.0, reason="TP", bars=10),
            make_trade(10, 60.0, reason="TP", bars=8),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        dist = report.execution_quality["exit_reason_distribution"]
        assert dist["SL"] == 1
        assert dist["TP"] == 2


# ════════════════════════════════════════
# Test: Risk Discipline
# ════════════════════════════════════════

class TestRiskDiscipline:
    def test_loss_streak_max(self):
        trades = [
            make_trade(1, -50.0),
            make_trade(5, -30.0),
            make_trade(10, -20.0),
            make_trade(15, 80.0),
            make_trade(20, -10.0),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        ls = report.risk_discipline["loss_streaks"]
        assert ls["max_streak"] == 3

    def test_no_loss_streak(self):
        trades = [
            make_trade(1, 50.0),
            make_trade(5, -30.0),
            make_trade(10, 40.0),
            make_trade(15, -20.0),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        ls = report.risk_discipline["loss_streaks"]
        assert ls["max_streak"] == 1


# ════════════════════════════════════════
# Test: Report JSON Structure
# ════════════════════════════════════════

class TestReportStructure:
    def test_json_keys(self):
        """Verify all required top-level keys are present."""
        trades = [
            make_trade(1, 100.0),
            make_trade(5, -50.0),
            make_trade(10, 75.0),
            make_trade(15, -25.0),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)
        json_out = report.to_dict()

        required_keys = [
            "performance_summary",
            "root_causes",
            "behavioral_violations",
            "regime_performance",
            "execution_quality",
            "risk_discipline",
            "coach_recommendations",
            "next_session_rules",
            "lockout_conditions",
            "analyzed_at",
        ]
        for key in required_keys:
            assert key in json_out, f"Missing key: {key}"

    def test_coach_recommendations_structure(self):
        trades = [
            make_trade(1, -50.0, regime="RANGING"),
            make_trade(5, -30.0, regime="RANGING"),
            make_trade(10, -20.0, regime="RANGING"),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        cr = report.coach_recommendations
        assert "rule_corrections" in cr
        assert "strategy_adjustments" in cr
        assert "regime_matrix" in cr
        assert "training_plan_7day" in cr

    def test_human_report_generation(self):
        trades = [
            make_trade(1, 100.0),
            make_trade(5, -50.0),
            make_trade(10, 75.0),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)
        text = coach.format_report_text(report)

        lines = text.split("\n")
        assert any("รายงานการเทรด" in line for line in lines)
        assert any("สรุปผลงาน" in line for line in lines)
        assert any("พฤติกรรมเสี่ยง" in line for line in lines)


# ════════════════════════════════════════
# Test: Enforcement Recommendations
# ════════════════════════════════════════

class TestEnforcement:
    def test_revenge_triggers_hard_block(self):
        """Revenge trading detected → cooldown hard block recommended."""
        trades = [
            make_trade(1, -50.0),
            make_trade(5, -30.0),
            make_trade(10, -20.0),  # taken during loss streak
            make_trade(15, 80.0),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        blocks = report.lockout_conditions.get("hard_blocks", [])
        if report.behavioral_violations.get("revenge_trading", {}).get("detected"):
            assert any(
                "COOLDOWN" in b.get("rule", "")
                for b in blocks
            ), "Expected COOLDOWN hard block for revenge trading"

    def test_low_winrate_triggers_lockout(self):
        """Win rate < 30% → lockout recommendation."""
        trades = [
            make_trade(1, -50.0),
            make_trade(5, -30.0),
            make_trade(10, 20.0),
            make_trade(15, -40.0),
            make_trade(20, -35.0),
            make_trade(25, -25.0),
            make_trade(30, -15.0),
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)

        lockouts = report.lockout_conditions.get("lockout_triggers", [])
        assert any(
            "win_rate" in lt.get("condition", "")
            for lt in lockouts
        ), "Expected lockout trigger for low win rate"


# ════════════════════════════════════════
# Test: Advanced Features (God-Tier)
# ════════════════════════════════════════

class TestAdvancedFeatures:
    def test_martingale_detection(self):
        """Increasing lot size after loss detected."""
        trades = [
            make_trade(1, -50.0, lot_size=0.1),
            make_trade(5, -100.0, lot_size=0.2), # Double down
            make_trade(10, -200.0, lot_size=0.4), # Quadruple down
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)
        
        martingale = report.behavioral_violations.get("martingale_detected", {})
        assert martingale.get("detected") is True
        assert martingale.get("count") == 2

    def test_hourly_analysis(self):
        """Hourly pnl aggregation."""
        trades = [
            make_trade(1, 100.0, entry_time="2023-10-27 09:05:00"),
            make_trade(2, 50.0, entry_time="2023-10-27 09:30:00"), # 09:00 total +150
            make_trade(3, -50.0, entry_time="2023-10-27 10:15:00"), # 10:00 total -50
        ]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)
        
        hourly = report.regime_performance.get("hourly_analysis", {})
        assert "09:00" in hourly
        assert hourly["09:00"]["total_pnl"] == 150.0
        assert "10:00" in hourly
        assert hourly["10:00"]["total_pnl"] == -50.0

    def test_session_score(self):
        """Score calculation logic."""
        # Perfect session - spaced out to avoid overtrading
        trades = [make_trade(i*10, 50.0) for i in range(1, 6)]
        coach = AutoCoach()
        session = make_session(trades)
        report = coach.analyze(session)
        
        score = report.performance_summary.get("session_score")
        assert score == 100
        assert report.performance_summary.get("score_rating") == "LEGENDARY"
        
        # Bad session (martingale + losses)
        trades_bad = [
            make_trade(1, -50.0, lot_size=0.1),
            make_trade(2, -100.0, lot_size=0.2), # Martingale (-25)
            make_trade(3, -200.0, lot_size=0.4), # Martingale (-25)
        ]
        session_bad = make_session(trades_bad)
        report_bad = coach.analyze(session_bad)
        
        score_bad = report_bad.performance_summary.get("session_score")
        assert score_bad < 60
        assert report_bad.performance_summary.get("score_rating") in ["GAMBLER", "AMATEUR"]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
