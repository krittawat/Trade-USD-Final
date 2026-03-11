#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Optimize INDICATOR_CONFLUENCE parameters per symbol.

Target params:
- min_score
- momentum threshold (delta around 100)
- sl_atr_mult
- tp_rr

The script:
1. Loads MT5 candles per symbol/timeframe.
2. Computes feature pipeline once.
3. Runs parameter search per symbol.
4. Exports recommendation table to CSV/JSON.

Usage:
  python -m backend.trader.scripts.optimize_indicator_confluence --days 120 --timeframe M15
"""
from __future__ import annotations

import argparse
import itertools
import json
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np
import pandas as pd

from backend.trader.features.candle_patterns import detect_candle_patterns
from backend.trader.features.divergence import detect_rsi_divergence
from backend.trader.features.institutional import add_institutional_features
from backend.trader.features.structure import add_structure_features, detect_displacement
from backend.trader.features.volatility import add_volatility_features
from backend.trader.scripts.run_backtest import load_mt5_data
from backend.trader.strategy.indicator_confluence import _resolve_params


SYMBOLS_DEFAULT = [
    "XAUUSD",
    "XAGUSD",
    "BTCUSD",
    "USOIL",
    "US30",
    "USTEC",
    "EURUSD",
    "GBPUSD",
    "USDJPY",
]


def _symbol_family(symbol: str) -> str:
    s = str(symbol).upper()
    if "BTC" in s:
        return "crypto"
    if "XAU" in s or "XAG" in s:
        return "metals"
    if "OIL" in s:
        return "oil"
    if "US30" in s or "USTEC" in s or "NAS" in s or "SPX" in s:
        return "indices"
    return "forex"


FAMILY_GRID = {
    "crypto": {
        "min_score": [6, 7, 8],
        "mom_delta": [0.15, 0.20, 0.25, 0.30],
        "sl_atr_mult": [1.7, 1.9, 2.1, 2.3],
        "tp_rr": [1.9, 2.1, 2.3, 2.5],
    },
    "metals": {
        "min_score": [5, 6, 7],
        "mom_delta": [0.06, 0.08, 0.10, 0.12],
        "sl_atr_mult": [1.1, 1.3, 1.5, 1.7],
        "tp_rr": [1.6, 1.8, 2.0, 2.2],
    },
    "oil": {
        "min_score": [5, 6, 7],
        "mom_delta": [0.08, 0.10, 0.12, 0.15],
        "sl_atr_mult": [1.2, 1.4, 1.6, 1.8],
        "tp_rr": [1.7, 1.9, 2.1, 2.3],
    },
    "indices": {
        "min_score": [5, 6, 7],
        "mom_delta": [0.08, 0.10, 0.12, 0.15],
        "sl_atr_mult": [1.3, 1.5, 1.7, 1.9],
        "tp_rr": [1.8, 2.0, 2.2, 2.4],
    },
    "forex": {
        "min_score": [4, 5, 6],
        "mom_delta": [0.02, 0.03, 0.04, 0.05],
        "sl_atr_mult": [0.9, 1.05, 1.2, 1.35],
        "tp_rr": [1.4, 1.6, 1.8, 2.0],
    },
}


@dataclass(frozen=True)
class ParamSet:
    min_score: int
    mom_delta: float
    sl_atr_mult: float
    tp_rr: float


def _build_grid_for_symbol(symbol: str, base: dict) -> List[ParamSet]:
    fam = _symbol_family(symbol)
    fam_grid = FAMILY_GRID[fam]
    all_sets = [
        ParamSet(
            int(ms),
            float(md),
            float(slm),
            float(tp),
        )
        for ms, md, slm, tp in itertools.product(
            fam_grid["min_score"],
            fam_grid["mom_delta"],
            fam_grid["sl_atr_mult"],
            fam_grid["tp_rr"],
        )
    ]
    baseline = ParamSet(
        int(base.get("min_score", 6)),
        round(float(base.get("momentum_buy_min", 100.05)) - 100.0, 4),
        round(float(base.get("sl_atr_mult", 1.2)), 4),
        round(float(base.get("tp_rr", 1.8)), 4),
    )
    if baseline not in all_sets:
        all_sets.append(baseline)
    return all_sets


def _ensure_numeric(df: pd.DataFrame, columns: Iterable[str]) -> None:
    for col in columns:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = pd.to_numeric(df[col], errors="coerce")


def _precompute_frame(df: pd.DataFrame, base: dict) -> Dict[str, np.ndarray]:
    ema_fast_period = int(base.get("ema_fast_period", 9))
    ema_mid_period = int(base.get("ema_mid_period", 21))
    ema_slow_period = int(base.get("ema_slow_period", 50))
    min_vol_ratio = float(base.get("min_volume_ratio", 1.0))
    rsi_buy_min = float(base.get("rsi_buy_min", 50.0))
    rsi_buy_max = float(base.get("rsi_buy_max", 68.0))
    rsi_sell_min = float(base.get("rsi_sell_min", 32.0))
    rsi_sell_max = float(base.get("rsi_sell_max", 50.0))
    min_atr_pct = float(base.get("min_atr_pct", 0.0))

    _ensure_numeric(
        df,
        [
            "open",
            "high",
            "low",
            "close",
            "atr",
            "vol_ratio",
            "rsi",
            "macd_line",
            "macd_signal",
            "macd_hist",
            "bull_power_25",
            "bear_power_25",
            "net_power_25",
            "momentum_14",
            "compression_ratio",
            "atr_baseline",
            "spread",
        ],
    )

    close = df["close"].ffill().bfill().to_numpy(dtype=float)
    high = df["high"].ffill().bfill().to_numpy(dtype=float)
    low = df["low"].ffill().bfill().to_numpy(dtype=float)

    atr = df["atr"].ffill().fillna(0.0).to_numpy(dtype=float)
    atr_baseline = df["atr_baseline"].ffill().fillna(0.0).to_numpy(dtype=float)
    compression_ratio = df["compression_ratio"].ffill().fillna(1.0).to_numpy(dtype=float)
    vol_ratio = df["vol_ratio"].fillna(1.0).to_numpy(dtype=float)
    rsi = df["rsi"].fillna(50.0).to_numpy(dtype=float)
    macd_line = df["macd_line"].fillna(0.0).to_numpy(dtype=float)
    macd_signal = df["macd_signal"].fillna(0.0).to_numpy(dtype=float)
    macd_hist = df["macd_hist"].fillna(0.0).to_numpy(dtype=float)
    momentum_14 = df["momentum_14"].fillna(100.0).to_numpy(dtype=float)
    bull_power = df["bull_power_25"].fillna(df.get("bull_power", 0.0)).fillna(0.0).to_numpy(dtype=float)
    bear_power = df["bear_power_25"].fillna(df.get("bear_power", 0.0)).fillna(0.0).to_numpy(dtype=float)
    net_power = df["net_power_25"].fillna(df.get("net_power", 0.0)).fillna(0.0).to_numpy(dtype=float)
    spread_points = df["spread"].fillna(0.0).to_numpy(dtype=float)

    ema_fast = pd.Series(close).ewm(span=ema_fast_period, adjust=False).mean().to_numpy(dtype=float)
    ema_mid = pd.Series(close).ewm(span=ema_mid_period, adjust=False).mean().to_numpy(dtype=float)
    ema_slow = pd.Series(close).ewm(span=ema_slow_period, adjust=False).mean().to_numpy(dtype=float)

    ema_fast_prev = np.roll(ema_fast, 1)
    ema_fast_prev[0] = ema_fast[0]
    ema_mid_prev = np.roll(ema_mid, 1)
    ema_mid_prev[0] = ema_mid[0]
    macd_hist_prev = np.roll(macd_hist, 1)
    macd_hist_prev[0] = macd_hist[0]
    bull_power_prev = np.roll(bull_power, 1)
    bull_power_prev[0] = bull_power[0]
    bear_power_prev = np.roll(bear_power, 1)
    bear_power_prev[0] = bear_power[0]

    # Trend regime approximation using same thresholds as classify_regime.
    trend_threshold = 0.65
    comp_thresh = 0.5
    exp_thresh = 1.5
    struct = df["structure"].fillna("NONE")
    bull_struct = struct.isin(["HH", "HL"]).astype(int).rolling(20, min_periods=1).sum().to_numpy(dtype=float)
    bear_struct = struct.isin(["LH", "LL"]).astype(int).rolling(20, min_periods=1).sum().to_numpy(dtype=float)
    total_struct = bull_struct + bear_struct
    bull_ratio = np.divide(bull_struct, np.where(total_struct == 0, np.nan, total_struct))
    bear_ratio = np.divide(bear_struct, np.where(total_struct == 0, np.nan, total_struct))
    trend_up = np.nan_to_num(bull_ratio) >= trend_threshold
    trend_down = np.nan_to_num(bear_ratio) >= trend_threshold
    expansion = atr > (atr_baseline * exp_thresh)
    compression = compression_ratio < comp_thresh
    regime_ok = (~compression) & (expansion | trend_up | trend_down)

    atr_pct = np.divide(atr, np.where(close == 0, np.nan, close))
    atr_ok = np.nan_to_num(atr_pct) >= min_atr_pct

    base_buy_score = (
        (ema_fast > ema_mid).astype(int)
        + (ema_mid > ema_slow).astype(int)
        + ((ema_fast > ema_fast_prev) & (ema_mid >= ema_mid_prev) & (close >= ema_fast)).astype(int)
        + ((macd_line > macd_signal) & (macd_hist > 0) & (macd_hist >= macd_hist_prev)).astype(int)
        + ((rsi >= rsi_buy_min) & (rsi <= rsi_buy_max)).astype(int)
        + ((bull_power > 0) & (net_power > 0) & (bear_power >= bear_power_prev)).astype(int)
        + (vol_ratio >= min_vol_ratio).astype(int)
    )
    base_sell_score = (
        (ema_fast < ema_mid).astype(int)
        + (ema_mid < ema_slow).astype(int)
        + ((ema_fast < ema_fast_prev) & (ema_mid <= ema_mid_prev) & (close <= ema_fast)).astype(int)
        + ((macd_line < macd_signal) & (macd_hist < 0) & (macd_hist <= macd_hist_prev)).astype(int)
        + ((rsi >= rsi_sell_min) & (rsi <= rsi_sell_max)).astype(int)
        + ((bear_power < 0) & (net_power < 0) & (bull_power <= bull_power_prev)).astype(int)
        + (vol_ratio >= min_vol_ratio).astype(int)
    )

    return {
        "close": close,
        "high": high,
        "low": low,
        "atr": atr,
        "spread_points": spread_points,
        "momentum_14": momentum_14,
        "base_buy_score": base_buy_score.astype(np.int16),
        "base_sell_score": base_sell_score.astype(np.int16),
        "regime_ok": regime_ok,
        "atr_ok": atr_ok,
    }


def _compute_signals(pre: Dict[str, np.ndarray], p: ParamSet) -> np.ndarray:
    mom = pre["momentum_14"]
    buy_score = pre["base_buy_score"] + (mom >= (100.0 + p.mom_delta)).astype(np.int16)
    sell_score = pre["base_sell_score"] + (mom <= (100.0 - p.mom_delta)).astype(np.int16)

    side = np.zeros_like(buy_score, dtype=np.int8)  # 1=BUY, -1=SELL
    buy_ok = (buy_score >= p.min_score) & (buy_score > sell_score)
    sell_ok = (sell_score >= p.min_score) & (sell_score > buy_score)
    side[buy_ok] = 1
    side[sell_ok] = -1

    # Apply gates
    side[(~pre["regime_ok"]) | (~pre["atr_ok"])] = 0
    return side


def _simulate(
    pre: Dict[str, np.ndarray],
    side: np.ndarray,
    p: ParamSet,
    *,
    hold_bars: int,
    lookback: int,
    point_size: float,
    min_sl_pct: float,
) -> Dict[str, float]:
    close = pre["close"]
    high = pre["high"]
    low = pre["low"]
    atr = pre["atr"]
    spread_points = pre["spread_points"]

    n = len(close)
    start_idx = max(lookback, 2)
    trade_idx = np.where(side[start_idx : n - hold_bars] != 0)[0] + start_idx
    if trade_idx.size == 0:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_r": 0.0,
            "avg_r": 0.0,
            "max_dd_r": 0.0,
            "score": -999.0,
        }

    rets: List[float] = []
    for i in trade_idx:
        s = int(side[i])
        entry = float(close[i])
        if not np.isfinite(entry) or entry <= 0:
            continue

        sl_dist = max(float(atr[i]) * p.sl_atr_mult, entry * min_sl_pct)
        if not np.isfinite(sl_dist) or sl_dist <= 0:
            continue
        tp_dist = sl_dist * p.tp_rr
        spread_price = float(spread_points[i]) * point_size

        if s == 1:
            sl = entry - sl_dist
            tp = entry + tp_dist
            effective_entry = entry + (spread_price / 2.0)
            out_r = None
            for j in range(i, i + hold_bars):
                if low[j] <= sl:
                    pnl = sl - effective_entry
                    out_r = pnl / sl_dist
                    break
                if high[j] >= tp:
                    pnl = tp - effective_entry
                    out_r = pnl / sl_dist
                    break
            if out_r is None:
                pnl = close[i + hold_bars - 1] - effective_entry
                out_r = pnl / sl_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist
            effective_entry = entry - (spread_price / 2.0)
            out_r = None
            for j in range(i, i + hold_bars):
                if high[j] >= sl:
                    pnl = effective_entry - sl
                    out_r = pnl / sl_dist
                    break
                if low[j] <= tp:
                    pnl = effective_entry - tp
                    out_r = pnl / sl_dist
                    break
            if out_r is None:
                pnl = effective_entry - close[i + hold_bars - 1]
                out_r = pnl / sl_dist
        rets.append(float(out_r))

    if not rets:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_r": 0.0,
            "avg_r": 0.0,
            "max_dd_r": 0.0,
            "score": -999.0,
        }

    arr = np.array(rets, dtype=float)
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    trades = int(arr.size)
    win_count = int(wins.size)
    loss_count = int(losses.size)
    win_rate = (win_count / trades) * 100.0 if trades else 0.0
    gross_profit = float(wins.sum()) if win_count else 0.0
    gross_loss = float(abs(losses.sum())) if loss_count else 1e-9
    pf = gross_profit / gross_loss if gross_loss > 0 else 0.0
    net_r = float(arr.sum())
    avg_r = float(arr.mean())
    equity_r = np.cumsum(arr)
    peak = np.maximum.accumulate(equity_r)
    max_dd_r = float(np.max(peak - equity_r)) if trades else 0.0

    # Ranking score with reliability penalty.
    score = (net_r * 3.2) + (win_rate * 0.22) + (min(pf, 4.5) * 6.0) - (max_dd_r * 0.9)
    if trades < 35:
        score -= float(35 - trades) * 1.2
    elif trades > 220:
        score -= float(trades - 220) * 0.05

    return {
        "trades": trades,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(pf, 3),
        "net_r": round(net_r, 3),
        "avg_r": round(avg_r, 4),
        "max_dd_r": round(max_dd_r, 3),
        "score": round(float(score), 4),
    }


def _infer_point_size(df: pd.DataFrame) -> float:
    close = pd.to_numeric(df.get("close"), errors="coerce").dropna()
    if close.empty:
        return 0.0001
    med = float(close.median())
    if med > 10000:
        return 1.0
    if med > 100:
        return 0.01
    if med > 10:
        return 0.001
    return 0.0001


def _optimize_symbol(
    symbol: str,
    timeframe: str,
    days: int,
    lookback: int,
    hold_bars: int,
    max_trials: int,
    seed: int,
) -> Dict:
    print(f"\n[OPT] {symbol} {timeframe} {days}d ...")
    bars = max(5000, int(days * 24 * 60 / 15) + 500)
    df = load_mt5_data(symbol, bars=bars, timeframe=timeframe, days=days)
    if df is None or df.empty:
        raise RuntimeError(f"No data for {symbol}")

    # Full feature pipeline once.
    df = add_volatility_features(df)
    df = add_structure_features(df)
    df = detect_displacement(df)
    df = add_institutional_features(df)
    df = detect_rsi_divergence(df)
    df = detect_candle_patterns(df)

    base = _resolve_params(symbol, {"symbol": symbol, "timeframe": timeframe, "regime_result": {"regime": "UNKNOWN"}})
    pre = _precompute_frame(df, base)
    min_sl_pct = float(base.get("min_sl_pct", 0.0007))
    point_size = _infer_point_size(df)

    grid = _build_grid_for_symbol(symbol, base)
    rng = random.Random(seed + abs(hash(symbol)) % 100000)
    if max_trials > 0 and len(grid) > max_trials:
        sampled = rng.sample(grid, k=max_trials)
        baseline = ParamSet(
            int(base.get("min_score", 6)),
            round(float(base.get("momentum_buy_min", 100.05)) - 100.0, 4),
            round(float(base.get("sl_atr_mult", 1.2)), 4),
            round(float(base.get("tp_rr", 1.8)), 4),
        )
        if baseline not in sampled:
            sampled.append(baseline)
        grid = sampled

    best = None
    baseline_metrics = None

    baseline_set = ParamSet(
        int(base.get("min_score", 6)),
        round(float(base.get("momentum_buy_min", 100.05)) - 100.0, 4),
        round(float(base.get("sl_atr_mult", 1.2)), 4),
        round(float(base.get("tp_rr", 1.8)), 4),
    )

    for params in grid:
        sig_side = _compute_signals(pre, params)
        metrics = _simulate(
            pre,
            sig_side,
            params,
            hold_bars=hold_bars,
            lookback=lookback,
            point_size=point_size,
            min_sl_pct=min_sl_pct,
        )
        row = {
            "symbol": symbol,
            "timeframe": timeframe,
            "days": days,
            "min_score": params.min_score,
            "mom_delta": params.mom_delta,
            "momentum_buy_min": round(100.0 + params.mom_delta, 4),
            "momentum_sell_max": round(100.0 - params.mom_delta, 4),
            "sl_atr_mult": params.sl_atr_mult,
            "tp_rr": params.tp_rr,
            **metrics,
        }
        if params == baseline_set:
            baseline_metrics = row
        if best is None or row["score"] > best["score"]:
            best = row

    assert best is not None
    if baseline_metrics is None:
        baseline_metrics = dict(best)
    else:
        baseline_metrics = dict(baseline_metrics)

    best = dict(best)

    improvement = {
        "score_delta": round(best["score"] - baseline_metrics["score"], 4),
        "net_r_delta": round(best["net_r"] - baseline_metrics["net_r"], 4),
        "wr_delta": round(best["win_rate"] - baseline_metrics["win_rate"], 4),
        "pf_delta": round(best["profit_factor"] - baseline_metrics["profit_factor"], 4),
        "trades_delta": int(best["trades"] - baseline_metrics["trades"]),
    }
    best["baseline"] = baseline_metrics
    best["improvement"] = improvement
    print(
        f"  -> best score={best['score']:.2f} | trades={best['trades']} | WR={best['win_rate']:.1f}% "
        f"| PF={best['profit_factor']:.2f} | netR={best['net_r']:.1f}"
    )
    return best


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize INDICATOR_CONFLUENCE per symbol")
    parser.add_argument("--symbols", default=",".join(SYMBOLS_DEFAULT), help="Comma-separated symbols")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--lookback", type=int, default=120)
    parser.add_argument("--hold-bars", type=int, default=48)
    parser.add_argument("--max-trials", type=int, default=180)
    parser.add_argument("--seed", type=int, default=20260310)
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    results = []
    for sym in symbols:
        try:
            res = _optimize_symbol(
                symbol=sym,
                timeframe=args.timeframe.upper(),
                days=int(args.days),
                lookback=int(args.lookback),
                hold_bars=int(args.hold_bars),
                max_trials=int(args.max_trials),
                seed=int(args.seed),
            )
            results.append(res)
        except Exception as e:
            print(f"[ERR] {sym}: {e}")

    if not results:
        print("No results generated.")
        return 1

    out_dir = Path("d:/VibeCode/Trade/backend/trader/data")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"indicator_confluence_tuning_{args.timeframe.upper()}_{args.days}d_{stamp}.json"
    csv_path = out_dir / f"indicator_confluence_tuning_{args.timeframe.upper()}_{args.days}d_{stamp}.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    table_rows = []
    for r in results:
        table_rows.append(
            {
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "days": r["days"],
                "min_score": r["min_score"],
                "momentum_buy_min": r["momentum_buy_min"],
                "momentum_sell_max": r["momentum_sell_max"],
                "sl_atr_mult": r["sl_atr_mult"],
                "tp_rr": r["tp_rr"],
                "trades": r["trades"],
                "win_rate": r["win_rate"],
                "profit_factor": r["profit_factor"],
                "net_r": r["net_r"],
                "max_dd_r": r["max_dd_r"],
                "score": r["score"],
                "score_delta_vs_base": r["improvement"]["score_delta"],
                "net_r_delta_vs_base": r["improvement"]["net_r_delta"],
            }
        )

    table_df = pd.DataFrame(table_rows).sort_values(by="symbol")
    table_df.to_csv(csv_path, index=False)

    print("\n=== Recommended Tuning Table ===")
    print(table_df.to_string(index=False))
    print(f"\nSaved JSON: {json_path}")
    print(f"Saved CSV : {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
