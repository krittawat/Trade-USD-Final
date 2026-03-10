"""
Auto Coach AI — Quantitative Trading Psychologist + Performance Engineer.

Analyzes Replay/Backtest trading sessions and generates objective,
actionable coaching reports to correct trader behavior, discipline
failures, and strategy misuse.

Analysis Subsystems:
    1. Behavioral Diagnostics (overtrading, revenge, FOMO, drift)
    2. Market Regime Fit (per-regime win rate, expectancy, DD contribution)
    3. Execution Quality (R:R realized vs theoretical, SL noise, hold time)
    4. Risk & Discipline Metrics (loss streaks, emotional exposure, violations)
    5. Coaching Corrections (root causes, rule changes, training plan)
    6. Auto-Enforcement Recommendations (hard blocks, warnings, lockouts)
    7. Trader Personality Profiling (psychological classification)

Input:  BacktestResult.trades (list[dict]) + equity_curve + session metadata
Output: Machine-readable JSON for dashboard + human-readable report
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.core.logging import get_logger

logger = get_logger("AutoCoach")


# =====================================================================
# Data Containers
# =====================================================================

@dataclass
class SessionData:
    """Input container for a coaching analysis session.

    Accepts data directly from BacktestResult or replay CSV exports.
    """
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    symbol: str = ""
    timeframe: str = "M5"
    session: str = ""           # ASIA / LONDON / NEW_YORK
    starting_balance: float = 10000.0
    ending_balance: float = 10000.0
    max_drawdown_pct: float = 0.0
    max_drawdown_usd: float = 0.0
    strategy_name: str = ""



@dataclass
class CoachReport:
    """Full coaching report — both human-readable and machine-readable."""
    performance_summary: dict = field(default_factory=dict)
    root_causes: list[dict] = field(default_factory=list)
    behavioral_violations: dict = field(default_factory=dict)
    regime_performance: dict = field(default_factory=dict)
    execution_quality: dict = field(default_factory=dict)
    risk_discipline: dict = field(default_factory=dict)
    coach_recommendations: dict = field(default_factory=dict)
    next_session_rules: dict = field(default_factory=dict)
    lockout_conditions: dict = field(default_factory=dict)
    trader_personality: dict = field(default_factory=dict)
    analyzed_at: str = ""

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict for API/dashboard."""
        return {
            "performance_summary": self.performance_summary,
            "root_causes": self.root_causes,
            "behavioral_violations": self.behavioral_violations,
            "regime_performance": self.regime_performance,
            "execution_quality": self.execution_quality,
            "risk_discipline": self.risk_discipline,
            "coach_recommendations": self.coach_recommendations,
            "next_session_rules": self.next_session_rules,
            "lockout_conditions": self.lockout_conditions,
            "trader_personality": self.trader_personality,
            "analyzed_at": self.analyzed_at,
        }



# =====================================================================
# Auto Coach Engine
# =====================================================================

class AutoCoach:
    """
    Quantitative Trading Psychologist + Performance Engineer.

    Analyzes trades from Replay/Backtest sessions and generates
    data-backed, actionable coaching reports.

    Usage:
        coach = AutoCoach()
        session = SessionData(
            trades=backtest_result.trades,
            equity_curve=backtest_result.equity_curve,
            symbol="XAUUSDc",
            starting_balance=10000,
            ending_balance=10250,
        )
        report = coach.analyze(session)
        json_output = report.to_dict()
    """

    # ── Configurable thresholds ──
    OVERTRADE_BAR_WINDOW: int = 3       # trades within N bars = overtrading
    FOMO_ATR_MULT: float = 2.0          # entry after candle > N × ATR
    REVENGE_FREQ_MULT: float = 1.5      # freq increase factor = revenge
    SL_NOISE_MAX_BARS: int = 3          # SL hit within N bars = stop too tight
    MIN_TRADES_FOR_ANALYSIS: int = 1    # minimum trades to produce report

    def analyze(self, session: SessionData) -> CoachReport:
        """
        Run full coaching analysis on a trading session.

        Args:
            session: SessionData with trades, equity_curve, metadata

        Returns:
            CoachReport with all analysis sections populated
        """
        report = CoachReport(analyzed_at=datetime.now(timezone.utc).isoformat())

        if len(session.trades) < self.MIN_TRADES_FOR_ANALYSIS:
            report.performance_summary = {
                "status": "ข้อมูลไม่เพียงพอ (INSUFFICIENT_DATA)",
                "total_trades": len(session.trades),
                "message": f"ต้องการอย่างน้อย {self.MIN_TRADES_FOR_ANALYSIS} ไม้เพื่อวิเคราะห์",
            }
            logger.warning("coach_insufficient_data", extra={
                "trades": len(session.trades),
                "min_required": self.MIN_TRADES_FOR_ANALYSIS,
            })
            return report

        # ── 1. Performance Summary ──
        report.performance_summary = self._performance_summary(session)

        # ── 2. Behavioral Diagnostics ──
        report.behavioral_violations = self._behavioral_diagnostics(session)

        # ── 3. Market Regime Fit & Hourly Analysis ──
        report.regime_performance = self._regime_fit_analysis(session)
        report.regime_performance["hourly_analysis"] = self._analyze_hourly_performance(session)

        # ── 4. Execution Quality ──
        report.execution_quality = self._execution_quality(session)

        # ── 5. Risk, Discipline & Session Score ──
        report.risk_discipline = self._risk_discipline_metrics(session)
        
        # Calculate Session Quality Score (0-100)
        session_score = self._calculate_session_score(report)
        report.performance_summary["session_score"] = session_score
        report.performance_summary["score_rating"] = self._get_score_rating(session_score)

        # ── 6. Root Cause Analysis (ranked by $ impact) ──
        report.root_causes = self._root_cause_analysis(
            session, report.behavioral_violations,
            report.regime_performance, report.execution_quality,
        )

        # ── 7. Coaching Corrections ──
        report.coach_recommendations = self._generate_corrections(
            session, report,
        )

        # ── 8. Next Session Rules ──
        report.next_session_rules = self._generate_next_session_rules(report)

        # ── 9. Auto-Enforcement / Lockout ──
        report.lockout_conditions = self._generate_enforcement(report)

        # ── 10. Trader Personality Profiling ──
        report.trader_personality = self._determine_personality(session, report)

        logger.info("coach_analysis_complete", extra={
            "symbol": session.symbol,
            "trades": len(session.trades),
            "root_causes": len(report.root_causes),
            "violations": sum(
                v.get("count", 0)
                for v in report.behavioral_violations.values()
                if isinstance(v, dict)
            ),
        })

        return report

    # =================================================================
    # A) Performance Summary
    # =================================================================

    def _performance_summary(self, session: SessionData) -> dict:
        """Compute net PnL, max DD, win rate, PF, expectancy."""
        trades = session.trades
        total = len(trades)

        pnls = [t.get("pnl", 0.0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        net_pnl = sum(pnls)
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))

        win_rate = (len(wins) / total * 100) if total > 0 else 0.0
        pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
        expectancy = net_pnl / total if total > 0 else 0.0

        avg_win = (gross_profit / len(wins)) if wins else 0.0
        avg_loss = (gross_loss / len(losses)) if losses else 0.0

        return {
            "status": "OK",
            "symbol": session.symbol,
            "strategy": session.strategy_name,
            "total_trades": total,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "net_pnl": round(net_pnl, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "win_rate": round(win_rate, 1),
            "profit_factor": round(pf, 2) if pf != float("inf") else 999.99,
            "expectancy_per_trade": round(expectancy, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "max_drawdown_pct": round(session.max_drawdown_pct, 1),
            "max_drawdown_usd": round(session.max_drawdown_usd, 2),
            "starting_balance": session.starting_balance,
            "ending_balance": session.ending_balance,
            "current_loss_streak": self._calculate_current_streak(pnls, count_loss=True),
            "current_win_streak": self._calculate_current_streak(pnls, count_loss=False),
            "return_pct": round(
                (session.ending_balance - session.starting_balance)
                / session.starting_balance * 100, 2
            ) if session.starting_balance > 0 else 0.0,
        }

    def _calculate_current_streak(self, pnls: list[float], count_loss: bool) -> int:
        """Helper to calculate current win/loss streak."""
        streak = 0
        for p in reversed(pnls):
            if count_loss:
                if p < 0: streak += 1
                else: break
            else:
                if p > 0: streak += 1
                else: break
        return streak

    # =================================================================
    # B) Behavioral Diagnostics
    # =================================================================

    def _behavioral_diagnostics(self, session: SessionData) -> dict:
        """Detect overtrading, revenge trading, FOMO, strategy drift."""
        trades = session.trades
        results: dict[str, Any] = {}

        # ── Overtrading: trades within OVERTRADE_BAR_WINDOW bars ──
        overtrade_count = 0
        overtrade_pairs: list[dict] = []
        for i in range(1, len(trades)):
            prev_bar = trades[i - 1].get("bars", 0) + trades[i - 1].get("id", 0)
            curr_bar = trades[i].get("id", 0)
            # Use trade IDs as proxy for bar distance if entry_bar not available
            bar_gap = abs(curr_bar - trades[i - 1].get("id", 0))
            if bar_gap <= self.OVERTRADE_BAR_WINDOW and bar_gap > 0:
                overtrade_count += 1
                overtrade_pairs.append({
                    "trade_a": trades[i - 1].get("id"),
                    "trade_b": trades[i].get("id"),
                    "bar_gap": bar_gap,
                })

        results["overtrading"] = {
            "detected": overtrade_count > 0,
            "count": overtrade_count,
            "pct_of_trades": round(overtrade_count / max(len(trades) - 1, 1) * 100, 1),
            "instances": overtrade_pairs[:10],  # cap for payload size
            "impact_description": (
                f"เข้าออเดอร์เร็วเกินไป {overtrade_count} ไม้ ภายใน {self.OVERTRADE_BAR_WINDOW} "
                f"แท่งจากไม้ก่อนหน้า ({round(overtrade_count / max(len(trades) - 1, 1) * 100, 1)}% ของทั้งหมด)"
            ) if overtrade_count > 0 else "ไม่พบการ Overtrading (No overtrading detected)",
        }

        # ── Revenge Trading: higher frequency after consecutive losses ──
        revenge_count = 0
        loss_streak = 0
        trades_during_streak: list[dict] = []

        for i, t in enumerate(trades):
            pnl = t.get("pnl", 0.0)
            if pnl <= 0:
                loss_streak += 1
                if loss_streak >= 2 and i + 1 < len(trades):
                    # Next trade taken during active loss streak
                    revenge_count += 1
                    trades_during_streak.append({
                        "trade_id": trades[i + 1].get("id") if i + 1 < len(trades) else None,
                        "streak_length": loss_streak,
                        "prev_loss": round(pnl, 2),
                    })
            else:
                loss_streak = 0

        # PnL of revenge trades
        revenge_pnl = 0.0
        revenge_ids = {t["trade_id"] for t in trades_during_streak if t["trade_id"]}
        for t in trades:
            if t.get("id") in revenge_ids:
                revenge_pnl += t.get("pnl", 0.0)

        results["revenge_trading"] = {
            "detected": revenge_count > 0,
            "count": revenge_count,
            "total_pnl_impact": round(revenge_pnl, 2),
            "instances": trades_during_streak[:10],
            "impact_description": (
                f"พบการเทรดล้างแค้น {revenge_count} ไม้หลังขาดทุนต่อเนื่อง "
                f"เสียหายรวม: ${round(revenge_pnl, 2)}"
            ) if revenge_count > 0 else "ไม่พบการ Revenge Trading",
        }

        # ── FOMO: entries where previous bar had large range ──
        fomo_count = 0
        fomo_trades: list[dict] = []
        # Approximate: if rr < 0.5 and exit_reason == "SL" → likely chasing
        for t in trades:
            rr = t.get("rr", 0.0)
            reason = t.get("reason", "")
            if reason == "SL" and rr < -0.8:
                # Quick SL hit with bad R:R → likely chased entry
                bars = t.get("bars", 0)
                if bars <= self.SL_NOISE_MAX_BARS:
                    fomo_count += 1
                    fomo_trades.append({
                        "trade_id": t.get("id"),
                        "rr": rr,
                        "bars_held": bars,
                    })

        results["fomo_entries"] = {
            "detected": fomo_count > 0,
            "count": fomo_count,
            "instances": fomo_trades[:10],
            "impact_description": (
                f"ไล่ราคา (FOMO) {fomo_count} ไม้ (SL ภายใน {self.SL_NOISE_MAX_BARS} แท่ง "
                f"พร้อม R:R < -0.8)"
            ) if fomo_count > 0 else "ไม่พบอาการ FOMO",
        }

        # ── Strategy Drift: trading in regimes with negative expectancy ──
        regime_pnl: dict[str, list[float]] = {}
        for t in trades:
            r = t.get("regime", "UNKNOWN")
            regime_pnl.setdefault(r, []).append(t.get("pnl", 0.0))

        drift_regimes: list[dict] = []
        for regime, pnls in regime_pnl.items():
            avg = sum(pnls) / len(pnls)
            if avg < 0 and len(pnls) >= 2:
                drift_regimes.append({
                    "regime": regime,
                    "trades": len(pnls),
                    "avg_pnl": round(avg, 2),
                    "total_loss": round(sum(p for p in pnls if p < 0), 2),
                })

        results["strategy_drift"] = {
            "detected": len(drift_regimes) > 0,
            "count": sum(d["trades"] for d in drift_regimes),
            "losing_regimes": drift_regimes,
            "impact_description": (
                f"ใช้กลยุทธ์ผิดสภาวะตลาดใน {len(drift_regimes)} regime: "
                + ", ".join(f'{d["regime"]}(เฉลี่ย ${d["avg_pnl"]})'
                            for d in drift_regimes)
            ) if drift_regimes else "เลือกใช้กลยุทธ์ได้เหมาะสม (Good Regime Fit)",
        }

        # ── Summary violation score ──
        total_violations = (
            overtrade_count + revenge_count + fomo_count
            + sum(d["trades"] for d in drift_regimes)
        )
        results["violation_score"] = {
            "total": total_violations,
            "rating": (
                "ดีเยี่ยม (EXCELLENT)" if total_violations == 0 else
                "ดี (GOOD)" if total_violations <= 2 else
                "พอใช้ (NEEDS_WORK)" if total_violations <= 5 else
                "วิกฤต (CRITICAL)"
            ),
        }

        # ── Martingale / Tilt Detection (God-Tier) ──
        martingale_count = 0
        martingale_trades = []
        for i in range(1, len(trades)):
            prev = trades[i-1]
            curr = trades[i]
            # Check if previous was loss and current lot size increased > 1.2x
            if prev.get("pnl", 0) < 0:
                prev_lot = prev.get("lot_size", 0.01) or 0.01
                curr_lot = curr.get("lot_size", 0.01) or 0.01
                if curr_lot > prev_lot * 1.2:
                    martingale_count += 1
                    martingale_trades.append({
                        "trade_id": curr.get("id"),
                        "prev_id": prev.get("id"),
                        "prev_loss": round(prev.get("pnl", 0), 2),
                        "lot_increase": f"{prev_lot} -> {curr_lot}",
                    })
        
        results["martingale_detected"] = {
            "detected": martingale_count > 0,
            "count": martingale_count,
            "instances": martingale_trades,
            "impact_description": (
                f"CRITICAL: พบการเบิ้ลไม้ (Martingale) {martingale_count} ครั้งหลังจากขาดทุน "
                "พฤติกรรมนี้เสี่ยงล้างพอร์ตสูงมาก"
            ) if martingale_count > 0 else "ไม่พบพฤติกรรม Martingale",
        }

        return results

    # =================================================================
    # C) Market Regime Fit Analysis
    # =================================================================

    def _regime_fit_analysis(self, session: SessionData) -> dict:
        """Performance breakdown by market regime."""
        trades = session.trades
        regimes: dict[str, dict] = {}

        total_loss = sum(
            abs(t.get("pnl", 0.0)) for t in trades if t.get("pnl", 0.0) < 0
        )

        for t in trades:
            r = t.get("regime", "UNKNOWN")
            if r not in regimes:
                regimes[r] = {
                    "trades": 0, "wins": 0, "losses": 0,
                    "total_pnl": 0.0, "loss_total": 0.0,
                    "pnls": [],
                }

            pnl = t.get("pnl", 0.0)
            regimes[r]["trades"] += 1
            regimes[r]["total_pnl"] += pnl
            regimes[r]["pnls"].append(pnl)
            if pnl > 0:
                regimes[r]["wins"] += 1
            else:
                regimes[r]["losses"] += 1
                regimes[r]["loss_total"] += abs(pnl)

        result: dict[str, Any] = {}
        loss_concentration: list[dict] = []

        for regime, data in regimes.items():
            cnt = data["trades"]
            win_rate = (data["wins"] / cnt * 100) if cnt > 0 else 0
            expectancy = data["total_pnl"] / cnt if cnt > 0 else 0
            dd_contribution = (
                (data["loss_total"] / total_loss * 100) if total_loss > 0 else 0
            )

            regime_info = {
                "trades": cnt,
                "wins": data["wins"],
                "losses": data["losses"],
                "win_rate": round(win_rate, 1),
                "total_pnl": round(data["total_pnl"], 2),
                "expectancy": round(expectancy, 2),
                "drawdown_contribution_pct": round(dd_contribution, 1),
            }
            result[regime] = regime_info

            # Flag high-loss regimes
            if dd_contribution > 40:
                loss_concentration.append({
                    "regime": regime,
                    "pct_of_total_losses": round(dd_contribution, 1),
                    "trades_in_regime": cnt,
                    "win_rate": round(win_rate, 1),
                })

        result["_loss_concentration_warning"] = loss_concentration
        result["_total_regimes"] = len(regimes)

        return result

    # =================================================================
    # D) Execution Quality
    # =================================================================

    def _execution_quality(self, session: SessionData) -> dict:
        """Measure exit reason distribution, R:R, hold time, SL noise."""
        trades = session.trades

        # ── Exit reason distribution ──
        exit_reasons: dict[str, int] = {}
        for t in trades:
            reason = t.get("reason", "UNKNOWN")
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        # ── R:R distribution ──
        rr_values = [t.get("rr", 0.0) for t in trades]
        rr_winners = [t.get("rr", 0.0) for t in trades if t.get("pnl", 0.0) > 0]
        rr_losers = [t.get("rr", 0.0) for t in trades if t.get("pnl", 0.0) <= 0]

        avg_rr = statistics.mean(rr_values) if rr_values else 0
        avg_rr_win = statistics.mean(rr_winners) if rr_winners else 0
        avg_rr_loss = statistics.mean(rr_losers) if rr_losers else 0

        # ── Theoretical vs. realized R:R ──
        # Theoretical R:R: |TP - entry| / |SL - entry|
        theoretical_rrs: list[float] = []
        realized_rrs: list[float] = []
        for t in trades:
            sl = t.get("sl", 0)
            tp = t.get("tp", 0)
            entry = t.get("entry", 0)
            if sl and tp and entry and sl != entry:
                sl_dist = abs(entry - sl)
                tp_dist = abs(tp - entry)
                theo = tp_dist / sl_dist if sl_dist > 0 else 0
                theoretical_rrs.append(theo)
                realized_rrs.append(t.get("rr", 0.0))

        avg_theo_rr = statistics.mean(theoretical_rrs) if theoretical_rrs else 0
        rr_capture_rate = (
            (avg_rr / avg_theo_rr * 100) if avg_theo_rr > 0 else 0
        )

        # ── Hold time analysis ──
        bars_held = [t.get("bars", 0) for t in trades]
        bars_winners = [t.get("bars", 0) for t in trades if t.get("pnl", 0.0) > 0]
        bars_losers = [t.get("bars", 0) for t in trades if t.get("pnl", 0.0) <= 0]

        avg_bars = statistics.mean(bars_held) if bars_held else 0
        avg_bars_win = statistics.mean(bars_winners) if bars_winners else 0
        avg_bars_loss = statistics.mean(bars_losers) if bars_losers else 0

        # ── SL Noise Detection ──
        sl_noise_count = 0
        for t in trades:
            if (t.get("reason") == "SL"
                    and t.get("bars", 999) <= self.SL_NOISE_MAX_BARS):
                sl_noise_count += 1

        sl_noise_pct = (sl_noise_count / len(trades) * 100) if trades else 0
        sl_too_tight = sl_noise_pct > 20  # >20% = signal stop is too tight

        return {
            "exit_reason_distribution": exit_reasons,
            "rr_analysis": {
                "avg_rr_all": round(avg_rr, 2),
                "avg_rr_winners": round(avg_rr_win, 2),
                "avg_rr_losers": round(avg_rr_loss, 2),
                "avg_theoretical_rr": round(avg_theo_rr, 2),
                "rr_capture_rate_pct": round(rr_capture_rate, 1),
            },
            "hold_time": {
                "avg_bars_all": round(avg_bars, 1),
                "avg_bars_winners": round(avg_bars_win, 1),
                "avg_bars_losers": round(avg_bars_loss, 1),
                "winners_hold_longer": avg_bars_win > avg_bars_loss,
            },
            "sl_noise": {
                "quick_sl_hits": sl_noise_count,
                "pct_of_trades": round(sl_noise_pct, 1),
                "stop_too_tight": sl_too_tight,
                "recommendation": (
                    f"ขยาย SL ด่วน: {sl_noise_count} ไม้ ({round(sl_noise_pct, 1)}%) "
                    f"โดน SL ภายใน {self.SL_NOISE_MAX_BARS} แท่ง — ตั้งแคบเกินไป (Too Tight)"
                ) if sl_too_tight else "ระยะ SL เหมาะสมแล้ว (SL Adequate)",
            },
        }

    # =================================================================
    # E) Risk & Discipline Metrics
    # =================================================================

    def _risk_discipline_metrics(self, session: SessionData) -> dict:
        """Loss streaks, emotional exposure, equity smoothness."""
        trades = session.trades

        # ── Loss streak distribution ──
        streaks: list[int] = []
        current_streak = 0
        max_streak = 0

        for t in trades:
            if t.get("pnl", 0.0) <= 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                if current_streak > 0:
                    streaks.append(current_streak)
                current_streak = 0
        if current_streak > 0:
            streaks.append(current_streak)

        # Streak histogram
        streak_hist: dict[str, int] = {}
        for s in streaks:
            key = f"{s}_loss"
            streak_hist[key] = streak_hist.get(key, 0) + 1

        # ── Trades during loss streaks (emotional risk exposure) ──
        trades_in_streaks = 0
        streak_len = 0
        for t in trades:
            if t.get("pnl", 0.0) <= 0:
                streak_len += 1
                if streak_len >= 2:
                    trades_in_streaks += 1
            else:
                streak_len = 0

        emotional_exposure_pct = (
            trades_in_streaks / len(trades) * 100
        ) if trades else 0

        # ── Equity curve smoothness ──
        pnls = [t.get("pnl", 0.0) for t in trades]
        returns_std = statistics.stdev(pnls) if len(pnls) > 1 else 0
        returns_mean = statistics.mean(pnls) if pnls else 0

        # Coefficient of variation (lower = smoother)
        cv = abs(returns_std / returns_mean) if returns_mean != 0 else float("inf")
        smoothness_rating = (
            "เรียบเนียน (SMOOTH)" if cv < 1.5 else
            "ปานกลาง (MODERATE)" if cv < 3.0 else
            "ผันผวนสูง (CHOPPY)"
        )

        return {
            "loss_streaks": {
                "max_streak": max_streak,
                "streak_histogram": streak_hist,
                "total_streak_events": len(streaks),
            },
            "emotional_exposure": {
                "trades_during_streaks": trades_in_streaks,
                "pct_of_total": round(emotional_exposure_pct, 1),
                "risk_level": (
                    "ต่ำ (LOW)" if emotional_exposure_pct < 10 else
                    "ปานกลาง (MODERATE)" if emotional_exposure_pct < 25 else
                    "สูง (HIGH)"
                ),
            },
            "equity_smoothness": {
                "returns_std": round(returns_std, 2),
                "returns_mean": round(returns_mean, 2),
                "coefficient_of_variation": round(cv, 2) if cv != float("inf") else None,
                "rating": smoothness_rating,
            },
        }

    # =================================================================
    # F) Root Cause Analysis (ranked by $ impact)
    # =================================================================

    def _root_cause_analysis(
        self,
        session: SessionData,
        behavior: dict,
        regime: dict,
        execution: dict,
    ) -> list[dict]:
        """Identify top 3 loss drivers ranked by dollar impact."""
        causes: list[dict] = []

        # ── Cause 1: Regime mismatch losses ──
        loss_warnings = regime.get("_loss_concentration_warning", [])
        for w in loss_warnings:
            # Calculate $ impact from regime losses
            regime_name = w["regime"]
            regime_data = regime.get(regime_name, {})
            regime_pnl = regime_data.get("total_pnl", 0)
            if regime_pnl < 0:
                causes.append({
                    "rank": 0,
                    "category": "REGIME_MISMATCH (แพ้ทางตลาด)",
                    "description": (
                        f"{w['pct_of_total_losses']}% ของการขาดทุนมาจากสภาวะ "
                        f"{regime_name} ({w['trades_in_regime']} ไม้, "
                        f"WR {w['win_rate']}%)"
                    ),
                    "dollar_impact": round(abs(regime_pnl), 2),
                    "fix": f"หยุดเทรด หรือลดขนาดไม้ในสภาวะ {regime_name} (Disable/Reduce Size)",
                })

        # ── Cause 2: Revenge trading damage ──
        revenge = behavior.get("revenge_trading", {})
        if revenge.get("detected"):
            revenge_loss = abs(revenge.get("total_pnl_impact", 0))
            if revenge_loss > 0:
                causes.append({
                    "rank": 0,
                    "category": "REVENGE_TRADING (เทรดล้างแค้น)",
                    "description": (
                        f"พบการเทรดล้างแค้น {revenge['count']} ครั้งหลังขาดทุนติดกัน "
                        f"เสียหายรวม: ${round(revenge_loss, 2)}"
                    ),
                    "dollar_impact": round(revenge_loss, 2),
                    "fix": "พักการเทรด (Cooldown) หลังขาดทุนจาก 5 นาที → 15 นาที",
                })

        # ── Cause 2.5: Martingale Damage ──
        martingale = behavior.get("martingale_detected", {})
        if martingale.get("detected"):
            martingale_losses = sum(
                abs(t.get("pnl", 0)) 
                for t in session.trades 
                if t.get("id") in {m["trade_id"] for m in martingale.get("instances", [])}
                and t.get("pnl", 0) < 0
            )
            causes.append({
                "rank": 0,
                "category": "MARTINGALE_TILT (เบิ้ลไม้สู้)",
                "description": martingale["impact_description"],
                "dollar_impact": round(martingale_losses, 2),
                "fix": "ห้ามเบิ้ลไม้เด็ดขาด (HARD BLOCK: Lot size increase forbidden)",
            })

        # ── Cause 3: SL noise (stops too tight) ──
        sl_noise = execution.get("sl_noise", {})
        if sl_noise.get("stop_too_tight"):
            # Estimate damage: count of quick SL × avg loss
            quick_hits = sl_noise.get("quick_sl_hits", 0)
            avg_loss = abs(
                session.trades[0].get("pnl", 0)
                if session.trades else 0
            )
            # Better estimate from actual trades
            noise_losses = [
                abs(t.get("pnl", 0))
                for t in session.trades
                if t.get("reason") == "SL"
                and t.get("bars", 999) <= self.SL_NOISE_MAX_BARS
                and t.get("pnl", 0) < 0
            ]
            noise_damage = sum(noise_losses)
            causes.append({
                "rank": 0,
                "category": "SL_TOO_TIGHT (ตั้ง SL แคบไป)",
                "description": (
                    f"โดน SL ไวเกินไป {quick_hits} ครั้งภายใน {self.SL_NOISE_MAX_BARS} แท่ง "
                    f"({sl_noise['pct_of_trades']}%), เสียหาย: ${round(noise_damage, 2)}"
                ),
                "dollar_impact": round(noise_damage, 2),
                "fix": "ขยาย SL อีก 1.2-1.5× ATR เพื่อหนี Noise",
            })

        # ── Cause 4: Overtrading damage ──
        overtrade = behavior.get("overtrading", {})
        if overtrade.get("detected"):
            causes.append({
                "rank": 0,
                "category": "OVERTRADING (เทรดถี่เกิน)",
                "description": (
                    f"เข้าออเดอร์เร็วเกินไป {overtrade['count']} ครั้ง "
                    f"({overtrade['pct_of_trades']}% ของไม้ทั้งหมด เข้าภายใน "
                    f"{self.OVERTRADE_BAR_WINDOW} แท่ง)"
                ),
                "dollar_impact": 0,  # Indirect impact — captured in other metrics
                "fix": f"จำกัดจำนวนไม้ต่อวัน (Max Trades) จากไม่จำกัด → 3-5 ไม้",
            })

        # ── Cause 5: FOMO entries ──
        fomo = behavior.get("fomo_entries", {})
        if fomo.get("detected"):
            fomo_losses = sum(
                abs(t.get("pnl", 0))
                for t in session.trades
                if t.get("id") in {
                    inst.get("trade_id") for inst in fomo.get("instances", [])
                }
                and t.get("pnl", 0) < 0
            )
            causes.append({
                "rank": 0,
                "category": "FOMO_ENTRIES (ไล่ราคา)",
                "description": (
                    f"พบการไล่ราคา (FOMO) {fomo['count']} ครั้ง (SL ไว + R:R แย่), "
                    f"เสียหาย: ${round(fomo_losses, 2)}"
                ),
                "dollar_impact": round(fomo_losses, 2),
                "fix": "รอราคาย่อตัว (Pullback) หลังแท่งยาว; ห้ามเทรด Breakout ปลายน้ำ",
            })

        # Sort by dollar impact, assign ranks
        causes.sort(key=lambda x: x["dollar_impact"], reverse=True)
        for i, c in enumerate(causes):
            c["rank"] = i + 1

        return causes[:5]  # Top 5

    # =================================================================
    # G) Coaching Corrections
    # =================================================================

    def _generate_corrections(
        self, session: SessionData, report: CoachReport,
    ) -> dict:
        """Generate actionable rule changes and training plan."""
        corrections: list[dict] = []
        strategy_adjustments: list[dict] = []

        # ── Rule changes based on root causes ──
        for cause in report.root_causes:
            cat = cause.get("category", "")

            if cat == "REGIME_MISMATCH":
                corrections.append({
                    "type": "RULE_CHANGE",
                    "rule": "ห้ามเข้าออเดอร์เมื่อ ADX < 18 หรือ ATR < ATR_MA",
                    "reason": cause["description"],
                    "priority": "HIGH",
                })
                strategy_adjustments.append({
                    "parameter": "adx_min_threshold",
                    "current": 15,
                    "recommended": 20,
                    "reason": "เทรดเยอะเกินไปในช่วงตลาดไม่เป็นเทรนด์ (Non-trending)",
                })

            elif cat == "REVENGE_TRADING":
                corrections.append({
                    "type": "RULE_CHANGE",
                    "rule": "เพิ่มเวลาพัก (Cooldown) หลังขาดทุนจาก 5 นาที → 15 นาที",
                    "reason": cause["description"],
                    "priority": "CRITICAL",
                })
                corrections.append({
                    "type": "RULE_CHANGE",
                    "rule": "หากขาดทุนติดกัน 2 ไม้ ให้หยุดเทรดทันทีใน Session นั้น",
                    "reason": "ป้องกันการใช้อารมณ์ (Emotional Escalation)",
                    "priority": "CRITICAL",
                })

            elif cat == "SL_TOO_TIGHT":
                corrections.append({
                    "type": "PARAMETER_CHANGE",
                    "rule": "ขยาย SL เป็น 1.5× ATR (จากค่าปัจจุบัน)",
                    "reason": cause["description"],
                    "priority": "HIGH",
                })
                strategy_adjustments.append({
                    "parameter": "sl_atr_multiplier",
                    "current": 1.0,
                    "recommended": 1.5,
                    "reason": "SL แคบเกินไป — โดนเหวี่ยงกินบ่อย (Noise Stops)",
                })

            elif cat == "OVERTRADING":
                perf = report.performance_summary
                max_trades = max(2, perf.get("winning_trades", 2))
                corrections.append({
                    "type": "RULE_CHANGE",
                    "rule": f"ลดจำนวนไม้สูงสุดต่อ Session เหลือ {min(max_trades, 5)} ไม้",
                    "reason": cause["description"],
                    "priority": "HIGH",
                })

            elif cat == "FOMO_ENTRIES":
                corrections.append({
                    "type": "RULE_CHANGE",
                    "rule": "ต้องรอราคาย่อตัว (Pullback Confirmation) ก่อนเข้าออเดอร์หลังแท่งยาว",
                    "reason": cause["description"],
                    "priority": "MEDIUM",
                })

        # ── Regime enable/disable matrix ──
        regime_matrix: dict[str, str] = {}
        for regime, data in report.regime_performance.items():
            if regime.startswith("_"):
                continue
            if not isinstance(data, dict):
                continue
            wr = data.get("win_rate", 50)
            exp = data.get("expectancy", 0)
            if wr < 35 or exp < -5:
                regime_matrix[regime] = "DISABLE"
            elif wr < 45:
                regime_matrix[regime] = "REDUCE_SIZE"
            else:
                regime_matrix[regime] = "ENABLED"

        # ── 7-Day Training Plan ──
        training_plan = self._build_training_plan(session, report)

        return {
            "rule_corrections": corrections,
            "strategy_adjustments": strategy_adjustments,
            "regime_matrix": regime_matrix,
            "training_plan_7day": training_plan,
        }

    def _build_training_plan(
        self, session: SessionData, report: CoachReport,
    ) -> list[dict]:
        """Generate a 7-day personalized training plan."""
        plan: list[dict] = []
        violations = report.behavioral_violations

        # Day 1: Immediate Correction (The "Rehab" Day)
        focus = "วินัยพื้นฐาน (Discipline)"
        drill = "เทรดเพียง 1 ไม้ แล้วปิดจอ (One Bullet Drill)"
        kpi = "ไร้การละเมิดกฎ (0 Violations)"
        
        if violations.get("revenge_trading", {}).get("detected"):
            focus = "ควบคุมอารมณ์ (Emotional Control)"
            drill = "ห้ามเทรดต่อหลังขาดทุน 1 ไม้ (Stop Loss = Stop Day)"
            kpi = "ทำตามกฎ Cooldown (Cooldown adhered)"
        elif violations.get("martingale_detected", {}).get("detected"):
            focus = "เลิกนิสัยการพนัน (Anti-Gambling)"
            drill = "ใช้ Fixed Lot Size เท่านั้น (ห้ามเพิ่ม Lot เด็ดขาด)"
            kpi = "ขนาด Lot คงที่ (Flat Sizing)"
        elif violations.get("overtrading", {}).get("detected"):
            focus = "เน้นคุณภาพ (Quality over Quantity)"
            drill = "จำกัด 2 ไม้ต่อวัน (Sniper Mode)"
            kpi = "ไม่เกิน 2 ไม้ (Max 2 trades)"

        plan.append({
            "day": "Day 1 (ฟื้นฟู)",
            "days": 1,
            "focus": focus,
            "drill": drill,
            "max_trades_per_day": 2,
            "session_focus": "Discipline",
            "kpis": [kpi],
            "success_kpi": kpi
        })

        # Day 2-3: Execution Focus
        exec_q = report.execution_quality
        ex_focus = "การรันเทรนด์ (Execution)"
        ex_drill = "ถือออเดอร์ให้ถึง TP หรือ SL เท่านั้น (Set & Forget)"
        
        if exec_q.get("sl_noise", {}).get("stop_too_tight"):
            ex_drill = "ตั้ง SL เผื่อระยะ ATR 1.5 เท่า (Wider Stops)"
        elif report.performance_summary.get("profit_factor", 0) < 1.0:
            ex_drill = "มองหา R:R 1:2 ขึ้นไป (High R:R Setup)"
        elif violations.get("fomo_entries", {}).get("detected"):
             ex_drill = "รอ Pullback เท่านั้น ห้าม Follow Trend (Pullback only)"

        plan.append({
            "day": "Day 2-3 (เทคนิค)",
            "days": 2,
            "focus": ex_focus,
            "drill": ex_drill,
            "max_trades_per_day": 3,
            "session_focus": "Execution",
            "kpis": ["ทำตามแผน (Follow Plan)", "ไม่ปิดมือ (No Early Exit)"],
            "success_kpi": "Follow Plan"
        })

        # Day 4-7: Consistency & Scale
        plan.append({
            "day": "Day 4-7 (ความสม่ำเสมอ)",
            "days": 4,
            "focus": "สร้างความสม่ำเสมอ (Consistency)",
            "drill": "เทรดตามระบบปกติ (Normal System Rules)",
            "max_trades_per_day": 5,
            "session_focus": "Performance",
            "kpis": ["Win Rate > 40%", "Profit Factor > 1.2"],
            "success_kpi": "PF > 1.2"
        })

        return plan

    # =================================================================
    # H) Next Session Rules
    # =================================================================

    def _generate_next_session_rules(self, report: CoachReport) -> dict:
        """Concrete rules for the next trading session."""
        rules: dict[str, Any] = {
            "max_trades": 5,
            "cooldown_after_loss_minutes": 5,
            "max_consecutive_losses_before_halt": 2,
            "allowed_regimes": ["TRENDING_UP", "TRENDING_DOWN", "HIGH_VOLATILITY"],
            "min_adx": 18,
            "min_atr_ratio": 0.8,
        }

        # Tighten rules based on coaching findings
        corrections = report.coach_recommendations.get("rule_corrections", [])
        for c in corrections:
            rule_text = c.get("rule", "")
            if "cooldown" in rule_text.lower() and "15 min" in rule_text:
                rules["cooldown_after_loss_minutes"] = 15
            if "max trades" in rule_text.lower():
                # Extract number
                import re
                nums = re.findall(r'\d+', rule_text)
                if nums:
                    rules["max_trades"] = min(int(nums[-1]), 5)

        # Disable underperforming regimes
        regime_matrix = report.coach_recommendations.get("regime_matrix", {})
        disabled_regimes = [
            r for r, status in regime_matrix.items() if status == "DISABLE"
        ]
        if disabled_regimes:
            rules["allowed_regimes"] = [
                r for r in rules["allowed_regimes"]
                if r not in disabled_regimes
            ]
            rules["disabled_regimes"] = disabled_regimes

        return rules

    # =================================================================
    # I) Auto-Enforcement Recommendations
    # =================================================================

    def _generate_enforcement(self, report: CoachReport) -> dict:
        """Map corrections to code-level enforcement."""
        hard_blocks: list[dict] = []
        ui_warnings: list[dict] = []
        lockout_triggers: list[dict] = []

        violations = report.behavioral_violations
        perf = report.performance_summary

        # ── Hard blocks ──
        if violations.get("revenge_trading", {}).get("detected"):
            hard_blocks.append({
                "rule": "COOLDOWN_AFTER_LOSS",
                "enforcement": "ระงับการเทรด 15 นาทีหลังขาดทุน (Block all trades for 15 minutes after a loss)",
                "code_module": "app.risk.cooldown_manager",
                "parameter": "cooldown_minutes=15",
            })
            hard_blocks.append({
                "rule": "LOSS_STREAK_HALT",
                "enforcement": "หยุดการเทรดเมื่อขาดทุนติดกัน 2 ไม้ (Halt trading after 2 consecutive losses)",
                "code_module": "app.risk.cooldown_manager",
                "parameter": "max_consecutive_losses=2",
            })

        if violations.get("strategy_drift", {}).get("detected"):
            for regime_info in violations["strategy_drift"].get("losing_regimes", []):
                hard_blocks.append({
                    "rule": "REGIME_BLOCK",
                    "enforcement": f"ระงับการเทรดในสภาวะ {regime_info['regime']}",
                    "code_module": "app.risk.regime_filter",
                    "parameter": f"blocked_regimes=['{regime_info['regime']}']",
                })

        # ── UI Warnings ──
        if violations.get("overtrading", {}).get("detected"):
            ui_warnings.append({
                "trigger": "Trade count approaching session limit",
                "message": "⚠️ ตรวจพบ Overtrading — กรุณาชะลอการเทรดและรอกราฟสวยๆ",
                "dashboard_panel": "Decision Monitor",
            })

        if violations.get("fomo_entries", {}).get("detected"):
            ui_warnings.append({
                "trigger": "Entry after large candle (> 2× ATR)",
                "message": "⚠️ อาจเป็นการไล่ราคา (FOMO) — ควรรอ Pullback",
                "dashboard_panel": "Decision Monitor",
            })

        sl_noise = report.execution_quality.get("sl_noise", {})
        if sl_noise.get("stop_too_tight"):
            ui_warnings.append({
                "trigger": f"SL hit within {self.SL_NOISE_MAX_BARS} bars",
                "message": "⚠️ SL แคบเกินไป — ควรขยายเป็น 1.5× ATR",
                "dashboard_panel": "Replay Review",
            })

        # ── Lockout triggers ──
        if perf.get("max_drawdown_pct", 0) > 5:
            lockout_triggers.append({
                "condition": "max_drawdown_pct > 5%",
                "action": "Switch to DRY_RUN mode",
                "duration": "Until manual review + approval",
            })

        if perf.get("win_rate", 100) < 30:
            lockout_triggers.append({
                "condition": "win_rate < 30%",
                "action": "Halt live trading, run 50-trade Replay drill",
                "duration": "Until Replay win_rate > 50%",
            })

        dd_rating = report.risk_discipline.get(
            "emotional_exposure", {},
        ).get("risk_level", "LOW")
        if dd_rating == "HIGH":
            lockout_triggers.append({
                "condition": "emotional_exposure = HIGH (>25% trades during loss streaks)",
                "action": "Reduce lot size by 50% + enforce 15-min cooldown",
                "duration": "Next 3 sessions",
            })

        return {
            "hard_blocks": hard_blocks,
            "ui_warnings": ui_warnings,
            "lockout_triggers": lockout_triggers,
            "total_enforcement_rules": (
                len(hard_blocks) + len(ui_warnings) + len(lockout_triggers)
            ),
        }

    # =================================================================
    # J) Advanced Analysis Methods (God-Tier)
    # =================================================================

    def _analyze_hourly_performance(self, session: SessionData) -> dict:
        """Analyze performance by hour of day (Golden Hours)."""
        hourly_stats: dict[int, dict] = {}
        
        for t in session.trades:
            # Try to parse entry_time
            entry_time_str = t.get("entry_time") or t.get("time") or ""
            hour = -1
            
            if entry_time_str:
                try:
                    # Handle typical ISO format or simple space-separated
                    # 2023-10-27T10:00:00 or 2023-10-27 10:00:00
                    clean_str = entry_time_str.replace("T", " ")
                    dt = datetime.strptime(clean_str[:19], "%Y-%m-%d %H:%M:%S")
                    hour = dt.hour
                except (ValueError, TypeError):
                    pass
            
            if hour == -1:
                continue
                
            if hour not in hourly_stats:
                hourly_stats[hour] = {"trades": 0, "wins": 0, "pnl": 0.0}
            
            pnl = t.get("pnl", 0.0)
            hourly_stats[hour]["trades"] += 1
            hourly_stats[hour]["pnl"] += pnl
            if pnl > 0:
                hourly_stats[hour]["wins"] += 1
        
        if not hourly_stats:
            return {"status": "NO_TIME_DATA"}
            
        # Enrich stats
        results = {}
        for h, stats in hourly_stats.items():
            cnt = stats["trades"]
            wr = (stats["wins"] / cnt * 100) if cnt > 0 else 0
            results[f"{h:02d}:00"] = {
                "trades": cnt,
                "win_rate": round(wr, 1),
                "total_pnl": round(stats["pnl"], 2),
                "rating": "GOLDEN" if stats["pnl"] > 0 and wr > 50 else "KILL_ZONE"
            }
            
        return results

    def _calculate_session_score(self, report: CoachReport) -> int:
        """Calculate a 0-100 Session Quality Score."""
        score = 100
        
        # 1. Behavioral Penalties
        violations = report.behavioral_violations
        if violations.get("overtrading", {}).get("detected"): score -= 10
        if violations.get("revenge_trading", {}).get("detected"): score -= 15
        if violations.get("fomo_entries", {}).get("detected"): score -= 10
        if violations.get("martingale_detected", {}).get("detected"): score -= 25
        if violations.get("strategy_drift", {}).get("detected"): score -= 5
        
        # 2. Performance Penalties
        perf = report.performance_summary
        if perf.get("win_rate", 0) < 40: score -= 10
        if perf.get("profit_factor", 1.0) < 1.0: score -= 10
        if perf.get("max_drawdown_pct", 0) > 3.0: score -= 10
        
        # 3. Execution Penalties
        exec_q = report.execution_quality
        if exec_q.get("sl_noise", {}).get("stop_too_tight"): score -= 5
        
        # 4. Risk Penalties
        risk = report.risk_discipline
        if risk.get("emotional_exposure", {}).get("risk_level") == "HIGH": score -= 10
        
        return max(0, min(100, score))

    def _get_score_rating(self, score: int) -> str:
        if score >= 90: return "LEGENDARY"
        if score >= 80: return "PROFESSIONAL"
        if score >= 70: return "DISCIPLINED"
        if score >= 50: return "AMATEUR"
        return "GAMBLER"

    # =================================================================
    # Human-Readable Report
    # =================================================================

    # =================================================================
    # H) Trader Personality Profiling
    # =================================================================

    def _determine_personality(self, session: SessionData, report: CoachReport) -> dict:
        """
        Classify trader personality based on behavioral patterns.
        
        Personality Types:
        1. The Sniper: High Win Rate, High RR, Low Frequency, Patient.
        2. The Machine: Consistent, High Volume, Stable Equity, Disciplined.
        3. The Scalper: High Frequency, Short Duration, Small Wins.
        4. The Gambler: High Risk, Martingale, Low Win Rate, Big Losses.
        5. The Revenge Trader: Loss Streaks -> High Volume, Anger Trading.
        6. The Hesitant: Missed Moves (N/A here), Cutting Winners Early (Low RR capture).
        7. The Rookie: Inconsistent, Random entries, No clear edge.
        """
        summary = report.performance_summary
        behavior = report.behavioral_violations
        execution = report.execution_quality
        risk = report.risk_discipline
        
        # Extract metrics
        win_rate = summary.get("win_rate", 0)
        pf = summary.get("profit_factor", 0)
        total_trades = summary.get("total_trades", 0)
        avg_rr = execution.get("rr_analysis", {}).get("avg_rr_all", 0)
        martingale_detected = behavior.get("martingale_detected", {}).get("detected", False)
        revenge_detected = behavior.get("revenge_trading", {}).get("detected", False)
        
        # Logic Hierarchy (Worst to Best)
        
        # 1. The Gambler (Dangerous)
        if martingale_detected or (win_rate < 40 and avg_rr < 0.8):
            return {
                "type": "THE GAMBLER (นักพนัน)",
                "description": "มีการเบิ้ลไม้สู้ (Martingale) หรือเข้าออเดอร์มั่วโดยไม่มี Edge ที่ชัดเจน เสี่ยงล้างพอร์ตสูงมาก",
                "score": 10,
                "color": "red"
            }
            
        # 2. The Revenge Trader (Emotional)
        if revenge_detected:
            return {
                "type": "THE REVENGE TRADER (สายหัวร้อน)",
                "description": "มักจะเสียสติเวลาขาดทุน (Loss Streak) แล้วกดออเดอร์รัวๆ เพื่อเอาคืน ทำให้เสียหายหนักกว่าเดิม",
                "score": 25,
                "color": "orange"
            }
            
        # 3. The Rookie (Inconsistent)
        if pf < 1.0 or win_rate < 40:
            return {
                "type": "THE ROOKIE (มือใหม่)",
                "description": "ผลงานยังไม่เสถียร ขาดทุนมากกว่ากำไร ยังต้องปรับปรุงกลยุทธ์และวินัยอีกมาก",
                "score": 40,
                "color": "yellow"
            }
            
        # 4. The Scalper (High Freq)
        # Assuming M5/M1 data, short duration
        avg_bars = execution.get("hold_time", {}).get("avg_bars_all", 0)
        is_scalping = avg_bars < 12 and total_trades > 20 # < 1 hour on M5 if freq is high
        if is_scalping and pf > 1.2:
            return {
                "type": "THE SCALPER (สายซิ่ง)",
                "description": "เน้นรอบเร็ว เก็บสั้นๆ ความแม่นยำใช้ได้ แต่ต้องระวังค่าคอมมิชชั่นและ Slippage",
                "score": 75,
                "color": "blue"
            }
            
        # 5. The Sniper (Patient & Sharp)
        if total_trades < 20 and win_rate > 60 and avg_rr > 1.5:
             return {
                "type": "THE SNIPER (สไนเปอร์)",
                "description": "เข้าออเดอร์น้อยแต่คม รอจังหวะได้ดีมาก (Patience) กำไรต่อไม้สูง วินัยดีเยี่ยม",
                "score": 90,
                "color": "green"
            }

        # 6. The Machine (Professional)
        if pf > 1.5 and risk.get("equity_smoothness", {}).get("rating") == "เรียบเนียน (SMOOTH)":
             return {
                "type": "THE MACHINE (เครื่องจักรผลิตเงิน)",
                "description": "เทรดได้อย่างสม่ำเสมอ พอร์ตโตแบบเรียบเนียน (Smooth Equity) ควบคุมอารมณ์และความเสี่ยงได้สมบูรณ์แบบ",
                "score": 95,
                "color": "gold"
            }
            
        # Default
        return {
            "type": "THE GRINDER (นักสู้)",
            "description": "พอมีกำไรบ้างแต่ยังเหนื่อย (PF > 1.0) ต้องปรับปรุงจุดเข้าออกให้คมขึ้นอีกนิด",
            "score": 60,
            "color": "gray"
        }

    def format_report_text(self, report: CoachReport) -> str:
        """Generate human-readable coaching report (Thai markdown)."""
        lines: list[str] = []
        perf = report.performance_summary
        
        score = perf.get("session_score", 0)
        rating_en = perf.get("score_rating", "GAMBLER")
        
        # Rating translation
        rating_map = {
            "LEGENDARY": "ตำนาน (Legendary)",
            "PROFESSIONAL": "มืออาชีพ (Professional)",
            "DISCIPLINED": "มีวินัย (Disciplined)",
            "AMATEUR": "มือสมัครเล่น (Amateur)",
            "GAMBLER": "นักพนัน (Gambler)",
        }
        rating_th = rating_map.get(rating_en, rating_en)

        lines.append("# 🏋️ โค้ชส่วนตัว (Auto Coach AI) — รายงานการเทรด")
        
        # Personality Header
        p = report.trader_personality
        p_type = p.get("type", "UNKNOWN")
        p_desc = p.get("description", "")
        p_score = p.get("score", 0)
        
        lines.append(f"### 🎭 ตัวตนของคุณ: **{p_type}** (Score: {p_score}/100)")
        lines.append(f"> *\"{p_desc}\"*")
        lines.append(f"\n*วิเคราะห์เมื่อ: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}*\n")

        # A) Performance Summary
        lines.append("## 📊 สรุปผลงาน (Performance)")
        status = perf.get("status", "UNKNOWN")
        if status == "INSUFFICIENT_DATA":
            lines.append("> ⚠️ **ข้อมูลไม่เพียงพอ**: ต้องการอย่างน้อย 3 ไม้เพื่อวิเคราะห์")
            return "\n".join(lines)

        lines.append(f"- **กำไรสุทธิ (Net PnL)**: `${perf.get('net_pnl', 0):.2f}`")
        lines.append(f"- **Win Rate**: `{perf.get('win_rate', 0)}%` ({perf.get('winning_trades', 0)}/{perf.get('total_trades', 0)})")
        lines.append(f"- **Profit Factor**: `{perf.get('profit_factor', 0):.2f}`")
        lines.append(f"- **Max Drawdown**: `{perf.get('max_drawdown_pct', 0)}%` (`${perf.get('max_drawdown_usd', 0):.2f}`)")

        # B) Root Causes
        lines.append("\n## 🚨 ปัญหาต้นตอที่ต้องแก้ (Root Causes)")
        if not report.root_causes:
            lines.append("- ✅ ยอดเยี่ยม! ไม่พบปัญหาใหญ่ในเซสชันนี้")
        for cause in report.root_causes:
            rank = cause.get("rank", 0)
            cat = cause.get("category", "")
            impact = cause.get("dollar_impact", 0)
            desc = cause.get("description", "")
            fix = cause.get("fix", "")
            lines.append(f"**{rank}. {cat}** (เสียหาย ${impact})")
            lines.append(f"   - **อาการ**: {desc}")
            lines.append(f"   - **วิธีแก้**: {fix}")

        # C) Behavioral Violations
        lines.append("\n## 🧠 พฤติกรรมเสี่ยง (Behavioral)")
        violation_found = False
        behavior_map = {
            "overtrading": "Overtrading (ซอยไม้ถี่เกินไป)",
            "revenge_trading": "Revenge Trading (เทรดล้างแค้น)",
            "fomo_entries": "FOMO (ไล่ราคาปลายเทรนด์)",
            "strategy_drift": "Strategy Drift (เทรดผิดสภาวะตลาด)",
            "martingale_detected": "Martingale (เบิ้ลไม้สู้ตาย)",
        }

        for key, data in report.behavioral_violations.items():
            if data.get("detected"):
                violation_found = True
                label = behavior_map.get(key, key)
                count = data.get("count", 0)
                lines.append(f"- ❌ **{label}**: พบ {count} ครั้ง")
                
        if not violation_found:
            lines.append("- ✅ วินัยดีเยี่ยม ไม่พบข้อผิดพลาดร้ายแรง")

        # D) Training Plan
        lines.append(f"\n## 🏋️ แผนฝึกซ้อม 7 วัน (Training Plan)")
        training = report.coach_recommendations.get("training_plan_7day", [])
        if training:
            for day in training:
                d_name = day.get("day", "")
                focus = day.get("focus", "")
                drill = day.get("drill", "")
                kpis = day.get("kpis", [])
                kpi_str = ", ".join(kpis) if isinstance(kpis, list) else str(kpis)
                
                lines.append(f"- **{d_name}**: {focus}")
                lines.append(f"  - 🎯 **ฝึก (Drill)**: {drill}")
                lines.append(f"  - 📈 **เป้าหมาย (KPIs)**: {kpi_str}")
        else:
            lines.append("- รักษามาตรฐานนี้ไว้ (Maintain current performance)")

        # E) Strategy Adjustments
        adjustments = report.coach_recommendations.get("strategy_adjustments", [])
        if adjustments:
             lines.append(f"\n## ⚙️ แนะนำปรับตั้งค่า (Strategy Tuning)")
             for adj in adjustments:
                 param = adj.get("parameter", "")
                 curr = adj.get("current", "")
                 rec = adj.get("recommended", "")
                 reason = adj.get("reason", "")
                 lines.append(f"- **{param}**: `{curr}` → `{rec}` ({reason})")

        return "\n".join(lines)
