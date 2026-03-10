"""
Pro Tournament Backtest — Multi-Symbol Strategy Tournament.

เป้าหมาย:
    - ดึง historical candles 60 วันจาก MT5 สำหรับ 6 symbols
    - รันทุก strategy ที่ match กับ symbol (ตาม asset_class)
    - จัดอันดับตาม composite score → บันทึก SQLite
    - สร้าง JSON report + พิมพ์ rich table

Usage:
    cd d:\VibeCode\Trade\backend
    python scripts/pro_tournament.py

    # เลือก symbol:
    python scripts/pro_tournament.py --symbols XAUUSDc BTCUSDc

    # กำหนดจำนวนวัน:
    python scripts/pro_tournament.py --days 30

    # เลือก timeframe:
    python scripts/pro_tournament.py --timeframe M15
"""

import sys
import os
import json
import time
import sqlite3
import argparse
import importlib
import traceback
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from pathlib import Path
from math import sqrt

# ─── Ensure backend is importable ───
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.execution.backtester import Backtester, BacktestResult
from app.brain.regime import classify_regime
from app.strategy.templates import _SEED_REGISTRY

logger = get_logger("ProTournament")


# ═════════════════════════════════════════════
# SYMBOL CONFIGURATIONS
# ═════════════════════════════════════════════

SYMBOL_CONFIGS = {
    "XAUUSDc": {
        "contract_size": 100.0,
        "point": 0.01,
        "digits": 2,
        "asset_class": "gold",
        "ghost_protocol": True,
        "volume_min": 0.01,
    },
    "XAGUSDc": {
        "contract_size": 5000.0,
        "point": 0.001,
        "digits": 3,
        "asset_class": "silver",
        "ghost_protocol": True,
        "volume_min": 0.01,
    },
    "BTCUSDc": {
        "contract_size": 1.0,
        "point": 0.01,
        "digits": 2,
        "asset_class": "crypto",
        "ghost_protocol": False,
        "volume_min": 0.01,
    },
    "EURUSDc": {
        "contract_size": 100000.0,
        "point": 0.00001,
        "digits": 5,
        "asset_class": "forex",
        "ghost_protocol": False,
        "volume_min": 0.01,
    },
    "GBPUSDc": {
        "contract_size": 100000.0,
        "point": 0.00001,
        "digits": 5,
        "asset_class": "forex",
        "ghost_protocol": False,
        "volume_min": 0.01,
    },
    "USDJPYc": {
        "contract_size": 100000.0,
        "point": 0.001,
        "digits": 3,
        "asset_class": "forex",
        "ghost_protocol": False,
        "volume_min": 0.01,
    },
}


# ═════════════════════════════════════════════
# DATA CLASS FOR RESULTS
# ═════════════════════════════════════════════

@dataclass
class TournamentEntry:
    """Single strategy result in the tournament."""
    symbol: str
    strategy_name: str
    asset_class: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_profit_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_usd: float = 0.0
    expectancy: float = 0.0
    avg_rr: float = 0.0
    sharpe_ratio: float = 0.0
    avg_bars_held: float = 0.0
    initial_equity: float = 10000.0
    final_equity: float = 10000.0
    composite_score: float = 0.0
    per_regime: dict = field(default_factory=dict)
    duration_seconds: float = 0.0
    live_recommended: bool = False
    rank: int = 0
    error: str = ""


# ═════════════════════════════════════════════
# STRATEGY LOADER
# ═════════════════════════════════════════════

def load_strategy(registry_entry: dict):
    """Dynamically load strategy class from registry entry."""
    module_path = registry_entry["module_path"]
    class_name = registry_entry["class_name"]

    try:
        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        instance = cls()

        # Ensure it has 'name' attribute
        if not hasattr(instance, "name") or not instance.name:
            instance.name = registry_entry["strategy_name"]

        # Auto-wrap SniperProStrategy with adapter (returns SniperSignal, not Decision)
        if class_name == "SniperProStrategy":
            instance = SniperProAdapter(instance)

        return instance
    except Exception as e:
        logger.warning(f"Failed to load {registry_entry['strategy_name']}: {e}")
        return None


def get_matching_strategies(asset_class: str) -> list[dict]:
    """Get all strategies that match the given asset class."""
    matches = []
    for entry in _SEED_REGISTRY:
        entry_class = entry.get("asset_class", "*")
        if entry_class == asset_class or entry_class == "*":
            matches.append(entry)
    return matches


# ═════════════════════════════════════════════
# SNIPER PRO ADAPTER (SniperSignal → Decision)
# ═════════════════════════════════════════════

class SniperProAdapter:
    """Wraps SniperProStrategy to convert SniperSignal → Decision for Backtester."""

    def __init__(self, inner_strategy):
        self._inner = inner_strategy
        self.name = getattr(inner_strategy, "name", "sniper_pro")

    def analyze(self, candles, profile, regime):
        """Convert SniperSignal → Decision."""
        from app.strategy.templates.sniper_pro import Signal as SniperSignalEnum

        # SniperPro expects (df, direction, htf_trend)
        result = self._inner.analyze(candles)

        # Map Signal enum → Action enum
        sig = result.signal
        if sig == SniperSignalEnum.BUY:
            action = Action.BUY
        elif sig == SniperSignalEnum.SELL:
            action = Action.SELL
        else:
            action = Action.HOLD

        return Decision(
            symbol=profile.symbol if profile else result.symbol,
            action=action,
            confidence=result.confidence / 100.0 if result.confidence > 1 else result.confidence,
            reason="; ".join(result.reasons) if result.reasons else "sniper_pro signal",
            stop_loss=result.stop_loss if result.stop_loss else 0.0,
            take_profit=result.take_profit if result.take_profit else 0.0,
        )


# ═════════════════════════════════════════════
# SCORING
# ═════════════════════════════════════════════

def compute_composite_score(result: BacktestResult) -> float:
    """
    Compute composite score for ranking.

    Formula: PF × (WR/100) × (1 - MaxDD/100) × √(trades)
    Penalty if PF < 1.0 or trades < 5
    """
    if result.total_trades < 5:
        return 0.0

    pf = max(result.profit_factor, 0.01)
    wr = result.win_rate / 100.0
    dd_factor = max(1.0 - result.max_drawdown_pct / 100.0, 0.01)
    trade_factor = sqrt(result.total_trades)

    score = pf * wr * dd_factor * trade_factor

    # Bonus for profitable strategies
    if result.total_profit_usd > 0:
        score *= 1.2

    # Penalty for negative PF
    if result.profit_factor < 1.0:
        score *= 0.3

    return round(score, 4)


# ═════════════════════════════════════════════
# MT5 DATA FETCHING
# ═════════════════════════════════════════════

def connect_mt5() -> bool:
    """Connect to MT5 terminal."""
    mt5_path = (os.environ.get("MT5_PATH", "").strip()) or None
    login = os.environ.get("MT5_LOGIN", "").strip() or None
    password = os.environ.get("MT5_PASSWORD", "").strip() or None
    server = os.environ.get("MT5_SERVER", "").strip() or None

    # Strategy: if login is provided, pass full credentials.
    # Otherwise, just connect to the already-open terminal (bare init or path-only).
    if login:
        init_args = {}
        if mt5_path:
            init_args["path"] = mt5_path
        init_args["login"] = int(login)
        if password:
            init_args["password"] = password
        if server:
            init_args["server"] = server
    else:
        # No login → attach to open terminal
        # Try with path first, then bare init
        init_args = {"path": mt5_path} if mt5_path else {}

    print(f"  🔌 MT5 init args: {list(init_args.keys())}")

    if not mt5.initialize(**init_args):
        # If path-only failed, try bare init
        if init_args and not login:
            print("  🔄 Retrying bare mt5.initialize()...")
            if not mt5.initialize():
                err = mt5.last_error()
                print(f"❌ MT5 connection failed: {err}")
                return False
        else:
            err = mt5.last_error()
            print(f"❌ MT5 connection failed: {err}")
            return False

    info = mt5.account_info()
    if info:
        print(f"✅ MT5 Connected: Login={info.login} Server={info.server} "
              f"Balance={info.balance:.2f} Equity={info.equity:.2f}")
    return True


def fetch_candles(symbol: str, timeframe: str, n_candles: int) -> pd.DataFrame | None:
    """Fetch historical candles from MT5."""
    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }

    mt5_tf = tf_map.get(timeframe, mt5.TIMEFRAME_M5)
    rates = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, n_candles)

    if rates is None or len(rates) == 0:
        print(f"  ⚠️  No data for {symbol} ({timeframe})")
        return None

    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")

    # Rename columns for consistency
    if "tick_volume" not in df.columns and "real_volume" in df.columns:
        df["tick_volume"] = df["real_volume"]
    elif "tick_volume" not in df.columns:
        df["tick_volume"] = 0

    return df


# ═════════════════════════════════════════════
# BACKTEST RUNNER
# ═════════════════════════════════════════════

def run_single_backtest(
    strategy,
    candles: pd.DataFrame,
    symbol: str,
    config: dict,
    initial_equity: float = 10000.0,
) -> BacktestResult | None:
    """Run a single backtest for one strategy on one symbol."""
    try:
        from app.risk.cooldown_manager import CooldownManager
        from app.risk.risk_dampener import RiskDampener
        from app.risk.session_guard import SessionGuard

        # ─── Relaxed safety for backtest (more trades = better ranking) ───
        cooldown = CooldownManager(cooldown_minutes=0, max_consecutive_losses=999)
        dampener = RiskDampener()
        guard = SessionGuard(max_trades_per_session=999)

        bt = Backtester(
            strategy=strategy,
            initial_equity=initial_equity,
            risk_per_trade=0.02,
            commission_per_lot=0.0,
            slippage_points=config["point"] * 2,
            warmup_bars=60,
            cooldown_mgr=cooldown,
            risk_dampener=dampener,
            session_guard=guard,
            max_trades_per_session=999,
        )

        result = bt.run(
            candles=candles,
            symbol=symbol,
            contract_size=config["contract_size"],
            point=config["point"],
        )

        return result

    except Exception as e:
        logger.error(f"Backtest failed for {strategy.name} on {symbol}: {e}")
        return None


# ═════════════════════════════════════════════
# SQLITE SAVE
# ═════════════════════════════════════════════

def save_to_sqlite(entries: list[TournamentEntry], db_path: str):
    """Save tournament results to SQLite."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Create tables if not exist
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tournament_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            asset_class TEXT,
            total_trades INTEGER,
            winning_trades INTEGER,
            losing_trades INTEGER,
            win_rate REAL,
            profit_factor REAL,
            total_profit_usd REAL,
            max_drawdown_pct REAL,
            max_drawdown_usd REAL,
            expectancy REAL,
            avg_rr REAL,
            sharpe_ratio REAL,
            avg_bars_held REAL,
            initial_equity REAL,
            final_equity REAL,
            composite_score REAL,
            per_regime TEXT,
            duration_seconds REAL,
            live_recommended INTEGER,
            rank INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS optimized_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            win_rate REAL,
            profit_factor REAL,
            composite_score REAL,
            sl_mult REAL DEFAULT 1.0,
            tp_mult REAL DEFAULT 2.0,
            risk_pct REAL DEFAULT 0.02,
            session_filter TEXT DEFAULT 'ALL',
            ghost_protocol INTEGER DEFAULT 0,
            regime TEXT DEFAULT 'ALL',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, strategy_name, regime)
        )
    """)

    # Insert tournament results
    ts = datetime.now(timezone.utc).isoformat()
    for e in entries:
        cur.execute("""
            INSERT INTO tournament_results
            (symbol, strategy_name, asset_class, total_trades, winning_trades,
             losing_trades, win_rate, profit_factor, total_profit_usd,
             max_drawdown_pct, max_drawdown_usd, expectancy, avg_rr,
             sharpe_ratio, avg_bars_held, initial_equity, final_equity,
             composite_score, per_regime, duration_seconds, live_recommended,
             rank, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            e.symbol, e.strategy_name, e.asset_class,
            e.total_trades, e.winning_trades, e.losing_trades,
            e.win_rate, e.profit_factor, e.total_profit_usd,
            e.max_drawdown_pct, e.max_drawdown_usd, e.expectancy,
            e.avg_rr, e.sharpe_ratio, e.avg_bars_held,
            e.initial_equity, e.final_equity, e.composite_score,
            json.dumps(e.per_regime), e.duration_seconds,
            1 if e.live_recommended else 0, e.rank, ts,
        ))

    # Update backtest_routing with best strategy per (symbol, regime)
    # Drop old table to handle schema changes
    cur.execute("DROP TABLE IF EXISTS backtest_routing")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS backtest_routing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            regime TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            score REAL,
            win_rate REAL,
            profit_factor REAL,
            total_trades INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, regime)
        )
    """)

    # Build best per (symbol, regime) from per_regime data
    symbol_regime_best: dict[str, dict[str, TournamentEntry]] = {}
    for e in entries:
        if e.total_trades < 5 or e.profit_factor < 0.5:
            continue
        sym = e.symbol
        if sym not in symbol_regime_best:
            symbol_regime_best[sym] = {}

        # Overall best
        if "ALL" not in symbol_regime_best[sym]:
            symbol_regime_best[sym]["ALL"] = e
        elif e.composite_score > symbol_regime_best[sym]["ALL"].composite_score:
            symbol_regime_best[sym]["ALL"] = e

        # Per-regime best
        for regime_name, regime_stats in e.per_regime.items():
            regime_trades = regime_stats.get("trades", 0)
            regime_wr = regime_stats.get("win_rate", 0)
            if regime_trades < 3:
                continue

            regime_score = regime_wr * regime_trades
            if regime_name not in symbol_regime_best[sym]:
                symbol_regime_best[sym][regime_name] = e
            else:
                existing = symbol_regime_best[sym][regime_name]
                existing_regime = existing.per_regime.get(regime_name, {})
                existing_score = existing_regime.get("win_rate", 0) * existing_regime.get("trades", 0)
                if regime_score > existing_score:
                    symbol_regime_best[sym][regime_name] = e

    for sym, regimes in symbol_regime_best.items():
        for regime, best_entry in regimes.items():
            cur.execute("""
                INSERT OR REPLACE INTO backtest_routing
                (symbol, regime, strategy_name, score, win_rate, profit_factor, total_trades, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                sym, regime, best_entry.strategy_name,
                best_entry.composite_score, best_entry.win_rate,
                best_entry.profit_factor, best_entry.total_trades, ts,
            ))

    # Save optimized_settings for live-recommended
    for e in entries:
        if not e.live_recommended:
            continue
        config = SYMBOL_CONFIGS.get(e.symbol, {})
        cur.execute("""
            INSERT OR REPLACE INTO optimized_settings
            (symbol, strategy_name, win_rate, profit_factor, composite_score,
             risk_pct, ghost_protocol, regime, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            e.symbol, e.strategy_name, e.win_rate, e.profit_factor,
            e.composite_score, 0.02,
            1 if config.get("ghost_protocol", False) else 0,
            "ALL", ts,
        ))

    conn.commit()
    conn.close()
    print(f"\n💾 Results saved to SQLite: {db_path}")


# ═════════════════════════════════════════════
# TOURNAMENT RUNNER
# ═════════════════════════════════════════════

def run_tournament(
    symbols: list[str],
    timeframe: str = "M5",
    days: int = 60,
    initial_equity: float = 10000.0,
    db_path: str = "data/sqlite/trading.db",
) -> dict[str, list[TournamentEntry]]:
    """
    Run full multi-symbol tournament backtest.

    Returns:
        dict: {symbol: [TournamentEntry, ...]} sorted by score DESC
    """
    # Calculate number of candles needed
    tf_bars_per_day = {
        "M1": 1440, "M5": 288, "M15": 96,
        "M30": 48, "H1": 24, "H4": 6, "D1": 1,
    }
    bars_per_day = tf_bars_per_day.get(timeframe, 288)
    n_candles = days * bars_per_day

    print("=" * 70)
    print("🏆 PRO TOURNAMENT BACKTEST — ANTIGRAVITY AI")
    print("=" * 70)
    print(f"📊 Symbols:    {', '.join(symbols)}")
    print(f"📅 Period:     {days} days (~{n_candles:,} candles per symbol)")
    print(f"⏱️  Timeframe:  {timeframe}")
    print(f"💰 Equity:     ${initial_equity:,.2f}")
    print(f"📁 DB:         {db_path}")
    print("=" * 70)

    all_results: dict[str, list[TournamentEntry]] = {}
    all_entries: list[TournamentEntry] = []

    for symbol in symbols:
        config = SYMBOL_CONFIGS.get(symbol)
        if not config:
            print(f"\n⚠️  Unknown symbol: {symbol} — skipping")
            continue

        print(f"\n{'─' * 60}")
        print(f"🔍 {symbol} ({config['asset_class'].upper()})")
        print(f"{'─' * 60}")

        # ─── Fetch candles ───
        print(f"  📥 Fetching {n_candles:,} candles...")
        candles = fetch_candles(symbol, timeframe, n_candles)
        if candles is None or len(candles) < 100:
            print(f"  ❌ Not enough data for {symbol}")
            continue

        print(f"  ✅ Got {len(candles):,} candles "
              f"({candles['time'].iloc[0]} → {candles['time'].iloc[-1]})")

        # ─── Get matching strategies ───
        strategies = get_matching_strategies(config["asset_class"])
        print(f"  🎯 Matching strategies: {len(strategies)}")

        symbol_entries: list[TournamentEntry] = []

        for reg_entry in strategies:
            strat_name = reg_entry["strategy_name"]
            print(f"    ⚡ {strat_name}...", end=" ", flush=True)

            # Load strategy
            strategy = load_strategy(reg_entry)
            if strategy is None:
                print("❌ LOAD FAIL")
                symbol_entries.append(TournamentEntry(
                    symbol=symbol,
                    strategy_name=strat_name,
                    asset_class=config["asset_class"],
                    error="Failed to load strategy",
                ))
                continue

            # Run backtest
            start_t = time.monotonic()
            result = run_single_backtest(
                strategy, candles, symbol, config, initial_equity
            )
            elapsed = time.monotonic() - start_t

            if result is None:
                print(f"❌ BACKTEST FAIL ({elapsed:.1f}s)")
                symbol_entries.append(TournamentEntry(
                    symbol=symbol,
                    strategy_name=strat_name,
                    asset_class=config["asset_class"],
                    error="Backtest execution failed",
                ))
                continue

            # Compute composite score
            score = compute_composite_score(result)
            is_recommended = (
                result.profit_factor >= 1.0
                and result.total_trades >= 5
                and result.max_drawdown_pct <= 30.0
                and result.total_profit_usd > 0
            )

            entry = TournamentEntry(
                symbol=symbol,
                strategy_name=strat_name,
                asset_class=config["asset_class"],
                total_trades=result.total_trades,
                winning_trades=result.winning_trades,
                losing_trades=result.losing_trades,
                win_rate=result.win_rate,
                profit_factor=result.profit_factor,
                total_profit_usd=result.total_profit_usd,
                max_drawdown_pct=result.max_drawdown_pct,
                max_drawdown_usd=result.max_drawdown_usd,
                expectancy=result.expectancy,
                avg_rr=result.avg_rr,
                sharpe_ratio=result.sharpe_ratio,
                avg_bars_held=result.avg_bars_held,
                initial_equity=result.initial_equity,
                final_equity=result.final_equity,
                composite_score=score,
                per_regime=result.per_regime,
                duration_seconds=elapsed,
                live_recommended=is_recommended,
            )

            symbol_entries.append(entry)

            # Status emoji
            if is_recommended:
                emoji = "🟢"
            elif result.total_trades >= 5:
                emoji = "🟡"
            else:
                emoji = "⚪"

            print(f"{emoji} Trades={result.total_trades:3d} "
                  f"WR={result.win_rate:5.1f}% "
                  f"PF={result.profit_factor:5.2f} "
                  f"P&L=${result.total_profit_usd:+8.2f} "
                  f"DD={result.max_drawdown_pct:5.1f}% "
                  f"Score={score:7.2f} "
                  f"({elapsed:.1f}s)")

        # Sort by composite score
        symbol_entries.sort(key=lambda x: x.composite_score, reverse=True)

        # Assign ranks
        for i, e in enumerate(symbol_entries, 1):
            e.rank = i

        all_results[symbol] = symbol_entries
        all_entries.extend(symbol_entries)

        # Print leaderboard
        _print_leaderboard(symbol, symbol_entries)

    # ─── Save to SQLite ───
    if all_entries:
        save_to_sqlite(all_entries, db_path)

    # ─── Print grand summary ───
    _print_grand_summary(all_results)

    # ─── Save JSON report ───
    _save_json_report(all_results)

    return all_results


# ═════════════════════════════════════════════
# DISPLAY
# ═════════════════════════════════════════════

def _print_leaderboard(symbol: str, entries: list[TournamentEntry]):
    """Print formatted leaderboard for one symbol."""
    print(f"\n  📋 LEADERBOARD — {symbol}")
    print(f"  {'Rank':<5} {'Strategy':<28} {'Trades':>7} {'WR%':>7} "
          f"{'PF':>7} {'P&L $':>10} {'MaxDD%':>8} {'Score':>8} {'Live?':>6}")
    print(f"  {'─' * 94}")

    for e in entries[:10]:
        live = "✅" if e.live_recommended else "❌"
        if e.error:
            print(f"  {e.rank:<5} {e.strategy_name:<28} {'ERROR':>7} "
                  f"{'—':>7} {'—':>7} {'—':>10} {'—':>8} {'—':>8} {'—':>6}")
        else:
            print(f"  {e.rank:<5} {e.strategy_name:<28} "
                  f"{e.total_trades:>7} {e.win_rate:>6.1f}% "
                  f"{e.profit_factor:>7.2f} {e.total_profit_usd:>+10.2f} "
                  f"{e.max_drawdown_pct:>7.1f}% {e.composite_score:>8.2f} "
                  f"{live:>6}")


def _print_grand_summary(all_results: dict[str, list[TournamentEntry]]):
    """Print grand summary across all symbols."""
    print("\n" + "=" * 70)
    print("🏆 GRAND SUMMARY — BEST STRATEGY PER SYMBOL")
    print("=" * 70)

    recommended = []

    for symbol, entries in all_results.items():
        best = next((e for e in entries if e.live_recommended), None)
        if best is None and entries:
            best = entries[0]

        if best and not best.error:
            live = "🟢 LIVE-READY" if best.live_recommended else "🟡 REVIEW"
            print(f"\n  {symbol}:")
            print(f"    Strategy:  {best.strategy_name}")
            print(f"    Trades:    {best.total_trades}")
            print(f"    Win Rate:  {best.win_rate:.1f}%")
            print(f"    PF:        {best.profit_factor:.2f}")
            print(f"    P&L:       ${best.total_profit_usd:+.2f}")
            print(f"    Max DD:    {best.max_drawdown_pct:.1f}%")
            print(f"    Sharpe:    {best.sharpe_ratio:.2f}")
            print(f"    Score:     {best.composite_score:.2f}")
            print(f"    Status:    {live}")

            if best.live_recommended:
                config = SYMBOL_CONFIGS.get(symbol, {})
                recommended.append({
                    "symbol": symbol,
                    "strategy": best.strategy_name,
                    "win_rate": best.win_rate,
                    "profit_factor": best.profit_factor,
                    "max_drawdown_pct": best.max_drawdown_pct,
                    "composite_score": best.composite_score,
                    "ghost_protocol": config.get("ghost_protocol", False),
                })
        else:
            print(f"\n  {symbol}: ❌ No viable strategy found")

    # ─── Live-Ready Summary ───
    if recommended:
        print(f"\n{'=' * 70}")
        print(f"🚀 LIVE-READY RECOMMENDATIONS ({len(recommended)} symbols)")
        print(f"{'=' * 70}")
        for r in recommended:
            ghost = " 👻 Ghost Protocol" if r["ghost_protocol"] else ""
            print(f"  {r['symbol']:<12} → {r['strategy']:<25} "
                  f"WR={r['win_rate']:.1f}% PF={r['profit_factor']:.2f} "
                  f"Score={r['composite_score']:.2f}{ghost}")

    print(f"\n{'=' * 70}")
    print("✅ Tournament complete!")
    print(f"{'=' * 70}")


def _save_json_report(all_results: dict[str, list[TournamentEntry]]):
    """Save JSON report to disk."""
    report_dir = Path("reports")
    report_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"tournament_{ts}.json"

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbols": {},
    }

    for symbol, entries in all_results.items():
        report["symbols"][symbol] = {
            "total_strategies_tested": len(entries),
            "live_recommended": sum(1 for e in entries if e.live_recommended),
            "best": None,
            "strategies": [],
        }

        for e in entries:
            entry_dict = {
                "rank": e.rank,
                "strategy_name": e.strategy_name,
                "total_trades": e.total_trades,
                "win_rate": e.win_rate,
                "profit_factor": e.profit_factor,
                "total_profit_usd": e.total_profit_usd,
                "max_drawdown_pct": e.max_drawdown_pct,
                "expectancy": e.expectancy,
                "avg_rr": e.avg_rr,
                "sharpe_ratio": e.sharpe_ratio,
                "composite_score": e.composite_score,
                "live_recommended": e.live_recommended,
                "per_regime": e.per_regime,
                "error": e.error,
            }
            report["symbols"][symbol]["strategies"].append(entry_dict)

            if e.rank == 1 and not e.error:
                report["symbols"][symbol]["best"] = {
                    "strategy": e.strategy_name,
                    "win_rate": e.win_rate,
                    "profit_factor": e.profit_factor,
                    "composite_score": e.composite_score,
                }

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)

    print(f"📄 JSON report saved: {report_path}")


# ═════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Pro Tournament Backtest")
    parser.add_argument(
        "--symbols", nargs="+",
        default=list(SYMBOL_CONFIGS.keys()),
        help="Symbols to test (default: all 6)",
    )
    parser.add_argument(
        "--days", type=int, default=60,
        help="Number of days of history (default: 60)",
    )
    parser.add_argument(
        "--timeframe", default="M5",
        choices=["M1", "M5", "M15", "M30", "H1"],
        help="Timeframe (default: M5)",
    )
    parser.add_argument(
        "--equity", type=float, default=10000.0,
        help="Initial equity (default: $10,000)",
    )
    parser.add_argument(
        "--db", default="data/sqlite/trading.db",
        help="SQLite database path",
    )
    args = parser.parse_args()

    # Load .env
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    load_dotenv(env_path)

    # Connect MT5
    if not connect_mt5():
        print("\n❌ Cannot proceed without MT5 connection.")
        print("   Make sure MetaTrader 5 is running and .env has correct credentials.")
        sys.exit(1)

    try:
        run_tournament(
            symbols=args.symbols,
            timeframe=args.timeframe,
            days=args.days,
            initial_equity=args.equity,
            db_path=args.db,
        )
    finally:
        mt5.shutdown()
        print("\n🔌 MT5 disconnected.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⛔ Tournament cancelled by user.")
    except Exception:
        traceback.print_exc()
