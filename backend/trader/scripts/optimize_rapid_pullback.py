#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Optimize RAPID_PULLBACK parameters per symbol/timeframe.

The optimizer reuses the live feature pipeline and strategy signal function,
then evaluates candidate parameter sets with a lightweight backtest loop.
Primary goal: find parameter sets that pass the same strict production gates
used for TF overrides.

Usage:
  python -m backend.trader.scripts.optimize_rapid_pullback --symbols XAUUSD --timeframe M5 --days 30
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd

from backend.trader.features.candle_patterns import detect_candle_patterns
from backend.trader.features.divergence import detect_rsi_divergence
from backend.trader.features.institutional import add_institutional_features
from backend.trader.features.structure import add_structure_features, detect_displacement
from backend.trader.features.volatility import add_volatility_features
from backend.trader.regime.classifier import classify_regime
from backend.trader.scripts.run_backtest import BacktestEngine, load_mt5_data, risk_engine
from backend.trader.strategy.rapid_pullback import _params_for_context, signal_rapid_pullback


SYMBOLS_DEFAULT = ["XAUUSD", "BTCUSD", "USOIL"]


@dataclass(frozen=True)
class ParamSet:
    lookback: int
    touch_atr: float
    sl_buffer_atr: float
    tp1_rr: float
    min_body_ratio: float
    min_vol_ratio: float
    min_roc: float
    min_di_gap: float


def _safe_float(value, fallback: float = 0.0) -> float:
    try:
        out = float(value)
        if pd.isna(out):
            return fallback
        return out
    except Exception:
        return fallback


def _build_grid(symbol: str, timeframe: str, base: dict) -> List[ParamSet]:
    sym = str(symbol or "").upper()
    tf = str(timeframe or "").upper()

    if "XAU" in sym and tf == "M5":
        grid = {
            "lookback": [4, 5, 6],
            "touch_atr": [0.20, 0.24, 0.28, 0.32],
            "sl_buffer_atr": [0.16, 0.20, 0.24, 0.28],
            "tp1_rr": [1.05, 1.10, 1.15, 1.20, 1.25, 1.30],
            "min_body_ratio": [0.28, 0.32, 0.36, 0.40],
            "min_vol_ratio": [0.92, 0.96, 1.00, 1.04],
            "min_roc": [0.01, 0.02, 0.03, 0.04],
            "min_di_gap": [1.0, 1.4, 1.8, 2.2],
        }
    else:
        grid = {
            "lookback": [max(3, int(base.get("lookback", 6)) - 1), int(base.get("lookback", 6)), int(base.get("lookback", 6)) + 1],
            "touch_atr": [round(float(base.get("touch_atr", 0.35)) + d, 2) for d in (-0.06, -0.02, 0.02, 0.06)],
            "sl_buffer_atr": [round(float(base.get("sl_buffer_atr", 0.25)) + d, 2) for d in (-0.06, -0.02, 0.02, 0.06)],
            "tp1_rr": [round(float(base.get("tp1_rr", 1.6)) + d, 2) for d in (-0.25, -0.10, 0.0, 0.10)],
            "min_body_ratio": [round(max(0.2, float(base.get("min_body_ratio", 0.45)) + d), 2) for d in (-0.10, -0.04, 0.0, 0.04)],
            "min_vol_ratio": [round(max(0.8, float(base.get("min_vol_ratio", 1.0)) + d), 2) for d in (-0.08, -0.04, 0.0, 0.04)],
            "min_roc": [round(max(0.0, float(base.get("min_roc", 0.04)) + d), 3) for d in (-0.02, -0.01, 0.0, 0.01)],
            "min_di_gap": [round(max(0.0, float(base.get("min_di_gap", 2.0)) + d), 2) for d in (-0.8, -0.4, 0.0, 0.4)],
        }

    combos = [
        ParamSet(
            int(lookback),
            float(touch_atr),
            float(sl_buffer_atr),
            float(tp1_rr),
            float(min_body_ratio),
            float(min_vol_ratio),
            float(min_roc),
            float(min_di_gap),
        )
        for lookback, touch_atr, sl_buffer_atr, tp1_rr, min_body_ratio, min_vol_ratio, min_roc, min_di_gap in itertools.product(
            grid["lookback"],
            grid["touch_atr"],
            grid["sl_buffer_atr"],
            grid["tp1_rr"],
            grid["min_body_ratio"],
            grid["min_vol_ratio"],
            grid["min_roc"],
            grid["min_di_gap"],
        )
    ]

    baseline = ParamSet(
        int(base.get("lookback", 6)),
        round(float(base.get("touch_atr", 0.35)), 3),
        round(float(base.get("sl_buffer_atr", 0.25)), 3),
        round(float(base.get("tp1_rr", 1.6)), 3),
        round(float(base.get("min_body_ratio", 0.45)), 3),
        round(float(base.get("min_vol_ratio", 1.0)), 3),
        round(float(base.get("min_roc", 0.04)), 3),
        round(float(base.get("min_di_gap", 2.0)), 3),
    )
    if baseline not in combos:
        combos.append(baseline)
    return combos


def _point_size_for_symbol(symbol: str) -> float:
    sym = str(symbol or "").upper()
    if "XAU" in sym or "XAG" in sym:
        return 0.01
    if "BTC" in sym or "30" in sym or "TEC" in sym or "NAS" in sym:
        return 1.0
    return 0.0001


def _strict_pass(metrics: dict, args) -> bool:
    return (
        int(metrics.get("trades", 0) or 0) > 0
        and float(metrics.get("win_rate", 0.0) or 0.0) >= float(args.prod_min_win_rate)
        and float(metrics.get("profit_factor", 0.0) or 0.0) >= float(args.prod_min_pf)
        and float(metrics.get("net_pnl", 0.0) or 0.0) > 0.0
        and float(metrics.get("max_dd", 999.0) or 999.0) <= float(args.prod_max_dd)
    )


def _simulate_signal(
    tester: BacktestEngine,
    signal: dict,
    future_bars: pd.DataFrame,
) -> dict:
    return tester.simulate_trade(signal, future_bars)


def _prepare_eval_bars(df: pd.DataFrame, *, lookback: int, hold_bars: int) -> List[dict]:
    regime_cfg = {
        "trend_threshold": 0.45,
        "volatility_compression_threshold": 0.5,
        "volatility_expansion_threshold": 1.5,
    }
    prepared: List[dict] = []
    for i in range(lookback, len(df) - hold_bars):
        window = df.iloc[i - lookback:i]
        if len(window) < lookback:
            continue
        prepared.append(
            {
                "bar_index": i,
                "regime_result": classify_regime(window, regime_cfg),
            }
        )
    return prepared


def _evaluate_params(
    df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    lookback: int,
    hold_bars: int,
    params: ParamSet,
    prepared_bars: List[dict],
) -> dict:
    tester = BacktestEngine(
        symbol=symbol,
        initial_equity=1000.0,
        timeframe=timeframe,
        strategy_mode="rapid_pullback",
    )
    tester.equity = 1000.0
    tester.peak_equity = 1000.0
    tester.daily_pnl = 0.0
    tester.consecutive_losses = 0
    tester.max_dd = 0.0
    tester.trades = []
    point_size = _point_size_for_symbol(symbol)

    total_signals = 0
    blocked_signals = 0

    param_overrides = {
        "lookback": params.lookback,
        "touch_atr": params.touch_atr,
        "sl_buffer_atr": params.sl_buffer_atr,
        "tp1_rr": params.tp1_rr,
        "min_body_ratio": params.min_body_ratio,
        "min_vol_ratio": params.min_vol_ratio,
        "min_roc": params.min_roc,
        "min_di_gap": params.min_di_gap,
    }

    for item in prepared_bars:
        i = int(item["bar_index"])
        window = df.iloc[i - lookback:i]
        if len(window) < lookback:
            continue

        context = {
            "symbol": symbol,
            "timeframe": timeframe,
            "regime_result": item["regime_result"],
            "brain_params": {"rapid_pullback": param_overrides},
        }
        signal = signal_rapid_pullback(window, context)
        if not signal:
            continue

        total_signals += 1
        signal["symbol"] = symbol

        account_state = {
            "equity": tester.equity,
            "daily_pnl": tester.daily_pnl,
            "consecutive_losses": tester.consecutive_losses,
        }
        market_state = {
            "spread": tester.specs.get("spread_points", 30),
            "is_news": False,
            "backtest_mode": True,
            "vol_ratio": _safe_float(window.iloc[-1].get("vol_ratio", 1.0), 1.0),
            "atr_deviation": 1.0,
            "tick_value": tester.specs.get("point_value_per_lot", 1.0),
            "tick_size": point_size,
            "volume_min": 0.01,
            "volume_step": 0.01,
        }
        gate = risk_engine.risk_gate(signal, account_state, market_state)
        if not gate.get("allowed", False):
            blocked_signals += 1
            continue

        future = df.iloc[i:i + hold_bars]
        outcome = _simulate_signal(tester, signal, future)
        pnl_usd = float(outcome["pnl"]) * 0.01 * float(tester.specs.get("contract_size", 100))
        tester.equity += pnl_usd
        tester.daily_pnl += pnl_usd
        if tester.equity > tester.peak_equity:
            tester.peak_equity = tester.equity
        dd = ((tester.peak_equity - tester.equity) / tester.peak_equity * 100.0) if tester.peak_equity > 0 else 0.0
        tester.max_dd = max(tester.max_dd, dd)

        if outcome["result"] == "SL":
            tester.consecutive_losses += 1
        else:
            tester.consecutive_losses = 0

        tester.trades.append(
            {
                "pnl_usd": round(pnl_usd, 4),
                "result": outcome["result"],
            }
        )

    wins = [t for t in tester.trades if t["pnl_usd"] > 0]
    losses = [t for t in tester.trades if t["pnl_usd"] <= 0]
    gross_profit = sum(t["pnl_usd"] for t in wins) if wins else 0.0
    gross_loss = abs(sum(t["pnl_usd"] for t in losses)) if losses else 1e-9
    metrics = {
        "total_signals": int(total_signals),
        "blocked_signals": int(blocked_signals),
        "trades": len(tester.trades),
        "win_rate": round((len(wins) / len(tester.trades)) * 100.0, 2) if tester.trades else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 3) if tester.trades else 0.0,
        "net_pnl": round(sum(t["pnl_usd"] for t in tester.trades), 4),
        "max_dd": round(tester.max_dd, 3),
        "final_equity": round(tester.equity, 4),
    }
    return metrics


def _optimize_symbol(symbol: str, args) -> Dict:
    timeframe = str(args.timeframe or "M5").upper()
    print(f"\n[OPT] {symbol} {timeframe} {args.days}d ...")
    df = load_mt5_data(symbol, bars=0, timeframe=timeframe, days=int(args.days))
    if df is None or df.empty:
        raise RuntimeError(f"No data for {symbol}")

    df = add_volatility_features(df)
    df = add_structure_features(df)
    df = detect_displacement(df)
    df = add_institutional_features(df)
    df = detect_rsi_divergence(df)
    df = detect_candle_patterns(df)

    base = _params_for_context(
        symbol,
        timeframe,
        {"symbol": symbol, "timeframe": timeframe, "brain_params": {}},
    )
    prepared_bars = _prepare_eval_bars(df, lookback=int(args.lookback), hold_bars=int(args.hold_bars))
    grid = _build_grid(symbol, timeframe, base)
    rng = random.Random(int(args.seed) + abs(hash((symbol, timeframe))) % 100000)
    if int(args.max_trials) > 0 and len(grid) > int(args.max_trials):
        sampled = rng.sample(grid, k=int(args.max_trials))
        baseline = ParamSet(
            int(base.get("lookback", 6)),
            round(float(base.get("touch_atr", 0.35)), 3),
            round(float(base.get("sl_buffer_atr", 0.25)), 3),
            round(float(base.get("tp1_rr", 1.6)), 3),
            round(float(base.get("min_body_ratio", 0.45)), 3),
            round(float(base.get("min_vol_ratio", 1.0)), 3),
            round(float(base.get("min_roc", 0.04)), 3),
            round(float(base.get("min_di_gap", 2.0)), 3),
        )
        if baseline not in sampled:
            sampled.append(baseline)
        grid = sampled

    best = None
    baseline_row = None
    baseline_key = ParamSet(
        int(base.get("lookback", 6)),
        round(float(base.get("touch_atr", 0.35)), 3),
        round(float(base.get("sl_buffer_atr", 0.25)), 3),
        round(float(base.get("tp1_rr", 1.6)), 3),
        round(float(base.get("min_body_ratio", 0.45)), 3),
        round(float(base.get("min_vol_ratio", 1.0)), 3),
        round(float(base.get("min_roc", 0.04)), 3),
        round(float(base.get("min_di_gap", 2.0)), 3),
    )

    total_trials = len(grid)
    for idx, params in enumerate(grid, start=1):
        metrics = _evaluate_params(
            df,
            symbol=symbol,
            timeframe=timeframe,
            lookback=int(args.lookback),
            hold_bars=int(args.hold_bars),
            params=params,
            prepared_bars=prepared_bars,
        )
        row = {
            "symbol": symbol,
            "timeframe": timeframe,
            "days": int(args.days),
            "lookback": params.lookback,
            "touch_atr": params.touch_atr,
            "sl_buffer_atr": params.sl_buffer_atr,
            "tp1_rr": params.tp1_rr,
            "min_body_ratio": params.min_body_ratio,
            "min_vol_ratio": params.min_vol_ratio,
            "min_roc": params.min_roc,
            "min_di_gap": params.min_di_gap,
            **metrics,
        }
        row["strict_pass"] = _strict_pass(row, args)
        row["rank_key"] = (
            1 if row["strict_pass"] else 0,
            float(row["net_pnl"]),
            float(row["profit_factor"]),
            float(row["win_rate"]),
            int(row["trades"]),
            -float(row["max_dd"]),
        )

        if params == baseline_key:
            baseline_row = dict(row)
        if best is None or row["rank_key"] > best["rank_key"]:
            best = dict(row)
        if idx == 1 or idx == total_trials or idx % 12 == 0:
            print(
                f"  trial {idx}/{total_trials} | strict={row['strict_pass']} | trades={row['trades']} "
                f"| WR={row['win_rate']:.2f}% | PF={row['profit_factor']:.2f} | pnl={row['net_pnl']:.2f}"
            )

    if best is None:
        raise RuntimeError("No optimization rows produced")
    if baseline_row is None:
        baseline_row = dict(best)

    best["baseline"] = baseline_row
    best["improvement"] = {
        "net_pnl_delta": round(float(best["net_pnl"]) - float(baseline_row["net_pnl"]), 4),
        "pf_delta": round(float(best["profit_factor"]) - float(baseline_row["profit_factor"]), 4),
        "wr_delta": round(float(best["win_rate"]) - float(baseline_row["win_rate"]), 4),
        "trades_delta": int(best["trades"]) - int(baseline_row["trades"]),
    }
    print(
        f"  -> strict={best['strict_pass']} | trades={best['trades']} | WR={best['win_rate']:.2f}% "
        f"| PF={best['profit_factor']:.2f} | pnl={best['net_pnl']:.2f} | dd={best['max_dd']:.2f}%"
    )
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize RAPID_PULLBACK per symbol")
    parser.add_argument("--symbols", default=",".join(SYMBOLS_DEFAULT), help="Comma-separated symbols")
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--lookback", type=int, default=100)
    parser.add_argument("--hold-bars", type=int, default=50)
    parser.add_argument("--max-trials", type=int, default=96)
    parser.add_argument("--seed", type=int, default=20260311)
    parser.add_argument("--prod-min-win-rate", type=float, default=35.0)
    parser.add_argument("--prod-min-pf", type=float, default=1.05)
    parser.add_argument("--prod-max-dd", type=float, default=25.0)
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in str(args.symbols).split(",") if s.strip()]
    results = []
    for sym in symbols:
        try:
            results.append(_optimize_symbol(sym, args))
        except Exception as e:
            print(f"[ERR] {sym}: {e}")

    if not results:
        print("No results generated.")
        return 1

    out_dir = Path("d:/VibeCode/Trade/backend/trader/data")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"rapid_pullback_tuning_{str(args.timeframe).upper()}_{int(args.days)}d_{stamp}.json"
    csv_path = out_dir / f"rapid_pullback_tuning_{str(args.timeframe).upper()}_{int(args.days)}d_{stamp}.csv"

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    table = pd.DataFrame(
        [
            {
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "days": r["days"],
                "strict_pass": r["strict_pass"],
                "lookback": r["lookback"],
                "touch_atr": r["touch_atr"],
                "sl_buffer_atr": r["sl_buffer_atr"],
                "tp1_rr": r["tp1_rr"],
                "min_body_ratio": r["min_body_ratio"],
                "min_vol_ratio": r["min_vol_ratio"],
                "min_roc": r["min_roc"],
                "min_di_gap": r["min_di_gap"],
                "trades": r["trades"],
                "win_rate": r["win_rate"],
                "profit_factor": r["profit_factor"],
                "net_pnl": r["net_pnl"],
                "max_dd": r["max_dd"],
                "pf_delta": r["improvement"]["pf_delta"],
                "net_pnl_delta": r["improvement"]["net_pnl_delta"],
            }
            for r in results
        ]
    ).sort_values(by=["strict_pass", "net_pnl", "profit_factor"], ascending=[False, False, False])
    table.to_csv(csv_path, index=False)

    print("\n=== RAPID_PULLBACK Tuning Table ===")
    print(table.to_string(index=False))
    print(f"\nSaved JSON: {json_path}")
    print(f"Saved CSV : {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
