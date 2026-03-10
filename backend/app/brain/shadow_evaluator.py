"""
Shadow Evaluator — Evaluate shadow trade outcomes using subsequent price action.

Purpose:
    - Check if virtual BUY/SELL signals would have been profitable
    - Compare entry_price vs SL/TP against candles that formed AFTER the signal
    - Feed WIN/LOSS results into MemoryStore for brain learning
    - Refresh the shadow_scoreboard so the factory can learn which strategies work best

How it works:
    1. Query unevaluated shadow trades from SQLite
    2. For each trade, fetch M5 candles after the signal timestamp
    3. Walk forward through candles:
       - If price hits TP first → WIN
       - If price hits SL first → LOSS
       - If neither hit within lookback window → mark EXPIRED
    4. Batch update outcomes in SQLite
    5. Feed results into MemoryStore.record_trade_outcome()
    6. Refresh scoreboard aggregates

Performance:
    - Batch processing (up to 200 trades per cycle)
    - Uses existing candle data where possible
    - Runs every ~60 cycles in MasterLoop (non-blocking)
"""

from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd

from app.core.logging import get_logger
from app.db.sqlite import SQLiteStore
from app.brain.memory_store import MemoryStore

logger = get_logger(__name__)

# Max candles to look forward after entry (M5 = 5 min per candle)
# 48 candles = 4 hours lookback window
MAX_LOOKFORWARD_BARS = 48

# Min age before evaluating (minutes) — wait for candles to form
MIN_AGE_MINUTES = 30


class ShadowEvaluator:
    """
    Evaluate shadow trades against real price action.

    Determines if virtual signals would have been profitable,
    then feeds results into the AI Brain for continuous learning.
    """

    def __init__(
        self,
        db: SQLiteStore | None = None,
        memory: MemoryStore | None = None,
    ) -> None:
        self.db = db
        self.memory = memory
        self._candle_cache: dict[str, pd.DataFrame] = {}

    def evaluate_pending(
        self,
        candles_by_symbol: dict[str, pd.DataFrame] | None = None,
    ) -> dict:
        """
        Evaluate all pending shadow trades.

        Args:
            candles_by_symbol: dict of symbol → M5 candles (from master loop)

        Returns:
            dict: {"evaluated": N, "wins": N, "losses": N, "expired": N}
        """
        if not self.db:
            return {"evaluated": 0, "wins": 0, "losses": 0, "expired": 0}

        # Cache candles for this evaluation cycle
        if candles_by_symbol:
            self._candle_cache = candles_by_symbol

        # 1. Get unevaluated shadow trades
        pending = self.db.get_unevaluated_shadows(limit=200)
        if not pending:
            return {"evaluated": 0, "wins": 0, "losses": 0, "expired": 0}

        # Filter: only evaluate trades old enough (MIN_AGE_MINUTES)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=MIN_AGE_MINUTES)
        ready = []
        for trade in pending:
            try:
                ts = datetime.fromisoformat(trade["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff:
                    ready.append(trade)
            except (ValueError, KeyError):
                continue

        if not ready:
            return {"evaluated": 0, "wins": 0, "losses": 0, "expired": 0}

        # 2. Evaluate each trade
        updates = []  # (outcome, pnl, id)
        wins, losses, expired = 0, 0, 0

        for trade in ready:
            outcome, pnl = self._evaluate_single(trade)
            updates.append((outcome, pnl, trade["id"]))

            if outcome == "WIN":
                wins += 1
            elif outcome == "LOSS":
                losses += 1
            else:
                expired += 1

            # 3. Feed into brain (if available)
            if self.memory and outcome in ("WIN", "LOSS"):
                try:
                    profit = pnl if outcome == "WIN" else -abs(pnl)
                    self.memory.record_trade_outcome(
                        strategy_name=trade["strategy_name"],
                        symbol=trade["symbol"],
                        regime=trade.get("regime", "UNKNOWN"),
                        session=trade.get("session", ""),
                        profit_usd=profit,
                        risk_reward=self._calc_rr(trade),
                    )
                except Exception as e:
                    logger.debug("shadow_brain_feed_error", extra={
                        "error": str(e), "trade_id": trade["id"],
                    })

        # 4. Batch update outcomes in DB
        if updates:
            self.db.update_shadow_outcomes_batch(updates)

        # 5. Refresh scoreboard
        self._refresh_scoreboard()

        evaluated = wins + losses + expired
        if evaluated > 0:
            logger.info("shadow_evaluation_complete", extra={
                "evaluated": evaluated,
                "wins": wins,
                "losses": losses,
                "expired": expired,
            })

        return {
            "evaluated": evaluated,
            "wins": wins,
            "losses": losses,
            "expired": expired,
        }

    def _evaluate_single(self, trade: dict) -> tuple[str, float]:
        """
        Evaluate a single shadow trade against price action.

        Returns:
            (outcome, pnl_distance) — "WIN"/"LOSS"/"EXPIRED", pip distance
        """
        symbol = trade["symbol"]
        action = trade["action"]
        entry = trade["entry_price"]
        sl = trade["stop_loss"]
        tp = trade["take_profit"]

        # Need valid SL and TP to evaluate
        if not entry or not sl or not tp or entry <= 0:
            return "EXPIRED", 0.0

        # Get candles for this symbol
        candles = self._candle_cache.get(symbol)
        if candles is None or candles.empty:
            return "EXPIRED", 0.0

        # Find candles AFTER the trade timestamp
        try:
            trade_ts = pd.Timestamp(trade["timestamp"])
            if trade_ts.tzinfo is None:
                trade_ts = trade_ts.tz_localize("UTC")

            # Filter candles after trade entry
            if "time" in candles.columns:
                time_col = "time"
            elif "datetime" in candles.columns:
                time_col = "datetime"
            else:
                return "EXPIRED", 0.0

            after = candles[candles[time_col] > trade_ts]
            if after.empty or len(after) < 2:
                return "EXPIRED", 0.0

            # Walk forward through candles (max lookback)
            bars = after.head(MAX_LOOKFORWARD_BARS)

        except Exception:
            return "EXPIRED", 0.0

        # Walk forward: check if TP or SL hit first
        for _, bar in bars.iterrows():
            high = bar.get("high", 0)
            low = bar.get("low", 0)
            if high <= 0 or low <= 0:
                continue

            if action == "BUY":
                # BUY: TP is above entry, SL is below entry
                if high >= tp:
                    pnl = abs(tp - entry)
                    return "WIN", pnl
                if low <= sl:
                    pnl = abs(entry - sl)
                    return "LOSS", pnl

            elif action == "SELL":
                # SELL: TP is below entry, SL is above entry
                if low <= tp:
                    pnl = abs(entry - tp)
                    return "WIN", pnl
                if high >= sl:
                    pnl = abs(sl - entry)
                    return "LOSS", pnl

        # Neither TP nor SL hit within lookback window
        return "EXPIRED", 0.0

    def _calc_rr(self, trade: dict) -> float:
        """Calculate risk:reward ratio from entry/SL/TP."""
        entry = trade.get("entry_price", 0)
        sl = trade.get("stop_loss", 0)
        tp = trade.get("take_profit", 0)
        if not entry or not sl or not tp:
            return 0.0
        sl_dist = abs(entry - sl)
        tp_dist = abs(tp - entry)
        return round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0

    def _refresh_scoreboard(self) -> None:
        """Refresh shadow_scoreboard table from aggregated shadow trades."""
        if not self.db:
            return
        try:
            stats = self.db.get_shadow_stats_for_scoring()
            for row in stats:
                self.db.upsert_shadow_score(
                    strategy_name=row["strategy_name"],
                    symbol=row["symbol"],
                    regime=row.get("regime", "ALL") or "ALL",
                    wins=row.get("wins", 0) or 0,
                    losses=row.get("losses", 0) or 0,
                    pending=row.get("pending", 0) or 0,
                    total_pnl=row.get("total_pnl", 0) or 0,
                    avg_confidence=row.get("avg_confidence", 0) or 0,
                )
        except Exception as e:
            logger.error("shadow_scoreboard_refresh_error", extra={"error": str(e)})

    def clear_cache(self) -> None:
        """Clear candle cache to free memory."""
        self._candle_cache.clear()
