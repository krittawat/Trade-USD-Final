"""
OPUS Live Gate — ตรวจสอบผลลัพธ์ Backtest ก่อนอนุญาตเปิด LIVE
เกณฑ์ผ่าน (จาก GEMINI.md):
  - Win Rate >= 50%
  - Profit Factor >= 1.3
  - Max Drawdown <= 6%

Usage:
  python trader/scripts/live_gate.py --backtest-result path/to/backtest_result.json
  หรือ
  python trader/scripts/live_gate.py --run-fresh --symbol XAUUSD --bars 5000
"""
import sys
sys.path.insert(0, "d:/VibeCode/Trade")

import argparse
import json
from datetime import datetime
from pathlib import Path

# ─── Live Gate Thresholds ─────────────────────────────────
GATE_CRITERIA = {
    "min_win_rate": 50.0,
    "min_profit_factor": 1.3,
    "max_drawdown": 6.0,
    "min_trades": 5,  # Selective strategy: quality over quantity
}

PASS = "✅"
FAIL = "❌"

def evaluate_gate(metrics: dict) -> dict:
    """
    Evaluate backtest metrics against live gate criteria.
    Returns: {approved: bool, checks: list}
    """
    checks = []
    all_ok = True

    # 1. Minimum trades
    total = metrics.get("total_trades", 0)
    ok = total >= GATE_CRITERIA["min_trades"]
    checks.append({"check": "Minimum Trades", "value": total, "threshold": GATE_CRITERIA["min_trades"], "pass": ok})
    if not ok: all_ok = False

    # 2. Win Rate
    wr = metrics.get("win_rate", 0)
    ok = wr >= GATE_CRITERIA["min_win_rate"]
    checks.append({"check": "Win Rate (%)", "value": wr, "threshold": GATE_CRITERIA["min_win_rate"], "pass": ok})
    if not ok: all_ok = False

    # 3. Profit Factor
    pf = metrics.get("profit_factor", 0)
    ok = pf >= GATE_CRITERIA["min_profit_factor"]
    checks.append({"check": "Profit Factor", "value": pf, "threshold": GATE_CRITERIA["min_profit_factor"], "pass": ok})
    if not ok: all_ok = False

    # 4. Max Drawdown
    dd = metrics.get("max_dd", 100)
    ok = dd <= GATE_CRITERIA["max_drawdown"]
    checks.append({"check": "Max Drawdown (%)", "value": dd, "threshold": GATE_CRITERIA["max_drawdown"], "pass": ok})
    if not ok: all_ok = False

    return {"approved": all_ok, "checks": checks, "metrics": metrics}


def print_gate_result(result: dict):
    print(f"\n{'='*60}")
    print(f"  OPUS LIVE GATE — Production Readiness")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    for c in result["checks"]:
        icon = PASS if c["pass"] else FAIL
        direction = ">=" if c["check"] != "Max Drawdown (%)" else "<="
        print(f"  {icon}  {c['check']}: {c['value']} (required {direction} {c['threshold']})")

    m = result["metrics"]
    print(f"\n  📊 Summary:")
    print(f"     Total Trades  : {m.get('total_trades', 0)}")
    print(f"     Net P&L       : ${m.get('net_pnl', 0)}")
    print(f"     Final Equity  : ${m.get('final_equity', 0)}")

    print(f"\n{'='*60}")
    if result["approved"]:
        print(f"  🟢 APPROVED — System is cleared for LIVE trading")
        print(f"  ⚠️  Start with minimum lot (0.01) for smoke test")
    else:
        print(f"  🔴 DENIED — Fix strategy/parameters before going LIVE")
        print(f"  💡 Optimize regime thresholds or liquidity detection")
    print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="OPUS Live Gate")
    parser.add_argument("--backtest-result", help="Path to backtest JSON result file")
    parser.add_argument("--run-fresh", action="store_true", help="Run fresh backtest then evaluate")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--bars", type=int, default=5000)
    parser.add_argument("--equity", type=float, default=1000.0)
    args = parser.parse_args()

    if args.run_fresh:
        # Import and run backtest inline
        from trader.scripts.run_backtest import BacktestEngine, load_mt5_data
        df = load_mt5_data(args.symbol, args.bars)
        engine = BacktestEngine(symbol=args.symbol, initial_equity=args.equity)
        metrics = engine.run(df)
    elif args.backtest_result:
        with open(args.backtest_result) as f:
            metrics = json.load(f)
    else:
        print("❌ Provide --backtest-result <path> or --run-fresh")
        sys.exit(1)

    result = evaluate_gate(metrics)
    print_gate_result(result)

    # Save gate decision
    gate_path = f"d:/VibeCode/Trade/trader/data/live_gate_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(gate_path, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\n  💾 Gate result saved to {gate_path}")

    sys.exit(0 if result["approved"] else 1)


if __name__ == "__main__":
    main()
