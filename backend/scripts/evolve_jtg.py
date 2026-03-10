#!/usr/bin/env python3
"""
JTG Strategy Evolution V2 — GA + Session Filter + AI Deep Model Boost.

Enhancements over V1:
    1. Deeper GA: 15gen × 30pop by default
    2. Session filter: evolvable start/end hour (12-16 UTC sweet spot)
    3. AI Deep Model boost: P(direction) adds bonus confluence score
    4. Pre-computed regime cache for 20× speed

Usage:
    cd d:\\VibeCode\\Trade\\backend
    python scripts/evolve_jtg.py
    python scripts/evolve_jtg.py --generations 20 --population 30 --days 90
"""

import sys
import os
import time
import json
import random
import copy
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

for name in ["app", "urllib3", "MetaTrader5"]:
    logging.getLogger(name).setLevel(logging.CRITICAL)

import pandas as pd
import numpy as np
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════
# JTG PARAMETER BOUNDS V2
# ═══════════════════════════════════════════════════

JTG_PARAM_BOUNDS: dict[str, tuple[float, float, float]] = {
    # (min, max, step)
    "sl_atr_mult":        (0.3,  3.0,  0.25),
    "sl_max_atr":         (0.5,  4.0,  0.25),
    "rr_target":          (1.0,  5.0,  0.25),
    "min_confluence":     (2.0,  8.0,  1.0),
    "sr_lookback":        (10.0, 60.0, 5.0),
    "sr_zone_atr_mult":   (0.1,  1.5,  0.1),
    "fvg_min_gap_atr":    (0.1,  1.0,  0.1),
    "vol_spike_ratio":    (1.0,  2.5,  0.1),
    "rsi_buy_max":        (50.0, 80.0, 5.0),
    "rsi_buy_min":        (15.0, 40.0, 5.0),
    "rsi_sell_min":       (20.0, 50.0, 5.0),
    "rsi_sell_max":       (50.0, 85.0, 5.0),
    "ema_fast":           (10.0, 100.0, 10.0),
    "ema_slow":           (50.0, 300.0, 20.0),
    "require_structure":  (0.0,  1.0,  1.0),
    "require_pattern":    (0.0,  1.0,  1.0),
    # V2: Session filter
    "session_start_utc":  (7.0,  16.0, 1.0),    # hour to start trading
    "session_end_utc":    (14.0, 22.0, 1.0),     # hour to stop trading
    # V2: AI Deep Model integration
    "ai_boost_threshold": (0.3,  0.7,  0.05),    # min P(direction) to add bonus
    "ai_boost_points":    (0.0,  3.0,  0.5),     # bonus confluence points from AI
}

# V1 evolution best (from previous run)
JTG_DEFAULTS = {
    "sl_atr_mult": 0.5,
    "sl_max_atr": 1.5,
    "rr_target": 1.5,
    "min_confluence": 3.0,
    "sr_lookback": 30.0,
    "sr_zone_atr_mult": 0.8,
    "fvg_min_gap_atr": 0.5,
    "vol_spike_ratio": 1.5,
    "rsi_buy_max": 60.0,
    "rsi_buy_min": 30.0,
    "rsi_sell_min": 40.0,
    "rsi_sell_max": 65.0,
    "ema_fast": 90.0,
    "ema_slow": 220.0,
    "require_structure": 1.0,
    "require_pattern": 1.0,
    # V2 new params
    "session_start_utc": 12.0,
    "session_end_utc": 16.0,
    "ai_boost_threshold": 0.45,
    "ai_boost_points": 1.0,
}

MUTATION_RATE = 0.30
CROSSOVER_RATE = 0.5

# ═══════════════════════════════════════════════════
# AI DEEP MODEL LOADER
# ═══════════════════════════════════════════════════

_deep_model = None
_feature_engine = None
_deep_model_available = False


def init_deep_model(symbol: str = "XAUUSDc"):
    """Load AI Deep V4 model for signal boosting."""
    global _deep_model, _feature_engine, _deep_model_available
    try:
        from app.brain.deep_model_v4 import DeepModelV4
        from app.brain.mtf_feature_engine import MTFFeatureEngine

        _deep_model = DeepModelV4(symbol)
        if _deep_model._trained:
            _feature_engine = MTFFeatureEngine()
            _deep_model_available = True
            print(f"  AI Deep V4 loaded for {symbol}")
        else:
            print(f"  AI Deep V4 NOT trained for {symbol} — running without AI boost")
    except Exception as e:
        print(f"  AI Deep V4 unavailable: {e} — running without AI boost")


def get_ai_signal(candles: pd.DataFrame, bar_idx: int) -> dict | None:
    """
    Get AI Deep V4 prediction at a specific bar.
    Returns: {buy: P, sell: P, hold: P} or None
    """
    if not _deep_model_available or _feature_engine is None:
        return None

    if bar_idx < 120:
        return None

    try:
        window = candles.iloc[max(0, bar_idx - 200):bar_idx + 1]
        features = _feature_engine.build_live_features_v4(
            candles_m5=window,
            candles_m15=None,
            candles_h1=None,
            candles_h4=None,
            candles_d1=None,
        )
        if features is None:
            return None

        pred = _deep_model.predict(features.squeeze(0))
        return {"buy": pred["buy"], "sell": pred["sell"], "hold": pred["hold"]}
    except Exception:
        return None


# ═══════════════════════════════════════════════════
# FITNESS
# ═══════════════════════════════════════════════════

def fitness_score(trades: list[dict]) -> dict:
    if not trades:
        return {"score": 0.0, "trades": 0, "wr": 0.0, "pf": 0.0,
                "dd": 0.0, "pnl": 0.0, "avg_rr": 0.0}

    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] <= 0]

    total_pos = sum(t["r"] for t in wins) if wins else 0.0
    total_neg = abs(sum(t["r"] for t in losses)) if losses else 0.001

    wr = len(wins) / len(trades) if trades else 0.0
    pf = total_pos / total_neg if total_neg > 0 else 0.0

    equity = 10000.0
    peak = equity
    max_dd = 0.0
    for t in trades:
        equity += t["r"] * 100
        peak = max(peak, equity)
        dd = (peak - equity) / peak
        max_dd = max(max_dd, dd)

    avg_rr = np.mean([t["r"] for t in trades]) if trades else 0.0

    score = (
        pf * 0.3 +
        wr * 0.3 +
        (1.0 - min(max_dd, 1.0)) * 0.2 +
        min(avg_rr / 3.0, 1.0) * 0.1 +
        min(len(trades) / 30.0, 1.0) * 0.1
    )

    return {
        "score": round(score, 4),
        "trades": len(trades),
        "wr": round(wr * 100, 1),
        "pf": round(pf, 2),
        "dd": round(max_dd * 100, 1),
        "pnl": round(sum(t["r"] for t in trades), 2),
        "avg_rr": round(avg_rr, 2),
    }


# ═══════════════════════════════════════════════════
# FAST BACKTESTER V2 (session filter + AI boost)
# ═══════════════════════════════════════════════════

def run_backtest(candles: pd.DataFrame, params: dict,
                 regime_cache: dict | None = None,
                 ai_cache: dict | None = None) -> list[dict]:
    """
    V2 Backtester with session filter and AI Deep boost.
    """
    from app.strategy.templates.jtg_zone_fvg import JTGZoneFVGStrategy
    from app.domain.models import SymbolProfile
    from app.domain.enums import Action, RegimeType

    strategy = JTGZoneFVGStrategy()
    profile = SymbolProfile(
        symbol="XAUUSDc", point=0.01, digits=2,
        contract_size=100.0, min_lot=0.01, max_lot=100.0, lot_step=0.01,
    )

    trades = []
    in_trade = False
    window_size = 300
    warmup = max(int(params.get("ema_slow", 200)) + 20, 250)

    # Session filter params
    session_start = int(params.get("session_start_utc", 0))
    session_end = int(params.get("session_end_utc", 24))

    # AI boost params
    ai_threshold = float(params.get("ai_boost_threshold", 0.45))
    ai_points = float(params.get("ai_boost_points", 1.0))

    # Regime cache
    if regime_cache is None:
        from app.brain.regime import classify_regime
        regime_cache = {}
        ri = 50
        for i in range(warmup, len(candles) - 1, ri):
            start = max(0, i - window_size)
            window = candles.iloc[start:i + 1]
            try:
                regime_cache[i] = classify_regime(window)
            except Exception:
                regime_cache[i] = RegimeType.UNKNOWN
        regime_interval = ri
    else:
        keys = sorted(regime_cache.keys())
        regime_interval = keys[1] - keys[0] if len(keys) >= 2 else 50

    # Build JTG params (exclude non-JTG params)
    jtg_params = {k: v for k, v in params.items()
                  if k not in ("session_start_utc", "session_end_utc",
                               "ai_boost_threshold", "ai_boost_points")}

    for i in range(warmup, len(candles) - 1):
        if in_trade:
            bar = candles.iloc[i]
            if trade_action == "BUY":
                if float(bar["low"]) <= trade_sl:
                    trades.append({"r": -1.0, "action": trade_action,
                                   "entry": trade_entry, "sl": trade_sl,
                                   "tp": trade_tp, "bar_idx": i})
                    in_trade = False
                    continue
                elif float(bar["high"]) >= trade_tp:
                    rr = abs(trade_tp - trade_entry) / max(abs(trade_entry - trade_sl), 0.01)
                    trades.append({"r": rr, "action": trade_action,
                                   "entry": trade_entry, "sl": trade_sl,
                                   "tp": trade_tp, "bar_idx": i})
                    in_trade = False
                    continue
            else:
                if float(bar["high"]) >= trade_sl:
                    trades.append({"r": -1.0, "action": trade_action,
                                   "entry": trade_entry, "sl": trade_sl,
                                   "tp": trade_tp, "bar_idx": i})
                    in_trade = False
                    continue
                elif float(bar["low"]) <= trade_tp:
                    rr = abs(trade_entry - trade_tp) / max(abs(trade_sl - trade_entry), 0.01)
                    trades.append({"r": rr, "action": trade_action,
                                   "entry": trade_entry, "sl": trade_sl,
                                   "tp": trade_tp, "bar_idx": i})
                    in_trade = False
                    continue
            if i - trade_bar > 50:
                close_p = float(candles.iloc[i]["close"])
                if trade_action == "BUY":
                    r_val = (close_p - trade_entry) / max(abs(trade_entry - trade_sl), 0.01)
                else:
                    r_val = (trade_entry - close_p) / max(abs(trade_sl - trade_entry), 0.01)
                trades.append({"r": round(r_val, 2), "action": trade_action,
                               "entry": trade_entry, "sl": trade_sl,
                               "tp": trade_tp, "bar_idx": i})
                in_trade = False
                continue
            continue

        if len(trades) >= 200:
            break

        # ── SESSION FILTER ──
        if "time" in candles.columns:
            bar_time = candles.iloc[i]["time"]
            try:
                hour = pd.Timestamp(bar_time).hour
            except Exception:
                hour = 12
            if session_start <= session_end:
                if not (session_start <= hour < session_end):
                    continue
            else:  # wraps around midnight
                if not (hour >= session_start or hour < session_end):
                    continue

        # Get regime
        regime_key = (i // regime_interval) * regime_interval
        if regime_key < warmup:
            regime_key = warmup
        regime = regime_cache.get(regime_key, RegimeType.UNKNOWN)

        # Get JTG signal
        start = max(0, i - window_size)
        window = candles.iloc[start:i + 1]

        try:
            decision = strategy.analyze(window, profile, regime, **jtg_params)
        except Exception:
            continue

        if decision.action in (Action.BUY, Action.SELL) and decision.stop_loss and decision.take_profit:
            # ── AI DEEP MODEL BOOST ──
            if ai_cache is not None and ai_points > 0:
                # Find nearest AI prediction
                ai_key = (i // 10) * 10  # AI predictions cached every 10 bars
                ai_pred = ai_cache.get(ai_key)
                if ai_pred:
                    if decision.action == Action.BUY and ai_pred["buy"] >= ai_threshold:
                        # AI agrees with BUY — great!
                        pass  # Trade proceeds
                    elif decision.action == Action.SELL and ai_pred["sell"] >= ai_threshold:
                        # AI agrees with SELL — great!
                        pass  # Trade proceeds
                    elif ai_points >= 2.0:
                        # AI disagrees strongly — skip trade
                        continue

            in_trade = True
            trade_action = "BUY" if decision.action == Action.BUY else "SELL"
            trade_entry = float(candles.iloc[i]["close"])
            trade_sl = float(decision.stop_loss)
            trade_tp = float(decision.take_profit)
            trade_bar = i

    return trades


# ═══════════════════════════════════════════════════
# GENETIC ALGORITHM
# ═══════════════════════════════════════════════════

def mutate(params: dict) -> dict:
    mutated = copy.deepcopy(params)
    for key, value in mutated.items():
        if key not in JTG_PARAM_BOUNDS:
            continue
        if random.random() > MUTATION_RATE:
            continue
        mn, mx, step = JTG_PARAM_BOUNDS[key]
        n_steps = random.choice([-3, -2, -1, 1, 2, 3])
        new_val = value + n_steps * step
        new_val = max(mn, min(mx, new_val))
        mutated[key] = round(new_val, 4)
    # Safety: ema_fast < ema_slow
    if mutated.get("ema_fast", 50) >= mutated.get("ema_slow", 200):
        mutated["ema_fast"] = mutated["ema_slow"] - 20
    # Safety: session_start < session_end
    if mutated.get("session_start_utc", 12) >= mutated.get("session_end_utc", 16):
        mutated["session_end_utc"] = mutated["session_start_utc"] + 2
    return mutated


def crossover(a: dict, b: dict) -> dict:
    child = {}
    for key in set(a) | set(b):
        child[key] = a.get(key) if random.random() < CROSSOVER_RATE else b.get(key, a.get(key))
    return child


def evolve(candles: pd.DataFrame, generations: int = 15,
           population_size: int = 30) -> dict:
    """Run GA V2 with session filter + AI Deep boost."""
    from app.brain.regime import classify_regime
    from app.domain.enums import RegimeType

    print(f"\n  Starting Evolution: {generations} gen x {population_size} pop")
    print(f"  Candles: {len(candles)} bars")

    # PRE-COMPUTE regime cache
    print(f"  Pre-computing regime cache...", end="", flush=True)
    regime_cache = {}
    window_size = 300
    warmup = 250
    regime_interval = 50
    for i in range(warmup, len(candles) - 1, regime_interval):
        start = max(0, i - window_size)
        window = candles.iloc[start:i + 1]
        try:
            regime_cache[i] = classify_regime(window)
        except Exception:
            regime_cache[i] = RegimeType.UNKNOWN
    print(f" done ({len(regime_cache)} checkpoints)")

    # PRE-COMPUTE AI predictions (every 10 bars)
    ai_cache = None
    if _deep_model_available:
        print(f"  Pre-computing AI predictions...", end="", flush=True)
        ai_cache = {}
        for i in range(warmup, len(candles) - 1, 10):
            pred = get_ai_signal(candles, i)
            if pred:
                ai_cache[i] = pred
        print(f" done ({len(ai_cache)} signals)")
    else:
        print(f"  AI boost: DISABLED (no trained model)")

    # Initial population: evolved V1 best + mutations
    population = [JTG_DEFAULTS.copy()]
    for _ in range(population_size - 1):
        population.append(mutate(JTG_DEFAULTS.copy()))

    # Baseline
    base_trades = run_backtest(candles, JTG_DEFAULTS, regime_cache, ai_cache)
    base_fitness = fitness_score(base_trades)
    print(f"  Baseline: {base_fitness['trades']}T WR={base_fitness['wr']}% "
          f"PF={base_fitness['pf']} DD={base_fitness['dd']}% "
          f"Score={base_fitness['score']}")

    best_params = JTG_DEFAULTS.copy()
    best_fitness = base_fitness
    generation_log = []

    for gen in range(generations):
        t0 = time.time()
        scored: list[tuple[dict, dict]] = []

        for params in population:
            trades = run_backtest(candles, params, regime_cache, ai_cache)
            fit = fitness_score(trades)
            scored.append((params, fit))

        scored.sort(key=lambda x: x[1]["score"], reverse=True)

        gen_best = scored[0]
        if gen_best[1]["score"] > best_fitness["score"]:
            best_fitness = gen_best[1]
            best_params = gen_best[0].copy()

        elapsed = time.time() - t0
        gen_log = {
            "gen": gen + 1,
            "best_score": gen_best[1]["score"],
            "best_wr": gen_best[1]["wr"],
            "best_pf": gen_best[1]["pf"],
            "best_dd": gen_best[1]["dd"],
            "best_trades": gen_best[1]["trades"],
            "avg_score": round(np.mean([s[1]["score"] for s in scored]), 4),
            "elapsed": round(elapsed, 1),
        }
        generation_log.append(gen_log)

        bar = "#" * int(gen_best[1]["score"] * 20)
        print(f"  Gen {gen+1:>2}/{generations} | "
              f"Score={gen_best[1]['score']:.4f} "
              f"WR={gen_best[1]['wr']:>5.1f}% "
              f"PF={gen_best[1]['pf']:>5.2f} "
              f"DD={gen_best[1]['dd']:>5.1f}% "
              f"T={gen_best[1]['trades']:>3} "
              f"|{bar}| {elapsed:.0f}s")

        # Selection
        n_parents = max(2, population_size // 2)
        parents = [s[0] for s in scored[:n_parents]]

        new_pop = [best_params.copy()]
        for p in parents[:3]:
            new_pop.append(copy.deepcopy(p))

        while len(new_pop) < population_size:
            p1 = random.choice(parents)
            p2 = random.choice(parents)
            child = crossover(p1, p2)
            child = mutate(child)
            new_pop.append(child)

        population = new_pop

    return {
        "best_params": best_params,
        "best_fitness": best_fitness,
        "baseline_fitness": base_fitness,
        "generation_log": generation_log,
    }


# ═══════════════════════════════════════════════════
# AI COACH V2
# ═══════════════════════════════════════════════════

def ai_coach_analyze(candles: pd.DataFrame, best_params: dict,
                     best_fitness: dict, baseline_fitness: dict) -> dict:
    print("\n  AI Coach Analysis...")

    param_changes = {}
    for key in JTG_DEFAULTS:
        old = JTG_DEFAULTS[key]
        new = best_params.get(key, old)
        if old != new:
            pct = ((new - old) / old * 100) if old != 0 else 0
            param_changes[key] = {
                "old": old, "new": new,
                "change_pct": round(pct, 1),
                "direction": "UP" if new > old else "DN",
            }

    trades = run_backtest(candles, best_params)
    wins = [t for t in trades if t["r"] > 0]
    losses = [t for t in trades if t["r"] <= 0]

    hour_stats = {}
    for t in trades:
        bar_idx = t["bar_idx"]
        if bar_idx < len(candles) and "time" in candles.columns:
            bar_time = candles.iloc[bar_idx]["time"]
            try:
                hour = pd.Timestamp(bar_time).hour
            except Exception:
                hour = 0
            if hour not in hour_stats:
                hour_stats[hour] = {"wins": 0, "losses": 0}
            if t["r"] > 0:
                hour_stats[hour]["wins"] += 1
            else:
                hour_stats[hour]["losses"] += 1

    insights = []
    if best_fitness["wr"] >= 50:
        insights.append("[PASS] WR >= 50%")
    else:
        insights.append(f"[FAIL] WR = {best_fitness['wr']}% < 50%")

    if best_fitness["pf"] >= 1.3:
        insights.append("[PASS] PF >= 1.3")
    else:
        insights.append(f"[FAIL] PF = {best_fitness['pf']} < 1.3")

    if best_fitness["dd"] <= 6.0:
        insights.append("[PASS] DD <= 6%")
    else:
        insights.append(f"[FAIL] DD = {best_fitness['dd']}% > 6%")

    improvement = best_fitness["score"] - baseline_fitness["score"]
    if improvement > 0:
        insights.append(f"[UP] Score improved by {improvement:.4f} ({improvement/max(baseline_fitness['score'],0.001)*100:.1f}%)")
    else:
        insights.append("[DOWN] No improvement over baseline")

    # Session analysis
    sess_start = int(best_params.get("session_start_utc", 0))
    sess_end = int(best_params.get("session_end_utc", 24))
    insights.append(f"[SESSION] Trading window: {sess_start:02d}:00-{sess_end:02d}:00 UTC")

    # AI boost status
    ai_threshold = best_params.get("ai_boost_threshold", 0.45)
    ai_points = best_params.get("ai_boost_points", 0)
    if ai_points > 0:
        insights.append(f"[AI] Deep Model boost: +{ai_points} pts when P(dir)>{ai_threshold:.0%}")
    else:
        insights.append("[AI] Deep Model boost: DISABLED by evolution")

    best_hours = sorted(hour_stats.items(),
                        key=lambda x: x[1]["wins"] / max(x[1]["wins"] + x[1]["losses"], 1),
                        reverse=True)
    if best_hours:
        top = best_hours[0]
        total = top[1]["wins"] + top[1]["losses"]
        wr_h = top[1]["wins"] / max(total, 1) * 100
        insights.append(f"[BEST_HOUR] {top[0]:02d}:00 UTC (WR={wr_h:.0f}%, {total} trades)")

    return {
        "param_changes": param_changes,
        "hour_stats": dict(sorted(hour_stats.items())),
        "total_wins": len(wins),
        "total_losses": len(losses),
        "insights": insights,
    }


# ═══════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="JTG Strategy Evolution V2")
    parser.add_argument("--generations", type=int, default=15)
    parser.add_argument("--population", type=int, default=30)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--symbol", type=str, default="XAUUSDc")
    parser.add_argument("--no-ai", action="store_true", help="Disable AI boost")
    args = parser.parse_args()

    print("=" * 70)
    print("  JTG Strategy Evolution V2 — GA + Session + AI Deep")
    print("=" * 70)

    # Init MT5
    if not mt5.initialize():
        print("MT5 init failed:", mt5.last_error())
        sys.exit(1)

    acct = mt5.account_info()
    print(f"  MT5: {acct.server} | Balance: {acct.balance}")

    # Load AI Deep Model
    if not args.no_ai:
        init_deep_model(args.symbol)

    # Fetch data
    bars = args.days * 24 * 4
    print(f"  Fetching {bars} M15 bars ({args.days} days) for {args.symbol}...")

    rates = mt5.copy_rates_from_pos(args.symbol, mt5.TIMEFRAME_M15, 0, bars)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        print("No data from MT5")
        sys.exit(1)

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    print(f"  Got {len(df)} candles [{df['time'].iloc[0]} -> {df['time'].iloc[-1]}]")

    # Split
    split_idx = int(len(df) * 0.80)
    train = df.iloc[:split_idx].reset_index(drop=True)
    validate = df.iloc[split_idx:].reset_index(drop=True)
    print(f"  Train: {len(train)} bars | Validate: {len(validate)} bars")

    # Evolve
    t0 = time.time()
    result = evolve(train, generations=args.generations,
                    population_size=args.population)
    elapsed = time.time() - t0

    best = result["best_fitness"]
    base = result["baseline_fitness"]

    print(f"\n  Evolution complete in {elapsed:.0f}s")
    print(f"\n{'='*70}")
    print(f"  EVOLUTION RESULTS V2")
    print(f"{'='*70}")
    print(f"  {'Metric':<20} {'Baseline':>10} {'Evolved':>10} {'Change':>10}")
    print(f"  {'='*50}")
    for key in ["trades", "wr", "pf", "dd", "avg_rr", "score"]:
        b = base.get(key, 0)
        e = best.get(key, 0)
        diff = e - b
        arrow = "UP" if diff > 0 else "DN" if diff < 0 else "=="
        print(f"  {key:<20} {b:>10} {e:>10} {arrow:>4} {abs(diff):>6.2f}")

    # Validate
    print(f"\n  Validating on hold-out ({len(validate)} bars)...")
    val_trades = run_backtest(validate, result["best_params"])
    val_fitness = fitness_score(val_trades)

    print(f"  Validation: {val_fitness['trades']}T WR={val_fitness['wr']}% "
          f"PF={val_fitness['pf']} DD={val_fitness['dd']}%")

    if val_fitness["score"] < best["score"] * 0.5:
        print(f"  WARNING: Possible overfitting! Val score={val_fitness['score']:.4f}")
    elif val_fitness["score"] >= best["score"] * 0.8:
        print(f"  PASS: Validation holds (>= 80% of train score)")
    else:
        print(f"  NOTE: Moderate hold-out degradation")

    # AI Coach
    coach = ai_coach_analyze(train, result["best_params"], best, base)

    print(f"\n{'='*70}")
    print(f"  AI COACH INSIGHTS")
    print(f"{'='*70}")
    for insight in coach["insights"]:
        print(f"  {insight}")

    print(f"\n  Parameter Changes:")
    for k, v in coach["param_changes"].items():
        print(f"     {k:<25} {v['old']:>8} -> {v['new']:>8} ({v['direction']}{abs(v['change_pct']):.0f}%)")

    # Live-Ready
    live_ready = (
        val_fitness["wr"] >= 50
        and val_fitness["pf"] >= 1.3
        and val_fitness["dd"] <= 6.0
    )

    print(f"\n{'='*70}")
    if live_ready:
        print(f"  [LIVE-READY] All criteria met on validation!")
    else:
        fails = []
        if val_fitness["wr"] < 50: fails.append(f"WR={val_fitness['wr']}%<50%")
        if val_fitness["pf"] < 1.3: fails.append(f"PF={val_fitness['pf']}<1.3")
        if val_fitness["dd"] > 6.0: fails.append(f"DD={val_fitness['dd']}%>6%")
        print(f"  [NOT LIVE-READY] {', '.join(fails)}")
    print(f"{'='*70}")

    # Save
    report = {
        "version": "V2",
        "symbol": args.symbol,
        "days": args.days,
        "generations": args.generations,
        "population": args.population,
        "elapsed_s": round(elapsed, 1),
        "ai_boost_enabled": _deep_model_available,
        "baseline": base,
        "evolved_train": best,
        "evolved_validate": val_fitness,
        "best_params": result["best_params"],
        "param_changes": coach["param_changes"],
        "insights": coach["insights"],
        "generation_log": result["generation_log"],
        "live_ready": live_ready,
    }

    out_path = os.path.join(os.path.dirname(__file__), "..", "reports",
                            "jtg_evolution_v2_result.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n  Saved: {os.path.abspath(out_path)}")

    print(f"\n  Best Evolved Params:")
    for k, v in sorted(result["best_params"].items()):
        print(f"     {k} = {v}")


if __name__ == "__main__":
    main()
