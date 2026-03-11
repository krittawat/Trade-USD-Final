from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.trader.storage.sqlite_db import DataStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed trader TF override routes from a backtest matrix JSON report."
    )
    parser.add_argument(
        "--report-json",
        required=True,
        help="Path to backtest_matrix_360 JSON report.",
    )
    parser.add_argument(
        "--db-path",
        default="d:/VibeCode/Trade/trader/data/opus.db",
        help="Path to trader sqlite DB.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Keep top K ranked rows per symbol.",
    )
    parser.add_argument(
        "--prefer-symbol-timeframe",
        default="",
        help="Force one symbol:timeframe to rank first, for example USOIL:M15.",
    )
    return parser.parse_args()


def load_report(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    rows = payload.get("ranked_rows") or []
    if not rows:
        raise ValueError(f"No ranked_rows found in report: {path}")
    return payload


def parse_preference(raw: str) -> tuple[str, str] | None:
    if not raw:
        return None
    token = str(raw).strip().upper()
    if ":" not in token:
        raise ValueError("prefer-symbol-timeframe must look like SYMBOL:TF")
    symbol, timeframe = token.split(":", 1)
    symbol = symbol.strip().upper()
    timeframe = timeframe.strip().upper()
    if not symbol or not timeframe:
        raise ValueError("prefer-symbol-timeframe must look like SYMBOL:TF")
    return symbol, timeframe


def sort_key(row: dict, preferred: tuple[str, str] | None) -> tuple[float, float, float, float]:
    symbol = str(row.get("symbol", "")).upper()
    timeframe = str(row.get("timeframe", "")).upper()
    is_preferred = 0 if preferred and (symbol, timeframe) == preferred else 1
    rank = int(row.get("rank", 9999) or 9999)
    score = float(row.get("score", 0.0) or 0.0)
    trades = float(row.get("total_trades", row.get("trades", 0)) or 0.0)
    return (is_preferred, rank, -score, -trades)


def build_seed_rows(report: dict, top_k: int, preferred: tuple[str, str] | None) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in report.get("ranked_rows") or []:
        symbol = str(row.get("symbol", "")).upper().strip()
        timeframe = str(row.get("timeframe", "")).upper().strip()
        if not symbol or not timeframe:
            continue
        grouped.setdefault(symbol, []).append(row)

    seeded: list[dict] = []
    for symbol, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: sort_key(row, preferred))
        for idx, row in enumerate(ordered[: max(1, int(top_k))], start=1):
            seeded.append(
                {
                    "symbol": symbol,
                    "timeframe": str(row.get("timeframe", "")).upper(),
                    "rank": idx,
                    "score": float(row.get("score", 0.0) or 0.0),
                    "total_trades": int(row.get("total_trades", row.get("trades", 0)) or 0),
                    "win_rate": float(row.get("win_rate", 0.0) or 0.0),
                    "profit_factor": float(row.get("profit_factor", 0.0) or 0.0),
                    "net_pnl": float(row.get("net_pnl", 0.0) or 0.0),
                    "max_dd": float(row.get("max_dd", row.get("max_drawdown_pct", 0.0)) or 0.0),
                    "engine": str(row.get("engine", report.get("engine", "trader"))).lower(),
                }
            )
    return seeded


def main() -> int:
    args = parse_args()
    report_path = Path(args.report_json).expanduser().resolve()
    if not report_path.exists():
        raise FileNotFoundError(f"Report not found: {report_path}")

    report = load_report(report_path)
    preferred = parse_preference(args.prefer_symbol_timeframe)
    seed_rows = build_seed_rows(report, args.top_k, preferred)
    if not seed_rows:
        raise ValueError(f"No seed rows built from report: {report_path}")

    report_run_id = str(report.get("run_id", "unknown")).strip()
    run_id = f"seed_{report_run_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    db = DataStore(db_path=args.db_path)
    notes = f"Seeded from {report_path.name}"
    if preferred:
        notes += f"; preferred={preferred[0]}:{preferred[1]}"

    db.upsert_backtest_override_run(
        run_id,
        engine=str(report.get("engine", "trader")).lower(),
        days=int(report.get("days", 0) or 0),
        equity=float(report.get("effective_equity", report.get("requested_equity", 0.0)) or 0.0),
        lookback=int(report.get("lookback", 100) or 100),
        hold_bars=int(report.get("hold_bars", 50) or 50),
        min_trades=int(report.get("min_trades", 0) or 0),
        max_bars_per_run=int(report.get("max_bars_per_run", 0) or 0),
        status="RUNNING",
        notes=notes,
    )
    inserted = db.upsert_backtest_tf_overrides(run_id, seed_rows, replace_run_rows=True)
    db.complete_backtest_override_run(
        run_id,
        status="COMPLETED",
        total_rows=inserted,
        notes=notes,
    )

    print("=== Seed Backtest Overrides Complete ===")
    print(f"DB:       {Path(args.db_path).resolve()}")
    print(f"Report:   {report_path}")
    print(f"Run ID:   {run_id}")
    print(f"Rows:     {inserted}")
    if preferred:
        print(f"Preferred: {preferred[0]} {preferred[1]}")

    by_symbol: dict[str, list[dict]] = {}
    for row in seed_rows:
        by_symbol.setdefault(row["symbol"], []).append(row)
    for symbol in sorted(by_symbol):
        top = sorted(by_symbol[symbol], key=lambda row: row["rank"])[0]
        print(
            f"{symbol:>7} -> {top['timeframe']:<3} "
            f"rank={top['rank']} trades={top['total_trades']} "
            f"wr={top['win_rate']:.1f}% pf={top['profit_factor']:.2f} "
            f"pnl={top['net_pnl']:.4f} score={top['score']:.4f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
