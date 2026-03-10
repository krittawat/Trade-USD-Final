"""
MT5 -> DuckDB OHLCV cache loader.

Purpose:
  - Pull bars once from MT5.
  - Store into DuckDB table `ohlcv_cache`.
  - Reuse in matrix backtests via --data-source duckdb/auto.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
from typing import List

import pandas as pd

from backend.trader.data.mapper import mapper
from backend.trader.scripts.run_backtest import load_mt5_data


BASE_SYMBOLS = ["XAUUSDm", "BTCUSDm", "USOILm", "US30m", "USTECm"]
DEFAULT_TIMEFRAMES = ["M5", "M15", "H1"]


def _parse_csv_arg(raw: str) -> List[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    return [x.strip().upper() for x in text.split(",") if x.strip()]


def _to_standard_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip()
    if not raw:
        return ""
    std = mapper.to_standard(raw)
    up = str(std or raw).upper()

    canonical_no_suffix = {"USTEC", "US30", "US500", "NAS100", "JP225", "HK50", "DE40"}
    if up in canonical_no_suffix:
        return up

    if up.endswith(("M", "C")):
        core = up[:-1]
        if core in canonical_no_suffix or core in {"XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "USOIL", "UKOIL"} or core.endswith("USD"):
            return core
    return up


def _resolve_symbols(symbols_arg: str) -> List[str]:
    broker_symbols = _parse_csv_arg(symbols_arg) if symbols_arg else list(BASE_SYMBOLS)
    seen = set()
    out = []
    for broker_sym in broker_symbols:
        std = _to_standard_symbol(broker_sym)
        if not std or std in seen:
            continue
        seen.add(std)
        out.append(std)
    return out


def _resolve_timeframes(timeframes_arg: str) -> List[str]:
    tfs = _parse_csv_arg(timeframes_arg) if timeframes_arg else list(DEFAULT_TIMEFRAMES)
    seen = set()
    out = []
    for tf in tfs:
        if tf in seen:
            continue
        seen.add(tf)
        out.append(tf)
    return out


def _ensure_schema(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ohlcv_cache (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            time TIMESTAMP NOT NULL,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            tick_volume BIGINT,
            real_volume BIGINT,
            spread DOUBLE,
            loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_ohlcv_cache_lookup
        ON ohlcv_cache(symbol, timeframe, time)
        """
    )


def _prepare_bars(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    if "time" not in work.columns:
        raise ValueError("missing `time` column")

    work["time"] = pd.to_datetime(work["time"], utc=True, errors="coerce")
    work = work.dropna(subset=["time"])

    for col in ["open", "high", "low", "close", "spread"]:
        if col not in work.columns:
            work[col] = 0.0
    for col in ["tick_volume", "real_volume"]:
        if col not in work.columns:
            work[col] = 0

    work = work[
        ["time", "open", "high", "low", "close", "tick_volume", "real_volume", "spread"]
    ].sort_values("time")
    return work


def _upsert_bars(conn, symbol: str, timeframe: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    bars = _prepare_bars(df)
    if bars.empty:
        return 0

    min_t = bars["time"].iloc[0].to_pydatetime()
    max_t = bars["time"].iloc[-1].to_pydatetime()

    conn.execute(
        """
        DELETE FROM ohlcv_cache
        WHERE symbol = ?
          AND timeframe = ?
          AND time >= ?
          AND time <= ?
        """,
        [symbol, timeframe, min_t, max_t],
    )

    conn.register("bars_tmp", bars)
    try:
        conn.execute(
            """
            INSERT INTO ohlcv_cache (
                symbol, timeframe, time, open, high, low, close,
                tick_volume, real_volume, spread, loaded_at
            )
            SELECT ?, ?, time, open, high, low, close,
                   tick_volume, real_volume, spread, CURRENT_TIMESTAMP
            FROM bars_tmp
            """,
            [symbol, timeframe],
        )
    finally:
        conn.unregister("bars_tmp")
    return int(len(bars))


def main() -> int:
    parser = argparse.ArgumentParser(description="Load MT5 bars into DuckDB ohlcv_cache")
    parser.add_argument("--days", type=int, default=120, help="Lookback window in days")
    parser.add_argument("--symbols", default="", help="Symbols CSV (default: focused universe)")
    parser.add_argument("--timeframes", default="", help="Timeframes CSV")
    parser.add_argument("--duckdb-path", default="backend/data/duckdb/analytics.duckdb")
    args = parser.parse_args()

    symbols = _resolve_symbols(args.symbols)
    timeframes = _resolve_timeframes(args.timeframes)
    if not symbols or not timeframes:
        print("No symbols/timeframes to cache.")
        return 1

    try:
        import duckdb  # type: ignore
    except Exception as e:
        print(f"[FATAL] duckdb import failed: {e}")
        return 1

    db_path = Path(args.duckdb_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(db_path))
    _ensure_schema(conn)

    loaded_rows = 0
    jobs = len(symbols) * len(timeframes)
    done = 0
    print("=" * 96)
    print(
        f"MT5 -> DuckDB Cache Start | db={db_path} | days={args.days} | "
        f"symbols={len(symbols)} | tfs={len(timeframes)} | jobs={jobs}"
    )
    print("=" * 96)

    try:
        for sym in symbols:
            for tf in timeframes:
                done += 1
                print(f"[{done}/{jobs}] {sym} {tf}")
                try:
                    df = load_mt5_data(symbol=sym, bars=0, timeframe=tf, days=args.days)
                    n = _upsert_bars(conn, sym, tf, df)
                    loaded_rows += n
                    print(f"  -> cached {n} bars")
                except Exception as e:
                    print(f"  -> skip: {e}")
    finally:
        conn.close()

    print("\n" + "=" * 96)
    print("MT5 -> DuckDB Cache Complete")
    print("=" * 96)
    print(f"DuckDB: {db_path}")
    print(f"Rows loaded: {loaded_rows}")
    print(f"Finished at: {datetime.now(UTC).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
