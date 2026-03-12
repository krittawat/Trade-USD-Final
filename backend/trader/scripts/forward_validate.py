"""
Forward validation helper for trader-engine strategies.

Runs one in-sample train window and the immediately following out-of-sample
holdout window using the same BacktestEngine configuration.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from backend.trader.data.mapper import mapper
from backend.trader.scripts.run_backtest import (
    BacktestEngine,
    load_backtest_news_events,
    load_mt5_data,
    load_strategy_params_arg,
)


def _parse_csv_arg(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    return [item.strip().upper() for item in text.split(",") if item.strip()]


def _parse_int_csv_arg(raw: str) -> list[int]:
    values: list[int] = []
    for item in _parse_csv_arg(raw):
        try:
            value = int(item)
        except Exception:
            continue
        if value > 0 and value not in values:
            values.append(value)
    return values


def _to_standard_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip()
    if not raw:
        return ""
    standardized = str(mapper.to_standard(raw) or raw).upper()
    if standardized.endswith(("M", "C")) and len(standardized) > 1:
        standardized = standardized[:-1]
    return standardized


def _open_duckdb(path: str):
    try:
        import duckdb  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"duckdb_import_failed: {exc}") from exc
    db_path = Path(path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def _load_duckdb_data(conn, symbol: str, timeframe: str, days: int) -> Optional[pd.DataFrame]:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, int(days)))
    try:
        df = conn.execute(
            """
            SELECT time, open, high, low, close, tick_volume
            FROM ohlcv_cache
            WHERE symbol = ?
              AND timeframe = ?
              AND time >= ?
            ORDER BY time ASC
            """,
            [str(symbol).upper(), str(timeframe).upper(), cutoff],
        ).df()
    except Exception:
        return None

    if df is None or df.empty:
        return None

    try:
        df["time"] = pd.to_datetime(df["time"], utc=True)
    except Exception:
        pass
    return df


def _load_bars(symbol: str, timeframe: str, days: int, data_source: str, duckdb_path: str) -> pd.DataFrame:
    mode = str(data_source or "auto").lower()
    if mode not in {"auto", "mt5", "duckdb"}:
        mode = "auto"

    if mode in {"auto", "duckdb"}:
        conn = None
        try:
            conn = _open_duckdb(duckdb_path)
            duck_df = _load_duckdb_data(conn, symbol, timeframe, days)
            if duck_df is not None and not duck_df.empty:
                return duck_df
            if mode == "duckdb":
                raise RuntimeError(f"DuckDB cache miss for {symbol} {timeframe}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    return load_mt5_data(symbol=symbol, bars=0, timeframe=timeframe, days=days)


def _score(metrics: dict, min_trades: int) -> float:
    trades = int(metrics.get("total_trades", 0) or 0)
    wr = float(metrics.get("win_rate", 0.0) or 0.0)
    pf = float(metrics.get("profit_factor", 0.0) or 0.0)
    dd = float(metrics.get("max_dd", 0.0) or 0.0)
    pnl = float(metrics.get("net_pnl", 0.0) or 0.0)

    reliability_k = max(4, int(max(1, min_trades)) * 2)
    reliability = trades / (trades + reliability_k) if trades > 0 else 0.0
    pf_capped = min(max(pf, 0.0), 6.0)

    raw_score = (pnl * 1.0) + (wr * 0.60) + (pf_capped * 8.0) - (dd * 0.70) + (min(trades, 50) * 0.15)
    score = raw_score * (0.35 + 0.65 * reliability)
    if trades < min_trades:
        score -= (20.0 + (min_trades - trades) * 2.0)
    return round(score, 4)


def _slice_window(
    df: pd.DataFrame,
    train_days: int,
    test_days: int,
    end_offset_days: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    frame = df.copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = frame.sort_values("time").reset_index(drop=True)

    end_time = pd.Timestamp(frame["time"].iloc[-1]).tz_convert(UTC)
    if end_offset_days > 0:
        end_time = end_time - pd.Timedelta(days=end_offset_days)
    split_time = end_time - pd.Timedelta(days=test_days)
    train_start = split_time - pd.Timedelta(days=train_days)

    train_df = frame[(frame["time"] >= train_start) & (frame["time"] < split_time)].copy()
    test_df = frame[(frame["time"] >= split_time) & (frame["time"] <= end_time)].copy()
    return train_df, test_df, train_start, split_time, end_time


def _slice_trailing_window(df: pd.DataFrame, days: int, end_offset_days: int) -> tuple[pd.DataFrame, pd.Timestamp, pd.Timestamp]:
    frame = df.copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = frame.sort_values("time").reset_index(drop=True)

    end_time = pd.Timestamp(frame["time"].iloc[-1]).tz_convert(UTC)
    if end_offset_days > 0:
        end_time = end_time - pd.Timedelta(days=end_offset_days)
    start_time = end_time - pd.Timedelta(days=days)
    window_df = frame[(frame["time"] >= start_time) & (frame["time"] <= end_time)].copy()
    return window_df, start_time, end_time


def _run_engine_silent(
    *,
    symbol: str,
    timeframe: str,
    strategy: str,
    equity: float,
    brain_params: dict,
    df: pd.DataFrame,
    lookback: int,
    hold_bars: int,
) -> tuple[dict, str]:
    engine = BacktestEngine(
        symbol=symbol,
        initial_equity=equity,
        timeframe=timeframe,
        strategy_mode=strategy,
        brain_params=brain_params,
    )
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        metrics = engine.run(df, lookback=lookback, hold_bars=hold_bars)
    return metrics, capture.getvalue()


def _decay_label(train_score: float, test_score: float) -> str:
    delta = round(test_score - train_score, 4)
    if delta >= 10.0:
        return "IMPROVING"
    if delta <= -10.0:
        return "DECAYING"
    return "STABLE"


def main() -> int:
    parser = argparse.ArgumentParser(description="Forward validation for trader-engine strategies")
    parser.add_argument("--symbols", default="XAUUSD,BTCUSD", help="CSV symbols")
    parser.add_argument("--timeframes", default="M5,M15,H1", help="CSV timeframes")
    parser.add_argument("--strategy", default="alpha_v7_ict")
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--window-days", default="30,90", help="CSV trailing windows to evaluate")
    parser.add_argument("--end-offset-days", type=int, default=0)
    parser.add_argument("--equity", type=float, default=100.0)
    parser.add_argument("--lookback", type=int, default=100)
    parser.add_argument("--hold-bars", type=int, default=50)
    parser.add_argument("--min-trades", type=int, default=3)
    parser.add_argument("--min-test-trades", type=int, default=1)
    parser.add_argument("--max-dd", type=float, default=25.0)
    parser.add_argument("--data-source", choices=["auto", "mt5", "duckdb"], default="auto")
    parser.add_argument("--duckdb-path", default="backend/data/duckdb/analytics.duckdb")
    parser.add_argument("--strategy-params-json", default="")
    parser.add_argument("--news-events-json", default="")
    args = parser.parse_args()

    symbols = [_to_standard_symbol(sym) for sym in _parse_csv_arg(args.symbols)]
    symbols = [sym for sym in symbols if sym]
    timeframes = _parse_csv_arg(args.timeframes)
    if not symbols or not timeframes:
        print("No symbols/timeframes to validate.")
        return 1
    window_days = _parse_int_csv_arg(args.window_days)
    if not window_days:
        print("No window days to validate.")
        return 1

    try:
        brain_params = load_strategy_params_arg(args.strategy_params_json, args.strategy)
    except Exception as exc:
        print(f"[ERR] invalid --strategy-params-json: {exc}")
        return 2

    try:
        loaded_news_events = load_backtest_news_events(args.news_events_json)
        if loaded_news_events:
            print(f"[NEWS] Loaded {loaded_news_events} historical events")
    except Exception as exc:
        print(f"[WARN] failed to load news events: {exc}")

    total_days = int(max(args.train_days + args.test_days, max(window_days)) + args.end_offset_days + 2)
    rows: list[dict] = []

    for symbol in symbols:
        for timeframe in timeframes:
            print(f"[RUN] {symbol} {timeframe} | train={args.train_days}d test={args.test_days}d")
            try:
                df = _load_bars(symbol, timeframe, total_days, args.data_source, args.duckdb_path)
                train_df, test_df, train_start, split_time, end_time = _slice_window(
                    df,
                    train_days=args.train_days,
                    test_days=args.test_days,
                    end_offset_days=args.end_offset_days,
                )
                min_required = max(int(args.lookback), 100) + int(args.hold_bars) + 10
                if len(train_df) < min_required or len(test_df) < min_required:
                    rows.append(
                        {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "error": "insufficient_bars",
                            "train_bars": len(train_df),
                            "test_bars": len(test_df),
                        }
                    )
                    continue

                window_metrics: dict[str, dict] = {}
                insufficient_window = None
                for window_days_value in window_days:
                    window_df, window_start, window_end = _slice_trailing_window(
                        df,
                        days=window_days_value,
                        end_offset_days=args.end_offset_days,
                    )
                    if len(window_df) < min_required:
                        insufficient_window = f"insufficient_window_bars_{window_days_value}d"
                        break
                    metrics, _ = _run_engine_silent(
                        symbol=symbol,
                        timeframe=timeframe,
                        strategy=args.strategy,
                        equity=args.equity,
                        brain_params=brain_params,
                        df=window_df,
                        lookback=args.lookback,
                        hold_bars=args.hold_bars,
                    )
                    window_metrics[f"{window_days_value}d"] = {
                        "start": window_start.isoformat(),
                        "end": window_end.isoformat(),
                        "bars": len(window_df),
                        "score": _score(metrics, args.min_test_trades),
                        "metrics": metrics,
                    }
                if insufficient_window is not None:
                    rows.append(
                        {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "error": insufficient_window,
                        }
                    )
                    continue

                train_metrics, _ = _run_engine_silent(
                    symbol=symbol,
                    timeframe=timeframe,
                    strategy=args.strategy,
                    equity=args.equity,
                    brain_params=brain_params,
                    df=train_df,
                    lookback=args.lookback,
                    hold_bars=args.hold_bars,
                )
                trailing_test_key = f"{int(args.test_days)}d"
                trailing_test = window_metrics.get(trailing_test_key, {})
                if trailing_test:
                    test_metrics = dict(trailing_test.get("metrics", {}))
                else:
                    test_metrics, _ = _run_engine_silent(
                        symbol=symbol,
                        timeframe=timeframe,
                        strategy=args.strategy,
                        equity=args.equity,
                        brain_params=brain_params,
                        df=test_df,
                        lookback=args.lookback,
                        hold_bars=args.hold_bars,
                    )

                train_score = _score(train_metrics, args.min_trades)
                test_score = _score(test_metrics, args.min_test_trades)
                edge_persisted = (
                    float(train_metrics.get("net_pnl", 0.0) or 0.0) > 0.0
                    and float(test_metrics.get("net_pnl", 0.0) or 0.0) > 0.0
                    and int(train_metrics.get("total_trades", 0) or 0) >= int(args.min_trades)
                    and int(test_metrics.get("total_trades", 0) or 0) >= int(args.min_test_trades)
                    and float(test_metrics.get("profit_factor", 0.0) or 0.0) >= 1.0
                    and float(test_metrics.get("max_dd", 999.0) or 999.0) <= float(args.max_dd)
                )
                window_persisted = all(
                    float(window_rec["metrics"].get("net_pnl", 0.0) or 0.0) > 0.0
                    and int(window_rec["metrics"].get("total_trades", 0) or 0) >= int(args.min_test_trades)
                    and float(window_rec["metrics"].get("profit_factor", 0.0) or 0.0) >= 1.0
                    for window_rec in window_metrics.values()
                )

                rows.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "windows": window_metrics,
                        "train_window": {
                            "start": train_start.isoformat(),
                            "end": split_time.isoformat(),
                            "bars": len(train_df),
                        },
                        "test_window": {
                            "start": split_time.isoformat(),
                            "end": end_time.isoformat(),
                            "bars": len(test_df),
                        },
                        "train": train_metrics,
                        "test": test_metrics,
                        "train_score": train_score,
                        "test_score": test_score,
                        "decay": _decay_label(train_score, test_score),
                        "edge_persisted": edge_persisted,
                        "window_persisted": window_persisted,
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "error": str(exc),
                    }
                )

    rows.sort(
        key=lambda row: (
            0 if row.get("edge_persisted") else 1,
            -float(row.get("test_score", -999999.0) or -999999.0),
            -float(((row.get("test") or {}).get("net_pnl", -999999.0) or -999999.0)),
        )
    )

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "strategy": args.strategy,
        "symbols": symbols,
        "timeframes": timeframes,
        "window_days": window_days,
        "train_days": int(args.train_days),
        "test_days": int(args.test_days),
        "end_offset_days": int(args.end_offset_days),
        "requested_equity": float(args.equity),
        "rows": rows,
    }

    out_dir = ROOT / "tmp"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"forward_validate_{args.strategy}_{stamp}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("\nFORWARD VALIDATION")
    print("=" * 100)
    for row in rows:
        if row.get("error"):
            print(f"{row['symbol']:>7} {row['timeframe']:>3} | ERROR: {row['error']}")
            continue
        test = row["test"]
        train = row["train"]
        persisted = "YES" if row["edge_persisted"] else "NO"
        windows = row.get("windows", {})
        window_bits = []
        for window_days_value in window_days:
            rec = windows.get(f"{window_days_value}d", {})
            metrics = rec.get("metrics", {})
            window_bits.append(
                f"{window_days_value}d t={metrics.get('total_trades',0):>2} pnl={metrics.get('net_pnl',0):>9}"
            )
        print(
            f"{row['symbol']:>7} {row['timeframe']:>3} | "
            f"{' | '.join(window_bits)} | "
            f"train t={train.get('total_trades',0):>2} pnl={train.get('net_pnl',0):>9} | "
            f"test t={test.get('total_trades',0):>2} pnl={test.get('net_pnl',0):>9} pf={test.get('profit_factor',0):>6} | "
            f"decay={row['decay']:<9} | WIN={('YES' if row['window_persisted'] else 'NO'):>3} | EDGE={persisted}"
        )

    print(f"\nSaved report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
