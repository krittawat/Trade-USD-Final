"""
Backtest Matrix (360D x All TFs) with DB-driven TF overrides.

Runs matrix backtests across configured symbols/timeframes using:
  - trader engine (selector-based)
  - app engine (active StrategyFactory strategies)
  - both (run both, keep best score per symbol/timeframe)

Persists ranked results into trader SQLite:
  - backtest_tf_override_runs
  - backtest_tf_overrides
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from backend.trader.data.mapper import mapper
from backend.trader.data.time_utils import time_utils
from backend.trader.features.candle_patterns import detect_candle_patterns
from backend.trader.features.divergence import detect_rsi_divergence
from backend.trader.features.institutional import add_institutional_features
from backend.trader.features.structure import add_structure_features, detect_displacement
from backend.trader.features.volatility import add_volatility_features
from backend.trader.regime.classifier import classify_regime
from backend.trader.scripts.run_backtest import (
    BacktestEngine,
    CONFIG as TRADER_CONFIG,
    SYMBOL_SPECS,
    load_mt5_data,
    load_strategy_params_arg,
    risk_engine,
)
from backend.trader.storage.sqlite_db import db


# Reduce noisy logs while matrix backtests are running.
logging.disable(logging.INFO)


def _configured_broker_symbols() -> List[str]:
    raw = list((TRADER_CONFIG.get("symbols", {}) or {}).values())
    seen = set()
    out = []
    for sym in raw:
        key = str(sym or "").upper().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(str(sym))
    return out


BASE_SYMBOLS = _configured_broker_symbols()
SHADOW_SYMBOLS: List[str] = []
DEFAULT_TIMEFRAMES = ["M3", "M5", "M6", "M12", "M15", "M30", "H1", "H2", "H4", "D1"]


_APP_FACTORY_CACHE: Optional[Dict] = None


def _open_duckdb(path: str):
    try:
        import duckdb  # type: ignore
    except Exception as e:
        raise RuntimeError(f"duckdb_import_failed: {e}") from e
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


def _to_standard_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip()
    if not raw:
        return ""
    std = mapper.to_standard(raw)
    up = str(std or raw).upper()

    # Do not trim canonical index symbols that naturally end with 'C'.
    canonical_no_suffix = {"USTEC", "US30", "US500", "NAS100", "JP225", "HK50", "DE40"}
    if up in canonical_no_suffix:
        return up

    if up.endswith(("M", "C")):
        core = up[:-1]
        if core in canonical_no_suffix or core in {"XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "USOIL", "UKOIL"} or core.endswith("USD"):
            return core
    return up


def _score(metrics: dict, min_trades: int) -> float:
    trades = int(metrics.get("total_trades", 0) or 0)
    wr = float(metrics.get("win_rate", 0.0) or 0.0)
    pf = float(metrics.get("profit_factor", 0.0) or 0.0)
    wins = int(metrics.get("wins", 0) or 0)
    losses = int(metrics.get("losses", 0) or 0)
    dd = float(metrics.get("max_dd", 0.0) or 0.0)
    pnl = float(metrics.get("net_pnl", 0.0) or 0.0)

    # Reliability damping:
    # - Prevent single/few trades from dominating rank (PF/WR can look perfect by chance)
    # - Keep score comparable while still rewarding larger sample sizes
    reliability_k = max(6, int(max(1, min_trades)) * 2)
    reliability = trades / (trades + reliability_k) if trades > 0 else 0.0

    pf_capped = min(max(pf, 0.0), 6.0)
    if losses <= 0 and wins <= 2:
        # Pure-win tiny samples are usually unstable; cap PF impact aggressively.
        pf_capped = min(pf_capped, 1.20)

    raw_score = (pnl * 1.0) + (wr * 0.60) + (pf_capped * 8.0) - (dd * 0.70) + (min(trades, 50) * 0.15)
    score = raw_score * (0.35 + 0.65 * reliability)
    if trades < min_trades:
        score -= (30.0 + (min_trades - trades) * 2.0)
    return round(score, 4)


def _is_production_candidate(row: Dict, args) -> bool:
    trades = int(row.get("total_trades", row.get("trades", 0)) or 0)
    wr = float(row.get("win_rate", 0.0) or 0.0)
    pf = float(row.get("profit_factor", 0.0) or 0.0)
    dd = float(row.get("max_dd", 999.0) or 999.0)
    pnl = float(row.get("net_pnl", 0.0) or 0.0)
    sc = float(row.get("score", -999.0) or -999.0)

    # Always enforce at least 3 trades for production route selection.
    prod_min_trades = max(3, int(getattr(args, "prod_min_trades", 3)))
    return (
        trades >= prod_min_trades
        and wr >= float(getattr(args, "prod_min_win_rate", 35.0))
        and pf >= float(getattr(args, "prod_min_pf", 1.05))
        and dd <= float(getattr(args, "prod_max_dd", 25.0))
        and sc >= float(getattr(args, "prod_min_score", 0.0))
        and (not bool(getattr(args, "prod_require_positive_pnl", True)) or pnl > 0.0)
    )


def _parse_csv_arg(raw: str) -> List[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    return [x.strip().upper() for x in text.split(",") if x.strip()]


def _resolve_symbols(symbols_arg: str, include_shadow: bool) -> List[str]:
    broker_symbols = _parse_csv_arg(symbols_arg) if symbols_arg else list(BASE_SYMBOLS)
    if include_shadow:
        broker_symbols.extend(SHADOW_SYMBOLS)

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


def _load_bars(
    *,
    symbol: str,
    timeframe: str,
    days: int,
    data_source: str,
    duck_conn,
) -> pd.DataFrame:
    mode = str(data_source or "auto").lower()
    if mode not in {"auto", "mt5", "duckdb"}:
        mode = "auto"

    if mode in {"auto", "duckdb"} and duck_conn is not None:
        duck_df = _load_duckdb_data(duck_conn, symbol, timeframe, days)
        if duck_df is not None and not duck_df.empty:
            print(f"  [DUCKDB] Loaded {len(duck_df)} bars for {symbol} ({timeframe})")
            return duck_df
        if mode == "duckdb":
            raise RuntimeError(f"DuckDB cache miss for {symbol} ({timeframe})")

    return load_mt5_data(symbol=symbol, bars=0, timeframe=timeframe, days=days)


def _get_app_factory_ctx() -> Dict:
    global _APP_FACTORY_CACHE
    if _APP_FACTORY_CACHE is not None:
        return _APP_FACTORY_CACHE

    from backend.app.core.config import get_settings
    from backend.app.db.sqlite import SQLiteStore
    from backend.app.strategy.factory import StrategyFactory

    settings = get_settings()
    app_db = SQLiteStore(settings)
    app_db.connect()
    factory = StrategyFactory()
    registered = factory.auto_register(db=app_db)
    _APP_FACTORY_CACHE = {
        "factory": factory,
        "db": app_db,
        "registered": int(registered or 0),
    }
    return _APP_FACTORY_CACHE


class AppFactoryBacktestEngine:
    def __init__(self, symbol: str, initial_equity: float = 1000.0):
        self.symbol = symbol
        self.initial_equity = float(initial_equity)
        self.equity = float(initial_equity)
        self.peak_equity = float(initial_equity)
        self.trades: List[Dict] = []
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.max_dd = 0.0
        self._cooldown_until_bar = 0
        self.specs = SYMBOL_SPECS.get(symbol, SYMBOL_SPECS["XAUUSD"]).copy()

    def _simulate_trade(self, signal: dict, future_bars: pd.DataFrame) -> dict:
        entry = float(signal["entry_price"])
        sl = float(signal["sl"])
        tp1 = float(signal["tp1"])
        side = str(signal["side"]).upper()
        spread_cost = float(self.specs.get("spread_points", 30) or 0.0) * float(self.specs.get("point", 0.01) or 0.01)

        for _, bar in future_bars.iterrows():
            if side == "BUY":
                effective_entry = entry + spread_cost / 2
                if float(bar["low"]) <= sl:
                    pnl = sl - effective_entry
                    return {"result": "SL", "pnl": pnl, "exit_price": sl}
                if float(bar["high"]) >= tp1:
                    pnl = tp1 - effective_entry
                    return {"result": "TP1", "pnl": pnl, "exit_price": tp1}
            else:
                effective_entry = entry - spread_cost / 2
                if float(bar["high"]) >= sl:
                    pnl = effective_entry - sl
                    return {"result": "SL", "pnl": pnl, "exit_price": sl}
                if float(bar["low"]) <= tp1:
                    pnl = effective_entry - tp1
                    return {"result": "TP1", "pnl": pnl, "exit_price": tp1}

        last_close = float(future_bars.iloc[-1]["close"])
        if side == "BUY":
            pnl = last_close - (entry + spread_cost / 2)
        else:
            pnl = (entry - spread_cost / 2) - last_close
        return {"result": "TIMEOUT", "pnl": pnl, "exit_price": last_close}

    @staticmethod
    def _to_regime_enum(regime_name: str):
        from backend.app.domain.enums import RegimeType

        key = str(regime_name or "UNKNOWN").upper()
        return RegimeType[key] if key in RegimeType.__members__ else RegimeType.UNKNOWN

    @staticmethod
    def _decision_to_signal(decision, window: pd.DataFrame) -> Optional[Dict]:
        side = getattr(decision, "action", None)
        side_val = str(getattr(side, "value", side or "")).upper()
        if side_val not in {"BUY", "SELL"}:
            return None

        sl = getattr(decision, "stop_loss", None)
        tp = getattr(decision, "take_profit", None)
        if sl is None:
            return None

        entry = float(window.iloc[-1]["close"])
        sl = float(sl)
        if tp is None:
            rr = float(getattr(decision, "risk_reward_ratio", 2.0) or 2.0)
            risk_dist = abs(entry - sl)
            if risk_dist <= 0:
                return None
            tp = entry + (risk_dist * rr) if side_val == "BUY" else entry - (risk_dist * rr)
        tp = float(tp)
        if abs(entry - sl) <= 0 or abs(tp - entry) <= 0:
            return None

        return {
            "symbol": str(getattr(decision, "symbol", "") or ""),
            "side": side_val,
            "model": str(getattr(decision, "strategy_name", "") or "app_factory"),
            "entry_price": entry,
            "sl": sl,
            "tp1": tp,
            "confidence": float(getattr(decision, "confidence", 0.0) or 0.0),
        }

    def run(self, df: pd.DataFrame, lookback: int = 100, hold_bars: int = 50) -> dict:
        from backend.app.domain.enums import Action
        from backend.app.domain.models import SymbolProfile

        app_ctx = _get_app_factory_ctx()
        factory = app_ctx["factory"]
        profile = SymbolProfile(symbol=self.symbol)

        df = time_utils.add_session_features(df)
        df = add_volatility_features(df)
        df = add_structure_features(df)
        df = detect_displacement(df)
        df = add_institutional_features(df)
        df = detect_rsi_divergence(df)
        df = detect_candle_patterns(df)

        regime_cfg = {"trend_threshold": 0.65, "volatility_expansion_threshold": 1.5, "volatility_compression_threshold": 0.5}
        total_signals = 0
        blocked_signals = 0

        for i in range(lookback, len(df) - hold_bars):
            window = df.iloc[i - lookback: i]
            if len(window) < lookback:
                continue

            regime_res = classify_regime(window, regime_cfg)
            regime_name = str(regime_res.get("regime", "UNKNOWN")).upper()
            regime_enum = self._to_regime_enum(regime_name)

            now_ts = window.iloc[-1].get("time", None)
            session = time_utils.assign_session(now_ts) if now_ts is not None else "CLOSED"
            decision = factory.get_decision(
                candles=window,
                profile=profile,
                regime=regime_enum,
                session=session,
                brain_recommendation=None,
            )
            if getattr(decision, "action", Action.HOLD) == Action.HOLD:
                continue

            signal = self._decision_to_signal(decision, window)
            if not signal:
                continue
            total_signals += 1

            if i > lookback:
                current_time = df.iloc[i].get("time", None)
                prev_time = df.iloc[i - 1].get("time", None)
                if current_time is not None and prev_time is not None:
                    try:
                        ct = pd.Timestamp(current_time)
                        pt = pd.Timestamp(prev_time)
                        if ct.date() != pt.date():
                            self.daily_pnl = 0.0
                            self.consecutive_losses = 0
                    except Exception:
                        pass

            if self.consecutive_losses >= 3:
                if self._cooldown_until_bar <= 0:
                    self._cooldown_until_bar = i + 50
                if i < self._cooldown_until_bar:
                    blocked_signals += 1
                    continue
                self.consecutive_losses = 0
                self._cooldown_until_bar = 0

            account_state = {
                "equity": self.equity,
                "daily_pnl": self.daily_pnl,
                "consecutive_losses": self.consecutive_losses,
            }
            market_state = {
                "spread": self.specs.get("spread_points", 30),
                "is_news": False,
                "backtest_mode": True,
            }
            gate = risk_engine.risk_gate(signal, account_state, market_state)
            if not gate.get("allowed", False):
                blocked_signals += 1
                continue

            lot = 0.01
            future = df.iloc[i: i + hold_bars]
            outcome = self._simulate_trade(signal, future)
            pnl_usd = float(outcome["pnl"]) * lot * float(self.specs.get("contract_size", 100))

            self.equity += pnl_usd
            self.daily_pnl += pnl_usd
            if self.equity > self.peak_equity:
                self.peak_equity = self.equity
            dd = ((self.peak_equity - self.equity) / self.peak_equity * 100.0) if self.peak_equity > 0 else 0.0
            self.max_dd = max(self.max_dd, dd)

            if outcome["result"] == "SL":
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0
                self._cooldown_until_bar = 0

            self.trades.append(
                {
                    "bar_index": i,
                    "time": df.iloc[i].get("time", i),
                    "side": signal["side"],
                    "model": signal["model"],
                    "regime": regime_name,
                    "entry": signal["entry_price"],
                    "sl": signal["sl"],
                    "tp1": signal["tp1"],
                    "exit": outcome["exit_price"],
                    "result": outcome["result"],
                    "lot": lot,
                    "pnl_usd": round(pnl_usd, 4),
                    "equity": round(self.equity, 4),
                    "dd_pct": round(dd, 2),
                    "confidence": signal.get("confidence", 0),
                }
            )

        return self.get_metrics(total_signals=total_signals, blocked_signals=blocked_signals)

    def get_metrics(self, total_signals: int = 0, blocked_signals: int = 0) -> dict:
        if not self.trades:
            return {
                "total_signals": int(total_signals),
                "blocked_signals": int(blocked_signals),
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "max_dd": round(self.max_dd, 2),
                "net_pnl": 0.0,
                "final_equity": round(self.equity, 4),
            }

        wins = [t for t in self.trades if t["pnl_usd"] > 0]
        losses = [t for t in self.trades if t["pnl_usd"] <= 0]
        gross_profit = sum(t["pnl_usd"] for t in wins) if wins else 0.0
        gross_loss = abs(sum(t["pnl_usd"] for t in losses)) if losses else 1e-9
        return {
            "total_signals": int(total_signals),
            "blocked_signals": int(blocked_signals),
            "total_trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round((len(wins) / len(self.trades)) * 100.0, 1),
            "profit_factor": round(gross_profit / gross_loss, 2),
            "max_dd": round(self.max_dd, 2),
            "net_pnl": round(sum(t["pnl_usd"] for t in self.trades), 4),
            "final_equity": round(self.equity, 4),
        }


def _run_trader_engine(df: pd.DataFrame, symbol: str, timeframe: str, args) -> dict:
    run_equity = float(getattr(args, "_effective_equity", args.equity))
    engine = BacktestEngine(
        symbol=symbol,
        initial_equity=run_equity,
        timeframe=timeframe,
        strategy_mode=getattr(args, "strategy", "all"),
        brain_params=getattr(args, "_strategy_params", {}),
    )
    return engine.run(df, lookback=args.lookback, hold_bars=args.hold_bars)


def _run_app_engine(df: pd.DataFrame, symbol: str, args) -> dict:
    run_equity = float(getattr(args, "_effective_equity", args.equity))
    engine = AppFactoryBacktestEngine(symbol=symbol, initial_equity=run_equity)
    return engine.run(df, lookback=args.lookback, hold_bars=args.hold_bars)


def _rank_rows_for_run(rows: List[Dict], symbols: List[str]) -> List[Dict]:
    by_key: Dict[tuple, Dict] = {}
    for row in rows:
        key = (row.get("symbol"), row.get("timeframe"))
        if key not in by_key or float(row.get("score", 0.0) or 0.0) > float(by_key[key].get("score", 0.0) or 0.0):
            by_key[key] = row

    deduped = list(by_key.values())
    ranked: List[Dict] = []
    for symbol in symbols:
        cand = [r for r in deduped if r.get("symbol") == symbol]
        cand.sort(key=lambda x: float(x.get("score", -999999.0) or -999999.0), reverse=True)
        for idx, rec in enumerate(cand, start=1):
            row = dict(rec)
            row["rank"] = idx
            ranked.append(row)
    return ranked


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 360D backtest matrix and persist TF overrides")
    parser.add_argument("--days", type=int, default=360, help="Backtest window in days")
    parser.add_argument("--equity", type=float, default=300.0, help="Initial equity")
    parser.add_argument(
        "--strategy",
        choices=[
            "all",
            "momentum",
            "momentum_rider",
            "momentum_scalper_v2",
            "usoil_momentum",
            "rapid_pullback",
            "indicator_confluence",
            "alpha_v7_ict",
        ],
        default="all",
        help="Trader-engine selector profile to run across the matrix.",
    )
    parser.add_argument(
        "--strategy-params-json",
        default="",
        help="JSON object or path to JSON file with brain_params overrides for the selected strategy.",
    )
    parser.add_argument("--lookback", type=int, default=100, help="Engine lookback bars")
    parser.add_argument("--hold-bars", type=int, default=50, help="Engine hold bars")
    parser.add_argument("--min-trades", type=int, default=5, help="Minimum trades for reliable ranking")
    parser.add_argument("--max-bars-per-run", type=int, default=5000, help="Safety cap per run (0 = no cap)")
    parser.add_argument(
        "--optimize-equity-floor",
        type=float,
        default=1000.0,
        help="For strategy discovery, force minimum initial equity (0 = disabled)",
    )
    parser.add_argument("--engine", choices=["trader", "app", "both"], default="trader")
    parser.add_argument("--symbols", default="", help="Override symbols CSV (standard/broker names)")
    parser.add_argument("--timeframes", default="", help="Override timeframes CSV")
    parser.add_argument(
        "--data-source",
        choices=["auto", "mt5", "duckdb"],
        default="auto",
        help="Bar source for backtest: auto(duckdb->mt5), mt5, duckdb",
    )
    parser.add_argument(
        "--duckdb-path",
        default="backend/data/duckdb/analytics.duckdb",
        help="DuckDB path (used when data-source is auto/duckdb)",
    )
    parser.add_argument(
        "--include-shadow",
        dest="include_shadow",
        action="store_true",
        default=True,
        help="Include XAG shadow symbol",
    )
    parser.add_argument(
        "--no-shadow",
        dest="include_shadow",
        action="store_false",
        help="Disable XAG shadow symbol in matrix run",
    )
    parser.add_argument("--prune-days", type=int, default=180, help="Prune override runs older than N days")
    parser.add_argument("--prod-min-trades", type=int, default=3, help="Production gate: minimum trades")
    parser.add_argument("--prod-min-win-rate", type=float, default=35.0, help="Production gate: minimum win rate (%%)")
    parser.add_argument("--prod-min-pf", type=float, default=1.05, help="Production gate: minimum profit factor")
    parser.add_argument("--prod-max-dd", type=float, default=25.0, help="Production gate: maximum drawdown (%%)")
    parser.add_argument("--prod-min-score", type=float, default=0.0, help="Production gate: minimum score")
    parser.add_argument(
        "--prod-require-positive-pnl",
        dest="prod_require_positive_pnl",
        action="store_true",
        default=True,
        help="Production gate: require net_pnl > 0",
    )
    parser.add_argument(
        "--prod-allow-nonpositive-pnl",
        dest="prod_require_positive_pnl",
        action="store_false",
        help="Production gate: allow net_pnl <= 0",
    )
    args = parser.parse_args()
    try:
        args._strategy_params = load_strategy_params_arg(getattr(args, "strategy_params_json", ""), getattr(args, "strategy", "all"))
    except Exception as e:
        print(f"[ERR] invalid --strategy-params-json: {e}")
        return 2
    if args.strategy != "all" and args.engine != "trader":
        print(f"[INFO] strategy={args.strategy} is trader-selector specific -> forcing --engine trader")
        args.engine = "trader"
    requested_equity = float(args.equity)
    equity_floor = float(max(0.0, args.optimize_equity_floor))
    effective_equity = max(requested_equity, equity_floor) if equity_floor > 0 else requested_equity
    args._effective_equity = effective_equity

    symbols = _resolve_symbols(args.symbols, include_shadow=args.include_shadow)
    timeframes = _resolve_timeframes(args.timeframes)
    if not symbols or not timeframes:
        print("No symbols/timeframes to run.")
        return 1

    engines = ["trader", "app"] if args.engine == "both" else [args.engine]
    total_jobs = len(symbols) * len(timeframes) * len(engines)
    job_idx = 0
    run_id = datetime.now(UTC).strftime("bt360_%Y%m%d_%H%M%S_%f")
    duck_conn = None
    if args.data_source in {"auto", "duckdb"}:
        try:
            duck_conn = _open_duckdb(args.duckdb_path)
        except Exception as e:
            if args.data_source == "duckdb":
                print(f"[FATAL] DuckDB unavailable: {e}")
                return 1
            print(f"[WARN] DuckDB unavailable, fallback to MT5: {e}")
            duck_conn = None

    db.upsert_backtest_override_run(
        run_id,
        engine=args.engine,
        days=args.days,
        equity=effective_equity,
        lookback=args.lookback,
        hold_bars=args.hold_bars,
        min_trades=args.min_trades,
        max_bars_per_run=args.max_bars_per_run,
        status="RUNNING",
        notes="matrix_started",
    )

    rows_all: List[Dict] = []
    print("=" * 100)
    print(
        f"Backtest Matrix Start | run_id={run_id} | days={args.days} | symbols={len(symbols)} | "
        f"tfs={len(timeframes)} | engines={','.join(engines)} | strategy={args.strategy}"
    )
    print(f"Data source: {args.data_source}{f' ({Path(args.duckdb_path).resolve()})' if args.data_source in {'auto','duckdb'} else ''}")
    print(
        f"Total jobs: {total_jobs} | requested_equity={requested_equity} | effective_equity={effective_equity} | "
        f"equity_floor={equity_floor} | lookback={args.lookback} | "
        f"hold={args.hold_bars} | min_trades={args.min_trades}"
    )
    print("=" * 100)

    try:
        for symbol in symbols:
            for tf in timeframes:
                try:
                    df = _load_bars(
                        symbol=symbol,
                        timeframe=tf,
                        days=args.days,
                        data_source=args.data_source,
                        duck_conn=duck_conn,
                    )
                except Exception as e:
                    print(f"[SKIP] {symbol} {tf} load error: {e}")
                    continue

                raw_bars = len(df)
                truncated = False
                if args.max_bars_per_run and args.max_bars_per_run > 0 and raw_bars > args.max_bars_per_run:
                    df = df.tail(args.max_bars_per_run).copy()
                    truncated = True

                if len(df) < (args.lookback + args.hold_bars + 20):
                    print(f"[SKIP] {symbol} {tf}: insufficient bars ({len(df)})")
                    continue

                for engine_name in engines:
                    job_idx += 1
                    print(f"\n[{job_idx}/{total_jobs}] {symbol} {tf} engine={engine_name}")
                    try:
                        if engine_name == "trader":
                            metrics = _run_trader_engine(df, symbol, tf, args)
                        else:
                            metrics = _run_app_engine(df, symbol, args)

                        rec = dict(metrics)
                        rec["symbol"] = symbol
                        rec["timeframe"] = tf
                        rec["engine"] = engine_name
                        rec["strategy"] = args.strategy if engine_name == "trader" else "app_factory"
                        rec["bars_used"] = len(df)
                        rec["bars_raw"] = raw_bars
                        rec["truncated"] = truncated
                        rec["score"] = _score(rec, args.min_trades)
                        rows_all.append(rec)
                    except Exception as e:
                        print(f"  [ERROR] {symbol} {tf} {engine_name}: {e}")

        if not rows_all:
            db.complete_backtest_override_run(run_id, status="FAILED", total_rows=0, notes="no_rows_generated")
            print("\nNo backtest rows generated.")
            return 1

        ranked_rows = _rank_rows_for_run(rows_all, symbols=symbols)
        for rec in ranked_rows:
            rec["production_eligible"] = _is_production_candidate(rec, args)
        inserted = db.upsert_backtest_tf_overrides(run_id, ranked_rows, replace_run_rows=True)
        db.complete_backtest_override_run(run_id, status="COMPLETED", total_rows=inserted, notes="ok")
        if args.prune_days > 0:
            db.prune_backtest_override_runs(retention_days=args.prune_days)

    except Exception as e:
        db.complete_backtest_override_run(run_id, status="FAILED", total_rows=0, notes=f"error:{e}")
        raise
    finally:
        if duck_conn is not None:
            try:
                duck_conn.close()
            except Exception:
                pass

    rows_all.sort(key=lambda x: float(x.get("score", -999999.0) or -999999.0), reverse=True)
    best_by_symbol = {}
    for symbol in symbols:
        candidates = [r for r in ranked_rows if r.get("symbol") == symbol]
        eligible = [r for r in candidates if _is_production_candidate(r, args)]
        best = eligible[0] if eligible else (candidates[0] if candidates else None)
        if best:
            best_by_symbol[symbol] = best

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "run_id": run_id,
        "engine": args.engine,
        "strategy": args.strategy,
        "days": args.days,
        "requested_equity": requested_equity,
        "effective_equity": effective_equity,
        "optimize_equity_floor": equity_floor,
        "lookback": args.lookback,
        "hold_bars": args.hold_bars,
        "min_trades": args.min_trades,
        "max_bars_per_run": args.max_bars_per_run,
        "production_filters": {
            "min_trades": int(args.prod_min_trades),
            "min_win_rate": float(args.prod_min_win_rate),
            "min_pf": float(args.prod_min_pf),
            "max_dd": float(args.prod_max_dd),
            "min_score": float(args.prod_min_score),
            "require_positive_pnl": bool(args.prod_require_positive_pnl),
        },
        "timeframes": timeframes,
        "symbols": symbols,
        "best_by_symbol": best_by_symbol,
        "ranked_rows": ranked_rows,
        "rows_all_engines": rows_all,
    }

    out_dir = Path("d:/VibeCode/Trade/tmp")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"backtest_matrix_360_{stamp}.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("\n" + "=" * 100)
    print("BEST BY SYMBOL (TOP-TF CANDIDATES)")
    print("=" * 100)
    for sym, b in best_by_symbol.items():
        print(
            f"{sym:>7} | TF={b.get('timeframe'):>3} | rank={b.get('rank', 0):>2} | engine={b.get('engine','-'):>6} | "
            f"trades={b.get('total_trades', b.get('trades', 0)):>4} | WR={b.get('win_rate',0):>5}% | "
            f"PF={b.get('profit_factor',0):>5} | PnL={b.get('net_pnl',0):>10} | DD={b.get('max_dd',0):>5}% | "
            f"score={b.get('score',0):>8} | PROD={'YES' if _is_production_candidate(b, args) else 'NO'}"
        )

    print("\nTOP 20 OVERALL")
    for r in rows_all[:20]:
        print(
            f"{r['symbol']:>7} {r['timeframe']:>3} [{r.get('engine','-'):>6}] | "
            f"trades={r.get('total_trades',0):>4} | WR={r.get('win_rate',0):>5}% | "
            f"PF={r.get('profit_factor',0):>5} | PnL={r.get('net_pnl',0):>10} | "
            f"DD={r.get('max_dd',0):>5}% | score={r.get('score',0):>8}"
        )

    print(f"\nSaved report: {json_path}")
    print(f"Persisted run_id: {run_id} | ranked_rows={len(ranked_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
