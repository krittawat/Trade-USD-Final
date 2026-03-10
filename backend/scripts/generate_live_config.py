"""
Generate Live Config — อ่านผล tournament จาก SQLite แล้วสร้าง YAML config.

สร้าง config สำหรับเทรดจริง:
    - Best strategy per symbol × regime
    - Risk parameters ตามผล backtest
    - Ghost Protocol flags
    - Session filter

Usage:
    cd d:\VibeCode\Trade\backend
    python scripts/generate_live_config.py

    # Output to custom path:
    python scripts/generate_live_config.py --output config/my_config.yaml
"""

import sys
import sqlite3
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ═══════════════════════════════════════
# SYMBOL DEFAULTS
# ═══════════════════════════════════════

SYMBOL_DEFAULTS = {
    "XAUUSDc": {
        "risk_per_trade": 0.015,
        "max_daily_loss": 0.06,
        "ghost_protocol": True,
        "session_filter": ["LONDON", "NY_MAIN", "OVERLAP"],
        "contract_size": 100.0,
    },
    "XAGUSDc": {
        "risk_per_trade": 0.015,
        "max_daily_loss": 0.06,
        "ghost_protocol": True,
        "session_filter": ["LONDON", "NY_MAIN"],
        "contract_size": 5000.0,
    },
    "BTCUSDc": {
        "risk_per_trade": 0.01,
        "max_daily_loss": 0.06,
        "ghost_protocol": False,
        "session_filter": ["ALL"],
        "contract_size": 1.0,
    },
    "EURUSDc": {
        "risk_per_trade": 0.02,
        "max_daily_loss": 0.06,
        "ghost_protocol": False,
        "session_filter": ["LONDON", "NY_MAIN", "OVERLAP"],
        "contract_size": 100000.0,
    },
    "GBPUSDc": {
        "risk_per_trade": 0.02,
        "max_daily_loss": 0.06,
        "ghost_protocol": False,
        "session_filter": ["LONDON", "NY_MAIN"],
        "contract_size": 100000.0,
    },
    "USDJPYc": {
        "risk_per_trade": 0.02,
        "max_daily_loss": 0.06,
        "ghost_protocol": False,
        "session_filter": ["TOKYO", "LONDON", "NY_MAIN"],
        "contract_size": 100000.0,
    },
}


def load_tournament_results(db_path: str) -> dict:
    """Load latest tournament results from SQLite."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    results = {}

    # Get best overall per symbol (from latest tournament)
    try:
        rows = cur.execute("""
            SELECT symbol, strategy_name, win_rate, profit_factor,
                   total_profit_usd, max_drawdown_pct, composite_score,
                   total_trades, sharpe_ratio, expectancy, per_regime
            FROM tournament_results
            WHERE live_recommended = 1
            AND created_at = (
                SELECT MAX(created_at) FROM tournament_results
            )
            ORDER BY composite_score DESC
        """).fetchall()

        for row in rows:
            sym = row["symbol"]
            if sym not in results:
                results[sym] = {
                    "best_overall": dict(row),
                    "regime_routing": {},
                }
                # Parse per_regime JSON
                try:
                    per_regime = json.loads(row["per_regime"]) if row["per_regime"] else {}
                    results[sym]["per_regime_stats"] = per_regime
                except (json.JSONDecodeError, TypeError):
                    results[sym]["per_regime_stats"] = {}
    except sqlite3.OperationalError:
        print("⚠️  tournament_results table not found")

    # Get routing table
    try:
        cols = [r["name"] for r in cur.execute("PRAGMA table_info(backtest_routing)").fetchall()]
        strategy_col = "strategy_name" if "strategy_name" in cols else "strategy"
        rows = cur.execute("""
            SELECT symbol, regime, """ + strategy_col + """ AS strategy_name, score, win_rate, profit_factor
            FROM backtest_routing
            ORDER BY symbol, score DESC
        """).fetchall()

        for row in rows:
            sym = row["symbol"]
            if sym not in results:
                results[sym] = {"best_overall": None, "regime_routing": {}}
            results[sym]["regime_routing"][row["regime"]] = {
                "strategy": row["strategy_name"],
                "score": row["score"],
                "win_rate": row["win_rate"],
                "profit_factor": row["profit_factor"],
            }
    except sqlite3.OperationalError:
        print("⚠️  backtest_routing table not found")

    conn.close()
    return results


def generate_yaml(results: dict, output_path: str):
    """Generate YAML config from tournament results."""
    lines = [
        "# ═══════════════════════════════════════════════════════════════",
        "# ANTIGRAVITY AI — Live Trading Configuration",
        f"# Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        "# Source: Tournament Backtest Results",
        "# ═══════════════════════════════════════════════════════════════",
        "",
        "mode: DRY_RUN  # Change to LIVE when ready",
        "",
        "# Global Risk Settings",
        "risk:",
        "  max_risk_per_trade_pct: 2.0",
        "  max_daily_loss_pct: 6.0",
        "  capital_floor_pct: 90.0",
        "  floating_dd_block_pct: 10.0",
        "  breakeven_r_multiple: 1.0",
        "  news_block_minutes: 30",
        "",
        "# Symbol Configuration",
        "symbols:",
    ]

    for symbol, defaults in SYMBOL_DEFAULTS.items():
        data = results.get(symbol, {})
        best = data.get("best_overall")
        routing = data.get("regime_routing", {})

        # Default strategy
        if best:
            default_strat = best["strategy_name"]
        elif routing.get("ALL"):
            default_strat = routing["ALL"]["strategy"]
        else:
            default_strat = "gold_elite" if "XAU" in symbol else "forex_precision"

        lines.append(f"")
        lines.append(f"  {symbol}:")
        lines.append(f"    enabled: true")
        lines.append(f"    default_strategy: {default_strat}")
        lines.append(f"    contract_size: {defaults['contract_size']}")
        lines.append(f"    risk_per_trade: {defaults['risk_per_trade']}")
        lines.append(f"    max_daily_loss: {defaults['max_daily_loss']}")
        lines.append(f"    ghost_protocol: {str(defaults['ghost_protocol']).lower()}")

        # Session filter
        sessions = defaults["session_filter"]
        lines.append(f"    session_filter: [{', '.join(sessions)}]")

        # Regime routing
        if routing:
            lines.append(f"    regime_routing:")
            for regime, info in routing.items():
                if regime == "ALL":
                    continue
                lines.append(f"      {regime}: {info['strategy']}  "
                           f"# WR={info.get('win_rate', 0):.1f}% "
                           f"PF={info.get('profit_factor', 0):.2f}")

        # Backtest metrics
        if best:
            lines.append(f"    backtest_metrics:")
            lines.append(f"      win_rate: {best['win_rate']:.1f}")
            lines.append(f"      profit_factor: {best['profit_factor']:.2f}")
            lines.append(f"      max_drawdown: {best['max_drawdown_pct']:.1f}")
            lines.append(f"      total_trades: {best['total_trades']}")
            lines.append(f"      sharpe_ratio: {best.get('sharpe_ratio', 0):.2f}")
            lines.append(f"      expectancy: {best.get('expectancy', 0):.2f}")
            lines.append(f"      composite_score: {best['composite_score']:.2f}")
            lines.append(f"      total_profit_usd: {best['total_profit_usd']:.2f}")

    lines.append("")
    lines.append("# End of configuration")

    # Write file
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"✅ Live config generated: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate Live Trading Config")
    parser.add_argument(
        "--db", default="data/sqlite/trading.db",
        help="SQLite database path",
    )
    parser.add_argument(
        "--output", default="data/live_config.yaml",
        help="Output YAML path",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("📝 GENERATE LIVE CONFIG — ANTIGRAVITY AI")
    print("=" * 60)

    results = load_tournament_results(args.db)

    if not results:
        print("\n⚠️  No tournament results found in database.")
        print("   Run the tournament first:")
        print("   python scripts/pro_tournament.py")
        print("\n   Generating config with defaults only...")

    generate_yaml(results, args.output)

    # Print summary
    print(f"\n📊 Summary:")
    for sym in SYMBOL_DEFAULTS:
        data = results.get(sym, {})
        best = data.get("best_overall")
        if best:
            print(f"  {sym:<12} → {best['strategy_name']:<25} "
                  f"WR={best['win_rate']:.1f}% PF={best['profit_factor']:.2f} "
                  f"Score={best['composite_score']:.2f}")
        else:
            print(f"  {sym:<12} → (default — no tournament data)")

    print(f"\n💡 To use this config:")
    print(f"   1. Review: type {args.output}")
    print(f"   2. Switch mode to DRY_RUN for testing")
    print(f"   3. Switch mode to LIVE when confident")


if __name__ == "__main__":
    main()
