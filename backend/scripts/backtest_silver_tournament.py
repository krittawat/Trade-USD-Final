"""
Silver Elite Tournament — 3 Parameter Variants.

Custom backtester loop optimized for M15 Silver:
- Analyzes EVERY bar (no skip)
- NO regime actionable filter (strategy decides internally)
- Supports dynamic parameter patching

Usage:
    python scripts/backtest_silver_tournament.py --days 200
"""
print("DEBUG: Silver Tournament Start", flush=True)

import sys
import os
import argparse
import time
import sqlite3
import importlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd
import numpy as np

from app.core.logging import get_logger
from app.execution.backtester import BacktestTrade, BacktestResult
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile, AccountState
from app.brain.regime import classify_regime
from app.risk.sizing import calculate_lot_size
from app.core.config import get_settings

logger = get_logger("SilverTournament")
settings = get_settings()

SYMBOL = "XAGUSDc"
TIMEFRAME = mt5.TIMEFRAME_M15
CONTRACT_SIZE = 5000.0
POINT = 0.001
DIGITS = 3

# ═════════════════════════════════════════════
# VARIANT DEFINITIONS
# ═════════════════════════════════════════════

# V1: Aggressive Mean-Reversion — BB tight + RSI wide + all sessions
V1_AGGRESSIVE_MR = {
    "name": "V1_AggressiveMR",
    "BB_LEN": 15, "BB_STD": 1.5,              # ← BB แคบมาก = touch บ่อย
    "RSI_PERIOD": 10, "RSI_BUY": 45, "RSI_SELL": 55,  # ← RSI กว้างมาก
    "RSI_EXTREME_BUY": 35, "RSI_EXTREME_SELL": 65,
    "EMA_MID": 34, "EMA_TREND": 100,          # ← EMA สั้นลง
    "ADX_PERIOD": 14, "ADX_RANGING": 35, "ADX_TRENDING": 35, "ADX_STRONG": 45,  # ← เกือบทุก bar = ranging
    "ATR_PERIOD": 14,
    "SL_ATR_MULT_RANGE": 1.5, "SL_ATR_MULT_BREAK": 2.0,
    "RR_MEAN_REV": 0.8, "RR_BREAKOUT": 1.2,   # ← TP ต่ำ = WR สูง
    "MIN_SL_DISTANCE": 0.02,
    "BB_SQUEEZE_PERCENTILE": 30,
    "VOL_MA": 15, "VOL_SPIKE_RATIO": 1.1,     # ← Vol confirm ง่าย
    "LONDON_START": 1, "LONDON_END": 23,       # ← เทรดได้เกือบ 24 ชม.
    "NY_START": 0, "NY_END": 24,
    "MIN_BARS_BETWEEN_TRADES": 1,              # ← Cooldown 1 bar = 15 นาที
}

# V2: Single-Condition All-Session — BB OR RSI + เทรดได้ทุก session
V2_SINGLE_ALL = {
    "name": "V2_SingleAll",
    "BB_LEN": 18, "BB_STD": 1.8,
    "RSI_PERIOD": 10, "RSI_BUY": 42, "RSI_SELL": 58,
    "RSI_EXTREME_BUY": 32, "RSI_EXTREME_SELL": 68,
    "EMA_MID": 34, "EMA_TREND": 144,
    "ADX_PERIOD": 14, "ADX_RANGING": 30, "ADX_TRENDING": 30, "ADX_STRONG": 40,
    "ATR_PERIOD": 14,
    "SL_ATR_MULT_RANGE": 1.5, "SL_ATR_MULT_BREAK": 2.0,
    "RR_MEAN_REV": 1.0, "RR_BREAKOUT": 1.5,
    "MIN_SL_DISTANCE": 0.025,
    "BB_SQUEEZE_PERCENTILE": 30,
    "VOL_MA": 15, "VOL_SPIKE_RATIO": 1.1,
    "LONDON_START": 0, "LONDON_END": 24,       # ← เทรดได้ 24 ชม.
    "NY_START": 0, "NY_END": 24,
    "MIN_BARS_BETWEEN_TRADES": 1,
    "_single_condition": True,                  # ← BB OR RSI
}

# V3: Silver Sniper — Balanced (BB 1.6 + RSI 40/60 + London+NY + lower TP)
V3_SILVER_SNIPER = {
    "name": "V3_SilverSniper",
    "BB_LEN": 16, "BB_STD": 1.6,              # ← BB ค่อนข้างแคบ
    "RSI_PERIOD": 8, "RSI_BUY": 40, "RSI_SELL": 60,
    "RSI_EXTREME_BUY": 30, "RSI_EXTREME_SELL": 70,
    "EMA_MID": 21, "EMA_TREND": 89,           # ← Fibonacci short
    "ADX_PERIOD": 14, "ADX_RANGING": 30, "ADX_TRENDING": 30, "ADX_STRONG": 40,
    "ATR_PERIOD": 14,
    "SL_ATR_MULT_RANGE": 1.2, "SL_ATR_MULT_BREAK": 1.8,
    "RR_MEAN_REV": 0.8, "RR_BREAKOUT": 1.2,
    "MIN_SL_DISTANCE": 0.02,
    "BB_SQUEEZE_PERCENTILE": 35,
    "VOL_MA": 10, "VOL_SPIKE_RATIO": 1.0,     # ← ไม่ filter volume
    "LONDON_START": 2, "LONDON_END": 22,       # ← เกือบทั้งวัน
    "NY_START": 0, "NY_END": 24,
    "MIN_BARS_BETWEEN_TRADES": 1,
}

ALL_VARIANTS = [V1_AGGRESSIVE_MR, V2_SINGLE_ALL, V3_SILVER_SNIPER]


# ═════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════

def _get_sl_tp(decision):
    sl = getattr(decision, 'sl', None) or getattr(decision, 'stop_loss', None) or 0.0
    tp = getattr(decision, 'tp', None) or getattr(decision, 'take_profit', None) or 0.0
    return sl, tp


def patch_module(mod, variant):
    """Patch silver_elite module constants with variant values."""
    for key, val in variant.items():
        if key.startswith("_") or key == "name":
            continue
        if hasattr(mod, key):
            setattr(mod, key, val)


# ═════════════════════════════════════════════
# SIMPLE SILVER BACKTESTER (no regime filter, every bar)
# ═════════════════════════════════════════════

def run_silver_backtest(strategy, candles, symbol, initial_equity=10000.0):
    """
    Simplified backtester for Silver M15:
    - Analyzes EVERY bar (no i%5 skip)
    - NO regime actionable filter
    - Standard SL/TP exit logic
    """
    total_bars = len(candles)
    warmup = 220  # EMA200 + buffer
    equity = initial_equity
    peak_equity = initial_equity
    max_dd_pct = 0.0
    open_trade = None
    closed_trades = []
    trade_counter = 0

    is_cent = str(symbol).upper().endswith("C")
    profile = SymbolProfile(
        symbol=symbol, contract_size=CONTRACT_SIZE, point=POINT,
        digits=DIGITS, volume_min=0.0001 if is_cent else 0.01,
        volume_max=200.0 if is_cent else 100.0, volume_step=0.01,
    )

    metrics = {
        "current_loss_streak": 0,
        "current_win_streak": 0,
        "max_drawdown_pct": 0.0,
        "risk_modifier": 1.0,
    }

    for i in range(warmup, total_bars):
        if i % 2000 == 0:
            print(f"  Bar {i}/{total_bars}", flush=True)

        bar = candles.iloc[i]
        bar_time = str(bar.get("time", i))
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])
        bar_close = float(bar["close"])

        # ─── Manage Open Trade ───
        if open_trade is not None:
            open_trade.bars_held += 1
            exit_happened = False

            if open_trade.action == "BUY":
                if open_trade.sl > 0 and bar_low <= open_trade.sl:
                    open_trade.exit_price = open_trade.sl
                    open_trade.exit_reason = "SL"
                    exit_happened = True
                elif open_trade.tp > 0 and bar_high >= open_trade.tp:
                    open_trade.exit_price = open_trade.tp
                    open_trade.exit_reason = "TP"
                    exit_happened = True
            else:
                if open_trade.sl > 0 and bar_high >= open_trade.sl:
                    open_trade.exit_price = open_trade.sl
                    open_trade.exit_reason = "SL"
                    exit_happened = True
                elif open_trade.tp > 0 and bar_low <= open_trade.tp:
                    open_trade.exit_price = open_trade.tp
                    open_trade.exit_reason = "TP"
                    exit_happened = True

            if exit_happened:
                open_trade.exit_time = bar_time
                # Calculate P&L
                if open_trade.action == "BUY":
                    pnl = (open_trade.exit_price - open_trade.entry_price) * open_trade.lot_size * CONTRACT_SIZE
                else:
                    pnl = (open_trade.entry_price - open_trade.exit_price) * open_trade.lot_size * CONTRACT_SIZE
                open_trade.profit_usd = round(pnl, 2)
                equity += pnl

                if pnl > 0:
                    metrics["current_win_streak"] += 1
                    metrics["current_loss_streak"] = 0
                else:
                    metrics["current_loss_streak"] += 1
                    metrics["current_win_streak"] = 0

                closed_trades.append(open_trade)
                open_trade = None

        # ─── Track DD ───
        if equity > peak_equity:
            peak_equity = equity
        dd_pct = (peak_equity - equity) / peak_equity * 100 if peak_equity > 0 else 0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
        metrics["max_drawdown_pct"] = max_dd_pct

        # ─── Signal: analyze every bar, no regime filter ───
        if open_trade is not None:
            continue  # Already in position

        window_start = max(0, i - 299)
        history = candles.iloc[window_start:i + 1]
        if len(history) < 120:
            continue

        # Classify regime for tagging only, NOT for filtering
        regime = classify_regime(history)

        try:
            decision = strategy.analyze(history, profile, regime.regime)
        except Exception:
            continue

        if decision.action not in (Action.BUY, Action.SELL):
            continue

        _sl, _tp = _get_sl_tp(decision)
        if not _sl or _sl <= 0:
            continue

        # ─── Sizing ───
        r_pct = getattr(decision, 'risk_pct', None)
        if r_pct is not None and r_pct < 1.0:
            r_pct *= 100.0

        sizing_dec = Decision(
            symbol=symbol,
            action=decision.action,
            confidence=min(max(decision.confidence, 0.0), 1.0),
            reason=decision.reason,
            stop_loss=_sl,
            take_profit=_tp,
            risk_pct=r_pct,
            strategy_name=strategy.name,
            timeframe="M15",
        )
        account_state = AccountState(
            balance=equity, equity=equity, margin=0, margin_free=equity,
        )

        try:
            plan = calculate_lot_size(
                decision=sizing_dec, profile=profile,
                account=account_state, settings=settings,
                entry_price=bar_close, stop_loss=_sl,
                performance_metrics=metrics,
            )
        except Exception:
            continue

        if not hasattr(plan, 'lot_size') or plan.lot_size <= 0:
            continue

        # ─── Open Trade ───
        trade_counter += 1
        open_trade = BacktestTrade(
            trade_id=trade_counter,
            symbol=symbol,
            strategy=strategy.name,
            action=decision.action.value,
            entry_price=bar_close,
            entry_time=bar_time,
            sl=_sl,
            tp=_tp,
            lot_size=plan.lot_size,
            regime=regime.regime.value,
        )

    # Close at end
    if open_trade:
        bar_close = float(candles.iloc[-1]["close"])
        open_trade.exit_price = bar_close
        open_trade.exit_time = str(candles.iloc[-1]["time"])
        open_trade.exit_reason = "END"
        if open_trade.action == "BUY":
            pnl = (open_trade.exit_price - open_trade.entry_price) * open_trade.lot_size * CONTRACT_SIZE
        else:
            pnl = (open_trade.entry_price - open_trade.exit_price) * open_trade.lot_size * CONTRACT_SIZE
        open_trade.profit_usd = round(pnl, 2)
        equity += pnl
        closed_trades.append(open_trade)

    # ─── Compute Stats ───
    total = len(closed_trades)
    wins = [t for t in closed_trades if t.profit_usd > 0]
    losses = [t for t in closed_trades if t.profit_usd <= 0]
    gross_profit = sum(t.profit_usd for t in wins)
    gross_loss = abs(sum(t.profit_usd for t in losses))
    total_pnl = sum(t.profit_usd for t in closed_trades)

    wr = round(len(wins) / total * 100, 1) if total > 0 else 0.0
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    # Per-regime breakdown
    per_regime = {}
    for t in closed_trades:
        r = t.regime
        if r not in per_regime:
            per_regime[r] = {"trades": 0, "wins": 0, "pnl": 0.0}
        per_regime[r]["trades"] += 1
        per_regime[r]["pnl"] += t.profit_usd
        if t.profit_usd > 0:
            per_regime[r]["wins"] += 1
    for r, s in per_regime.items():
        s["win_rate"] = round(s["wins"] / s["trades"] * 100, 1) if s["trades"] > 0 else 0
        s["pnl"] = round(s["pnl"], 2)

    # Compute extra stats
    win_count = len(wins)
    loss_count = len(losses)
    expectancy = round(total_pnl / total, 2) if total > 0 else 0
    avg_bars = round(sum(t.bars_held for t in closed_trades) / total, 1) if total > 0 else 0

    trade_dicts = []
    for t in closed_trades:
        trade_dicts.append({
            "id": t.trade_id, "action": t.action,
            "entry": t.entry_price, "exit": t.exit_price,
            "pnl": t.profit_usd, "reason": t.exit_reason,
            "regime": t.regime, "bars": t.bars_held,
        })

    return BacktestResult(
        symbol=symbol,
        strategy=strategy.name,
        start_date="",
        end_date="",
        total_bars=total_bars,
        total_trades=total,
        winning_trades=win_count,
        losing_trades=loss_count,
        win_rate=wr,
        profit_factor=pf,
        total_profit_usd=round(total_pnl, 2),
        max_drawdown_pct=round(max_dd_pct, 1),
        max_drawdown_usd=0.0,
        expectancy=expectancy,
        avg_rr=0.0,
        sharpe_ratio=0.0,
        avg_bars_held=avg_bars,
        initial_equity=initial_equity,
        final_equity=round(equity, 2),
        equity_curve=[],
        trades=trade_dicts,
        per_regime=per_regime,
        duration_seconds=0.0,
    )


# ═════════════════════════════════════════════
# V2: Single-Condition Strategy
# ═════════════════════════════════════════════

class SingleCondSilverElite:
    """V2: BB touch OR RSI extreme — single condition entry."""
    name = "silver_elite_v2"
    timeframe = "M15"

    def __init__(self):
        self._last_signal_bar = -999

    def create_hold(self, symbol="XAGUSDc", reason=""):
        return Decision(
            symbol=symbol, action=Action.HOLD, confidence=0.0,
            reason=reason, strategy_name=self.name, timeframe=self.timeframe,
        )

    def analyze(self, candles, profile, regime=RegimeType.UNKNOWN):
        import app.strategy.templates.silver_elite as P

        symbol = profile.symbol if hasattr(profile, 'symbol') else "XAGUSDc"
        min_bars = max(P.EMA_TREND + 20, 120)
        if candles is None or len(candles) < min_bars:
            return self.create_hold(symbol=symbol, reason="Data insufficient")

        # Session
        if "time" in candles.columns:
            current_time = candles["time"].iloc[-1]
            hour = current_time.hour if hasattr(current_time, 'hour') else pd.Timestamp(current_time).hour
            in_london = P.LONDON_START <= hour < P.LONDON_END
            in_ny = P.NY_START <= hour < P.NY_END
            if not (in_london or in_ny):
                return self.create_hold(symbol=symbol, reason="Session")

        bar_idx = len(candles) - 1
        if (bar_idx - self._last_signal_bar) < P.MIN_BARS_BETWEEN_TRADES:
            return self.create_hold(symbol=symbol, reason="Cooldown")

        close = candles["close"].astype(float)
        high = candles["high"].astype(float)
        low = candles["low"].astype(float)
        current_close = float(close.iloc[-1])

        ema_mid = close.ewm(span=P.EMA_MID, adjust=False).mean()
        ema_trend = close.ewm(span=P.EMA_TREND, adjust=False).mean()
        ema_m_val = float(ema_mid.iloc[-1])
        ema_t_val = float(ema_trend.iloc[-1])

        # BB
        bb_mid = close.rolling(P.BB_LEN).mean()
        bb_std = close.rolling(P.BB_LEN).std()
        bb_upper = bb_mid + (P.BB_STD * bb_std)
        bb_lower = bb_mid - (P.BB_STD * bb_std)
        bb_mid_val = float(bb_mid.iloc[-1])
        bb_upper_val = float(bb_upper.iloc[-1])
        bb_lower_val = float(bb_lower.iloc[-1])

        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0.0).rolling(P.RSI_PERIOD).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(P.RSI_PERIOD).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50.0

        # ATR
        tr = pd.concat([
            high - low, (high - close.shift()).abs(), (low - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr_val = float(tr.rolling(P.ATR_PERIOD).mean().iloc[-1])

        # ADX
        up_move = high - high.shift()
        down_move = low.shift() - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        plus_dm_s = pd.Series(plus_dm, index=high.index).rolling(P.ADX_PERIOD).mean()
        minus_dm_s = pd.Series(minus_dm, index=high.index).rolling(P.ADX_PERIOD).mean()
        atr_adx = tr.rolling(P.ADX_PERIOD).mean()
        di_plus = 100 * plus_dm_s / (atr_adx + 1e-10)
        di_minus = 100 * minus_dm_s / (atr_adx + 1e-10)
        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus + 1e-10)
        adx_val = float(dx.rolling(P.ADX_PERIOD).mean().iloc[-1])
        if pd.isna(adx_val): adx_val = 0.0

        if any(pd.isna(v) for v in [bb_mid_val, rsi_val, atr_val]):
            return self.create_hold(symbol=symbol, reason="NaN")
        if atr_val <= 0:
            return self.create_hold(symbol=symbol, reason="ATR=0")

        # Volume
        vol_col = "tick_volume" if "tick_volume" in candles.columns else "volume"
        vol = candles[vol_col].astype(float) if vol_col in candles.columns else None
        vol_current = float(vol.iloc[-1]) if vol is not None else 0
        vol_avg = float(vol.rolling(P.VOL_MA).mean().iloc[-1]) if vol is not None else 0
        vol_spike = vol_avg > 0 and vol_current > vol_avg * P.VOL_SPIKE_RATIO

        ema_distance_atr = abs(current_close - ema_m_val) / atr_val
        if ema_distance_atr > 4.0:
            return self.create_hold(symbol=symbol, reason="Too far from EMA")

        # ═══ MODE ═══
        if adx_val < P.ADX_RANGING:
            mode = "RANGING"
        else:
            mode = "BREAKOUT"

        action = Action.HOLD
        confidence = 0.0
        reasons = []
        sl_price = None
        tp_price = None

        if mode == "RANGING":
            bb_buy = current_close < bb_lower_val
            rsi_buy = rsi_val < P.RSI_BUY
            bb_sell = current_close > bb_upper_val
            rsi_sell = rsi_val > P.RSI_SELL

            if bb_buy or rsi_buy:
                action = Action.BUY
                confidence = 55.0
                if bb_buy:
                    confidence += 10
                    reasons.append(f"BB_L({current_close:.3f}<{bb_lower_val:.3f})")
                if rsi_buy:
                    confidence += 10
                    reasons.append(f"RSI({rsi_val:.0f})")
                if rsi_val < P.RSI_EXTREME_BUY:
                    confidence += 10
                if vol_spike:
                    confidence += 5
                sl_price = current_close - (atr_val * P.SL_ATR_MULT_RANGE)
                tp_price = bb_mid_val

            elif bb_sell or rsi_sell:
                action = Action.SELL
                confidence = 55.0
                if bb_sell:
                    confidence += 10
                    reasons.append(f"BB_U({current_close:.3f}>{bb_upper_val:.3f})")
                if rsi_sell:
                    confidence += 10
                    reasons.append(f"RSI({rsi_val:.0f})")
                if rsi_val > P.RSI_EXTREME_SELL:
                    confidence += 10
                if vol_spike:
                    confidence += 5
                sl_price = current_close + (atr_val * P.SL_ATR_MULT_RANGE)
                tp_price = bb_mid_val
            else:
                return self.create_hold(symbol=symbol, reason="No range signal")

        else:  # BREAKOUT
            trend_up = ema_m_val > ema_t_val
            trend_down = ema_m_val < ema_t_val

            if current_close > bb_upper_val and (trend_up or vol_spike):
                action = Action.BUY
                confidence = 65.0
                reasons.append("BO:BUY")
                if trend_up: confidence += 10
                if vol_spike: confidence += 10
                sl_price = current_close - (atr_val * P.SL_ATR_MULT_BREAK)
                sl_dist = current_close - sl_price
                tp_price = current_close + (sl_dist * P.RR_BREAKOUT)

            elif current_close < bb_lower_val and (trend_down or vol_spike):
                action = Action.SELL
                confidence = 65.0
                reasons.append("BO:SELL")
                if trend_down: confidence += 10
                if vol_spike: confidence += 10
                sl_price = current_close + (atr_val * P.SL_ATR_MULT_BREAK)
                sl_dist = sl_price - current_close
                tp_price = current_close - (sl_dist * P.RR_BREAKOUT)
            else:
                return self.create_hold(symbol=symbol, reason="No breakout")

        if action == Action.HOLD:
            return self.create_hold(symbol=symbol, reason="No setup")
        if confidence < 55:
            return self.create_hold(symbol=symbol, reason=f"Low conf {confidence:.0f}")

        # SL distance
        if sl_price:
            sl_dist = abs(current_close - sl_price)
            if sl_dist < P.MIN_SL_DISTANCE:
                sl_price = (current_close - P.MIN_SL_DISTANCE) if action == Action.BUY else (current_close + P.MIN_SL_DISTANCE)

        rr = 0.0
        if sl_price and tp_price:
            sl_d = abs(current_close - sl_price)
            tp_d = abs(tp_price - current_close)
            rr = tp_d / sl_d if sl_d > 0 else 0

        confidence_norm = min(confidence / 100.0, 0.95)
        self._last_signal_bar = bar_idx

        return Decision(
            symbol=symbol, action=action, confidence=confidence_norm,
            reason=f"{mode}|{'|'.join(reasons[:3])}",
            stop_loss=sl_price, take_profit=tp_price,
            risk_reward_ratio=rr, strategy_name=self.name,
            timeframe=self.timeframe,
            tags=["silver_v2", mode.lower()],
            debug={"adx": round(adx_val, 1), "rsi": round(rsi_val, 1)},
        )


# ═════════════════════════════════════════════
# RUN VARIANT
# ═════════════════════════════════════════════

def run_variant(variant, candles, initial_equity=10000.0):
    """Load strategy, patch params, run backtest."""
    import app.strategy.templates.silver_elite as mod
    importlib.reload(mod)
    patch_module(mod, variant)

    is_single = variant.get("_single_condition", False)
    if is_single:
        strategy = SingleCondSilverElite()
    else:
        strategy = mod.SilverEliteStrategy()

    print(f"\n{'='*60}")
    print(f"  VARIANT: {variant['name']}")
    print(f"  RSI: {variant['RSI_BUY']}/{variant['RSI_SELL']}  "
          f"ADX: {variant['ADX_RANGING']}/{variant['ADX_TRENDING']}  "
          f"BB: {variant['BB_LEN']},{variant['BB_STD']}  "
          f"RR: {variant['RR_MEAN_REV']}/{variant['RR_BREAKOUT']}")
    print(f"{'='*60}")

    start_time = time.monotonic()
    result = run_silver_backtest(strategy, candles.copy(), SYMBOL, initial_equity)
    elapsed = time.monotonic() - start_time

    wr = float(result.win_rate) if result.win_rate else 0
    pf = float(result.profit_factor) if result.profit_factor else 0

    print(f"\n{'─'*50}")
    print(f"  RESULT: {variant['name']}")
    print(f"{'─'*50}")
    print(f"  Total Trades:  {result.total_trades}")
    print(f"  Win Rate:      {wr}%")
    print(f"  Profit Factor: {pf}")
    print(f"  P&L:           ${result.total_profit_usd:.2f}")
    print(f"  Max DD:        {result.max_drawdown_pct}%")
    print(f"  Time:          {elapsed:.1f}s")

    if wr >= 60 and pf >= 1.5:
        print(f"  ✅ PASS: WR={wr}% ≥ 60% AND PF={pf} ≥ 1.5")
    elif wr >= 60:
        print(f"  ⚠️ PARTIAL: WR={wr}% ≥ 60% but PF={pf} < 1.5")
    elif pf >= 1.5:
        print(f"  ⚠️ PARTIAL: PF={pf} ≥ 1.5 but WR={wr}% < 60%")
    else:
        print(f"  ❌ FAIL: WR={wr}% < 60% AND PF={pf} < 1.5")

    if hasattr(result, 'per_regime') and result.per_regime:
        print(f"\n  Per Regime:")
        for r, stats in result.per_regime.items():
            print(f"    {r:20s}: {stats.get('win_rate', 0)}% WR | "
                  f"${stats.get('pnl', 0)} | {stats.get('trades', 0)} trades")

    print(f"{'─'*50}")
    return result


# ═════════════════════════════════════════════
# SAVE
# ═════════════════════════════════════════════

def save_results_to_db(variant_name, result, days):
    db_path = Path(__file__).resolve().parent.parent / "data" / "sqlite" / "brain.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS silver_tournament_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            variant TEXT NOT NULL,
            days INTEGER,
            total_trades INTEGER,
            win_rate REAL,
            profit_factor REAL,
            total_profit_usd REAL,
            max_drawdown_pct REAL,
            passed_wr60 INTEGER,
            passed_pf15 INTEGER
        )
    """)
    wr = float(result.win_rate) if result.win_rate else 0
    pf = float(result.profit_factor) if result.profit_factor else 0
    c.execute("""
        INSERT INTO silver_tournament_results
        (timestamp, variant, days, total_trades, win_rate,
         profit_factor, total_profit_usd, max_drawdown_pct, passed_wr60, passed_pf15)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(), variant_name, days,
        result.total_trades, wr, pf, result.total_profit_usd,
        result.max_drawdown_pct, 1 if wr >= 60 else 0, 1 if pf >= 1.5 else 0,
    ))
    conn.commit()
    conn.close()


def save_best_params_to_db(variant, result, days):
    """บันทึก parameters ของ variant ที่ดีที่สุดลง brain.db"""
    import json
    db_path = Path(__file__).resolve().parent.parent / "data" / "sqlite" / "brain.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS silver_best_params (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            variant_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            days INTEGER,
            total_trades INTEGER,
            win_rate REAL,
            profit_factor REAL,
            total_profit_usd REAL,
            max_drawdown_pct REAL,
            params_json TEXT NOT NULL,
            is_active INTEGER DEFAULT 1
        )
    """)
    # deactivate ตัวเก่า
    c.execute("UPDATE silver_best_params SET is_active = 0 WHERE symbol = ?", (SYMBOL,))

    # เก็บ params (ลบ internal keys)
    params = {k: v for k, v in variant.items() if not k.startswith("_")}
    wr = float(result.win_rate) if result.win_rate else 0
    pf = float(result.profit_factor) if result.profit_factor else 0

    c.execute("""
        INSERT INTO silver_best_params
        (timestamp, variant_name, symbol, days, total_trades,
         win_rate, profit_factor, total_profit_usd, max_drawdown_pct,
         params_json, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
    """, (
        datetime.now(timezone.utc).isoformat(),
        variant["name"], SYMBOL, days,
        result.total_trades, wr, pf,
        result.total_profit_usd, result.max_drawdown_pct,
        json.dumps(params, indent=2),
    ))
    conn.commit()
    conn.close()
    print(f"  💾 BEST PARAMS บันทึกลง brain.db (silver_best_params) เรียบร้อย")
    print(f"     params_json = {json.dumps(params, indent=2)}")


# ═════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Silver Elite Tournament")
    parser.add_argument("--days", type=int, default=200)
    parser.add_argument("--equity", type=float, default=10000.0)
    args = parser.parse_args()

    if not mt5.initialize():
        print("❌ MT5 Init failed")
        return

    print(f"\n🔄 Loading {SYMBOL} M15 data ({args.days} days)...")
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=args.days)
    rates = mt5.copy_rates_range(SYMBOL, TIMEFRAME, utc_from, utc_to)

    if rates is None or len(rates) == 0:
        print(f"❌ No data for {SYMBOL}")
        mt5.shutdown()
        return

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"✅ Loaded {len(df)} bars\n")

    results = {}
    for variant in ALL_VARIANTS:
        try:
            result = run_variant(variant, df, args.equity)
            if result:
                results[variant["name"]] = {"result": result, "variant": variant}
                save_results_to_db(variant["name"], result, args.days)
                print(f"💾 {variant['name']} saved to DB")
        except Exception as e:
            print(f"❌ Error: {variant['name']}: {e}")
            import traceback
            traceback.print_exc()

    # ═══ TOURNAMENT SUMMARY ═══
    if results:
        print(f"\n{'='*80}")
        print(f"  🏆 SILVER TOURNAMENT SUMMARY — {SYMBOL} {args.days}d")
        print(f"{'='*80}")
        print(f"  {'Variant':20s} {'Trades':>7s} {'WR%':>6s} {'PF':>6s} "
              f"{'P&L':>10s} {'DD%':>6s} {'Status':>8s}")
        print(f"  {'─'*20} {'─'*7} {'─'*6} {'─'*6} {'─'*10} {'─'*6} {'─'*8}")

        best = None
        best_score = -999

        for name, data in results.items():
            res = data["result"]
            wr = float(res.win_rate) if res.win_rate else 0
            pf = float(res.profit_factor) if res.profit_factor else 0

            status = "✅ PASS" if wr >= 60 and pf >= 1.5 else (
                "⚠️ PART" if wr >= 60 or pf >= 1.5 else "❌ FAIL")

            # Composite score: profitability first, then WR, then PF
            score = (res.total_profit_usd / 10) + wr + min(pf, 10) * 15

            print(f"  {name:20s} {res.total_trades:7d} {wr:6.1f} {pf:6.2f} "
                  f"${res.total_profit_usd:9.2f} {res.max_drawdown_pct:6.1f} {status:>8s}")

            if score > best_score and res.total_trades >= 3:
                best_score = score
                best = name

        print(f"{'='*80}")
        if best:
            v = results[best]["variant"]
            r = results[best]["result"]
            print(f"\n  🏆 WINNER: {best}")
            print(f"     Trades={r.total_trades} WR={r.win_rate}% PF={r.profit_factor} P&L=${r.total_profit_usd:.2f}")
            print(f"     Params: RSI={v['RSI_BUY']}/{v['RSI_SELL']} "
                  f"ADX={v['ADX_RANGING']}/{v['ADX_TRENDING']} "
                  f"BB={v['BB_LEN']},{v['BB_STD']} "
                  f"RR={v['RR_MEAN_REV']}/{v['RR_BREAKOUT']}")

            # ═══ บันทึก BEST PARAMS ลง DB ═══
            save_best_params_to_db(v, r, args.days)
        else:
            print(f"\n  ⚠️ ไม่มี variant ที่มี ≥3 trades")
        print()

    mt5.shutdown()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
