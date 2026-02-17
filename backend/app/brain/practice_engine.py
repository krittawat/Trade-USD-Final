"""
PracticeEngine — ฝึกซ้อมเทรดบนข้อมูลอดีต (Historical Backtester).

หน้าที่:
    - รัน strategy บนข้อมูลแท่งเทียนอดีต (ไม่ส่งออเดอร์จริง)
    - จำลอง SL/TP hit จาก High/Low ของแท่งเทียน
    - คำนวณ performance metrics: win_rate, PF, max_dd, expectancy, score
    - ตรวจจับ chart patterns และ tag เทรดด้วย news context
    - รองรับ tournament mode: รันทุก strategy แล้วจัดอันดับ

กฎ RAM (8GB mode):
    - ใช้ sliding window (250 candles) — ไม่โหลดทั้งหมดเข้า RAM
    - ไม่เก็บ tick-level data
    - ใช้ strategy logic เดียวกับ live (สร้าง Decision จาก Factory)

วิธีจำลอง trade:
    1. Strategy สร้าง Decision (BUY/SELL + SL/TP)
    2. PatternDetector ตรวจ patterns ของ window → tag trade
    3. NewsCollector ตรวจ news context ของ bar → tag trade
    4. ดูแท่งเทียนถัดไป: ถ้า High >= TP → TP hit, ถ้า Low <= SL → SL hit
    5. คำนวณ R จาก SL distance
    6. สะสม results + pattern/news stats → return PracticeResult
"""

import asyncio
import time

import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, PracticeResult, SymbolProfile
from app.brain.regime import classify_regime

logger = get_logger(__name__)

# --- Constants ---
WINDOW_SIZE = 250       # จำนวนแท่งเทียนต่อ window (RAM-safe)
MIN_CANDLES = 50        # ขั้นต่ำสำหรับการวิเคราะห์
MAX_TRADES_PER_RUN = 200  # จำกัดจำนวน simulated trades ต่อรอบ


class PracticeEngine:
    """
    ฝึกซ้อมเทรด — รัน strategy บนข้อมูลอดีตแล้วคำนวณ metrics.

    ไม่ส่งออเดอร์จริง, ไม่ผ่าน MT5, ไม่ผ่าน Risk Gate.
    ใช้ logic เดียวกับ Strategy.analyze() เพื่อสร้าง Decision.

    Enhancement:
        - PatternDetector: ตรวจจับ candlestick + chart patterns ทุก bar
        - NewsCollector: tag เทรดด้วย news context (near HIGH/MEDIUM/LOW event)
    """

    def __init__(self, factory=None, pattern_detector=None, news_collector=None) -> None:
        """
        Args:
            factory: StrategyFactory instance ที่ลงทะเบียน strategies แล้ว
            pattern_detector: PatternDetector instance (optional)
            news_collector: NewsCollector instance (optional)
        """
        self.factory = factory
        self.pattern_detector = pattern_detector
        self.news_collector = news_collector

    # ────────────────────────────────────────────────────────────────
    # run_practice — ฝึกซ้อม single strategy
    # ────────────────────────────────────────────────────────────────

    async def run_practice(
        self,
        symbol: str,
        strategy_name: str,
        candles: pd.DataFrame,
        profile: SymbolProfile | None = None,
        params: dict | None = None,
    ) -> PracticeResult:
        """
        รัน strategy บนข้อมูลอดีตแล้วคำนวณ performance.

        วิธี:
            1. Slide window ทีละ 1 bar ตั้งแต่ bar 50 จนจบ
            2. ทุก window: เรียก strategy.analyze() → ถ้า BUY/SELL → simulate trade
            3. ดูแท่งเทียนถัดไป → SL/TP hit?
            4. สะสม wins/losses/R → return PracticeResult

        Args:
            symbol: สัญลักษณ์ เช่น XAUUSDm
            strategy_name: ชื่อ strategy ที่จะทดสอบ
            candles: DataFrame ของแท่งเทียน [open, high, low, close, volume, time]
            profile: SymbolProfile (ถ้าไม่ส่ง → สร้าง default)
            params: parameters override (reserved for evolver)

        Returns:
            PracticeResult: ผลลัพธ์การฝึกซ้อม
        """
        if candles is None or len(candles) < MIN_CANDLES:
            return PracticeResult(
                strategy_name=strategy_name,
                symbol=symbol,
                params=params or {},
            )

        if profile is None:
            profile = SymbolProfile(symbol=symbol)

        # --- ดึง strategy instance ---
        strategy = self._get_strategy(strategy_name)
        if strategy is None:
            logger.warning("practice_strategy_not_found", extra={
                "strategy": strategy_name, "symbol": symbol,
            })
            return PracticeResult(
                strategy_name=strategy_name,
                symbol=symbol,
                params=params or {},
            )

        # --- Run simulation (optimized) ---
        trades: list[dict] = []
        pattern_trades: dict[str, list[bool]] = {}  # pattern_name → [is_win, ...]
        news_trades: dict[str, list[bool]] = {}      # news_ctx → [is_win, ...]
        total_candles = len(candles)

        # ── Pre-compute patterns in batch (3-5× faster) ──
        pattern_batch: dict[int, list] = {}
        if self.pattern_detector:
            try:
                pattern_batch = self.pattern_detector.detect_batch(
                    candles, start=MIN_CANDLES, step=1,
                )
            except Exception:
                pass  # pattern batch fail doesn't block

        # ── Pre-compute news context in batch ──
        news_batch: dict[int, object] = {}
        if self.news_collector and "time" in candles.columns:
            try:
                bar_times = []
                for i in range(MIN_CANDLES, total_candles - 1):
                    bt = candles.iloc[i]["time"]
                    if hasattr(bt, 'to_pydatetime'):
                        bt = bt.to_pydatetime()
                    bar_times.append(bt)
                news_lookup = self.news_collector.get_news_batch(
                    symbol, bar_times,
                )
                # Re-index: news_lookup uses 0-based index within bar_times
                # but we need original candle index starting from MIN_CANDLES
                for rel_idx, ctx in news_lookup.items():
                    news_batch[MIN_CANDLES + rel_idx] = ctx
            except Exception:
                pass

        # ── Pre-compute regime at coarse intervals ──
        regime_cache: dict[int, object] = {}
        REGIME_INTERVAL = 20
        for i in range(MIN_CANDLES, total_candles - 1, REGIME_INTERVAL):
            start_idx = max(0, i - WINDOW_SIZE)
            window = candles.iloc[start_idx:i + 1]
            regime_cache[i] = classify_regime(window)

        # ── Main simulation loop ──
        for i in range(MIN_CANDLES, total_candles - 1):
            if len(trades) >= MAX_TRADES_PER_RUN:
                break

            # Use cached regime (nearest lower interval)
            regime_key = (i // REGIME_INTERVAL) * REGIME_INTERVAL
            if regime_key < MIN_CANDLES:
                regime_key = MIN_CANDLES
            regime = regime_cache.get(regime_key)
            if regime is None:
                # Fallback: compute
                start_idx = max(0, i - WINDOW_SIZE)
                window = candles.iloc[start_idx:i + 1]
                regime = classify_regime(window)
                regime_cache[regime_key] = regime

            # --- สร้าง window สำหรับ strategy ---
            start_idx = max(0, i - WINDOW_SIZE)
            window = candles.iloc[start_idx:i + 1]

            # --- ถาม strategy ---
            try:
                decision = strategy.analyze(window, profile, regime)
            except Exception:
                continue

            if decision.action not in (Action.BUY, Action.SELL):
                continue
            if decision.stop_loss is None:
                continue

            # --- Lookup patterns from batch (O(1)) ---
            detected_patterns: list[str] = []
            if pattern_batch:
                signals = pattern_batch.get(i, [])
                detected_patterns = [s.name for s in signals]

            # --- Lookup news from batch (O(1)) ---
            news_ctx = "NONE"
            if news_batch:
                ctx = news_batch.get(i)
                if ctx and ctx.near_news:
                    news_ctx = ctx.impact

            # --- Simulate trade ---
            entry_price = candles.iloc[i]["close"]
            next_bar = candles.iloc[i + 1]
            trade_result = self._simulate_trade(
                action=decision.action,
                entry=entry_price,
                sl=decision.stop_loss,
                tp=decision.take_profit,
                next_high=next_bar["high"],
                next_low=next_bar["low"],
                next_close=next_bar["close"],
            )

            trade_result["patterns"] = detected_patterns
            trade_result["news_context"] = news_ctx
            trades.append(trade_result)

            # Track pattern-level outcomes
            for p_name in detected_patterns:
                if p_name not in pattern_trades:
                    pattern_trades[p_name] = []
                pattern_trades[p_name].append(trade_result["is_win"])

            # Track news-level outcomes
            if news_ctx not in news_trades:
                news_trades[news_ctx] = []
            news_trades[news_ctx].append(trade_result["is_win"])

            # Yield control periodically
            if len(trades) % 50 == 0:
                await asyncio.sleep(0)

        # --- คำนวณ metrics ---
        return self._compute_metrics(
            trades=trades,
            strategy_name=strategy_name,
            symbol=symbol,
            params=params or {},
            candles_tested=total_candles,
            pattern_trades=pattern_trades,
            news_trades=news_trades,
        )

    # ────────────────────────────────────────────────────────────────
    # run_tournament — ทดสอบทุก strategy แล้วจัดอันดับ
    # ────────────────────────────────────────────────────────────────

    async def run_tournament(
        self,
        symbol: str,
        candles: pd.DataFrame,
        profile: SymbolProfile | None = None,
        strategies: list[str] | None = None,
    ) -> list[PracticeResult]:
        """
        รันทุก strategy บน candles เดียวกัน → จัดอันดับตาม score.

        Args:
            symbol: สัญลักษณ์ที่ทดสอบ
            candles: แท่งเทียน historical
            profile: SymbolProfile
            strategies: รายชื่อ strategies (ถ้าไม่ส่ง → ใช้ทั้งหมดจาก Factory)

        Returns:
            list[PracticeResult] sorted by score descending
        """
        if not self.factory:
            return []

        # --- รายชื่อ strategies ที่จะทดสอบ ---
        if strategies:
            strat_names = strategies
        else:
            strat_names = list(self.factory._strategies.keys())

        results: list[PracticeResult] = []
        for name in strat_names:
            try:
                result = await self.run_practice(
                    symbol=symbol,
                    strategy_name=name,
                    candles=candles,
                    profile=profile,
                )
                results.append(result)
            except Exception as e:
                logger.debug("practice_strategy_error", extra={
                    "strategy": name, "error": str(e),
                })

        # จัดอันดับตาม score (สูง → ดี)
        results.sort(key=lambda r: r.score, reverse=True)

        logger.info("tournament_complete", extra={
            "symbol": symbol,
            "strategies_tested": len(results),
            "best": results[0].strategy_name if results else "none",
            "best_score": round(results[0].score, 3) if results else 0,
        })

        return results

    # ────────────────────────────────────────────────────────────────
    # Private: _simulate_trade — จำลอง 1 trade
    # ────────────────────────────────────────────────────────────────

    def _simulate_trade(
        self,
        action: Action,
        entry: float,
        sl: float,
        tp: float | None,
        next_high: float,
        next_low: float,
        next_close: float,
    ) -> dict:
        """
        จำลองผลลัพธ์เทรด 1 ตัวจากแท่งเทียนถัดไป.

        Logic:
            BUY:  ถ้า low <= SL → SL hit (loss),  ถ้า high >= TP → TP hit (win)
            SELL: ถ้า high >= SL → SL hit (loss), ถ้า low <= TP → TP hit (win)

            ถ้าทั้ง SL และ TP hit ในแท่งเดียว → ถือว่า SL hit ก่อน (conservative)
            ถ้าไม่มีตัวไหน hit → ปิดที่ close ของแท่งถัดไป

        Returns:
            dict: {r_value, is_win, exit_reason}
        """
        sl_distance = abs(entry - sl)
        if sl_distance <= 0:
            return {"r_value": 0.0, "is_win": False, "exit_reason": "zero_sl"}

        if action == Action.BUY:
            sl_hit = next_low <= sl
            tp_hit = tp is not None and next_high >= tp

            if sl_hit and tp_hit:
                # ทั้ง SL และ TP hit → conservative: ถือว่า SL ก่อน
                r_value = -1.0
                is_win = False
                reason = "sl_hit_conservative"
            elif sl_hit:
                r_value = -1.0
                is_win = False
                reason = "sl_hit"
            elif tp_hit and tp is not None:
                r_value = (tp - entry) / sl_distance
                is_win = True
                reason = "tp_hit"
            else:
                # ปิดที่ close
                r_value = (next_close - entry) / sl_distance
                is_win = r_value > 0
                reason = "bar_close"

        else:  # SELL
            sl_hit = next_high >= sl
            tp_hit = tp is not None and next_low <= tp

            if sl_hit and tp_hit:
                r_value = -1.0
                is_win = False
                reason = "sl_hit_conservative"
            elif sl_hit:
                r_value = -1.0
                is_win = False
                reason = "sl_hit"
            elif tp_hit and tp is not None:
                r_value = (entry - tp) / sl_distance
                is_win = True
                reason = "tp_hit"
            else:
                r_value = (entry - next_close) / sl_distance
                is_win = r_value > 0
                reason = "bar_close"

        return {"r_value": r_value, "is_win": is_win, "exit_reason": reason}

    # ────────────────────────────────────────────────────────────────
    # Private: _compute_metrics — คำนวณ performance metrics
    # ────────────────────────────────────────────────────────────────

    def _compute_metrics(
        self,
        trades: list[dict],
        strategy_name: str,
        symbol: str,
        params: dict,
        candles_tested: int = 0,
        pattern_trades: dict | None = None,
        news_trades: dict | None = None,
    ) -> PracticeResult:
        """
        คำนวณ performance metrics จาก simulated trades.

        Metrics:
            - win_rate: จำนวนชนะ / ทั้งหมด
            - profit_factor: total_positive_R / |total_negative_R|
            - max_drawdown: drawdown สูงสุด (% of peak)
            - expectancy: (WR × avg_win) - ((1-WR) × avg_loss)  ในหน่วย R
            - score: PF × WR × (1 - DD/100)
            - patterns_found: pattern → count
            - pattern_win_rates: pattern → win_rate
            - news_stats: news_ctx → {count, win_rate}
        """
        total = len(trades)
        if total == 0:
            return PracticeResult(
                strategy_name=strategy_name,
                symbol=symbol,
                params=params,
                candles_tested=candles_tested,
            )

        wins = sum(1 for t in trades if t["is_win"])
        losses = total - wins
        win_rate = wins / total

        # --- R-based metrics ---
        total_r = sum(t["r_value"] for t in trades)
        positive_r = sum(t["r_value"] for t in trades if t["r_value"] > 0)
        negative_r = abs(sum(t["r_value"] for t in trades if t["r_value"] < 0))

        profit_factor = positive_r / negative_r if negative_r > 0 else (positive_r if positive_r > 0 else 0)

        # --- Average R:R ---
        winning_trades = [t for t in trades if t["is_win"]]
        avg_rr = (
            sum(t["r_value"] for t in winning_trades) / len(winning_trades)
            if winning_trades else 0.0
        )

        # --- Expectancy ---
        avg_win_r = positive_r / wins if wins > 0 else 0
        avg_loss_r = negative_r / losses if losses > 0 else 0
        expectancy = (win_rate * avg_win_r) - ((1 - win_rate) * avg_loss_r)

        # --- Max Drawdown (R-based equity curve) ---
        equity_curve = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in trades:
            equity_curve += t["r_value"]
            peak = max(peak, equity_curve)
            dd = peak - equity_curve
            max_dd = max(max_dd, dd)

        # แปลง DD เป็น % ของ peak (ถ้า peak > 0)
        dd_pct = (max_dd / peak * 100) if peak > 0 else 0

        # --- Composite Score ---
        # PF × WR × (1 - DD/100) → ค่าสูง = ดี
        # Cap PF ที่ 10 เพื่อไม่ให้ค่าสุดขั้วครอบงำ
        capped_pf = min(profit_factor, 10.0)
        score = capped_pf * win_rate * max(0, 1 - dd_pct / 100)

        # --- Pattern stats ---
        patterns_found = {}
        pattern_win_rates = {}
        if pattern_trades:
            for p_name, outcomes in pattern_trades.items():
                count = len(outcomes)
                patterns_found[p_name] = count
                pattern_win_rates[p_name] = round(
                    sum(1 for w in outcomes if w) / count, 4
                ) if count > 0 else 0.0

        # --- News stats ---
        news_stats = {}
        if news_trades:
            for ctx, outcomes in news_trades.items():
                count = len(outcomes)
                wr = round(sum(1 for w in outcomes if w) / count, 4) if count > 0 else 0.0
                news_stats[ctx] = {"count": count, "win_rate": wr}

        return PracticeResult(
            strategy_name=strategy_name,
            symbol=symbol,
            params=params,
            total_trades=total,
            wins=wins,
            losses=losses,
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 3),
            max_drawdown=round(dd_pct, 2),
            total_r=round(total_r, 2),
            expectancy=round(expectancy, 4),
            avg_rr=round(avg_rr, 2),
            score=round(score, 4),
            candles_tested=candles_tested,
            patterns_found=patterns_found,
            pattern_win_rates=pattern_win_rates,
            news_stats=news_stats,
        )

    # ────────────────────────────────────────────────────────────────
    # Private: _get_strategy
    # ────────────────────────────────────────────────────────────────

    def _get_strategy(self, name: str):
        """ดึง strategy instance จาก Factory."""
        if not self.factory:
            return None
        return self.factory._strategies.get(name)
