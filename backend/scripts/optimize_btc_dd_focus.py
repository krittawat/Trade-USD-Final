"""
BTCUSDm DD-Focused Optimizer — Optuna Machine Learning
=======================================================
Target: Maximize profitability while keeping Max DD ≤ 6%

Objective function:
- HARD REJECT: DD > 8% → score = -9999
- HEAVY PENALTY: DD > 6% → linear penalty
- BONUS: DD ≤ 4% → bonus points
- Profit Factor weight: high
- Win Rate weight: moderate
- Trade count filter: ≥ 10 trades for statistical significance

Search Space:
- smc_pivot: 3–20 (SMC structure lookback)
- ssl_baseline: 20–200 (HMA baseline period)
- tp_mult: 0.8–3.0 (Take Profit multiplier)
- mss_displacement: 0.0–2.5 (ATR displacement filter)
- adx_threshold: 12–35 (Trend strength gate)
- risk_per_trade: 0.005–0.02 (Position sizing risk %)
- max_dd_limit: 0.04–0.08 (Backtester DD circuit breaker)
"""

import sys
import time
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import optuna

# Setup paths
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import MetaTrader5 as mt5
from app.execution.backtester import Backtester
from app.strategy.templates.alpha_v6_smc import AlphaV6SMCStrategy
from app.core.logging import get_logger

# Mute heavy logging during optimization
import logging
for log_name in [
    "app.core.logging", "app.risk", "app.risk.sizing",
    "app.risk.cooldown_manager", "app.risk.risk_dampener",
    "app.risk.session_guard", "app.risk.regime_filter",
    "FullBacktest", "Backtester", "__main__",
    "app.strategy.templates.alpha_v6_smc",
]:
    logging.getLogger(log_name).setLevel(logging.CRITICAL)

logger = get_logger("BTC_Optimizer")


def fetch_data(symbol: str, tf, days: int) -> pd.DataFrame | None:
    """Fetch OHLCV data from MT5."""
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, tf, utc_from, utc_to)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df


class BTCDDObjective:
    """
    Multi-objective scoring function for BTC optimization.
    
    Priority: Capital Preservation > Consistency > Profit
    
    Score composition:
    1. Drawdown Score (40% weight) — penalizes DD > 6%, hard reject > 8%
    2. Profit Factor Score (30% weight) — rewards PF > 1.3
    3. Win Rate Score (20% weight) — rewards WR > 45%
    4. Trade Efficiency Score (10% weight) — bonus for good avg RR
    """

    def __init__(self, symbol: str, df: pd.DataFrame, initial_equity: float):
        self.symbol = symbol
        self.df = df
        self.initial_equity = initial_equity

    def __call__(self, trial: optuna.Trial) -> float:
        # ═══ Hyperparameter Search Space ═══
        smc_pivot = trial.suggest_int("smc_pivot", 3, 20)
        ssl_baseline = trial.suggest_int("ssl_baseline", 20, 200, step=10)
        tp_mult = trial.suggest_float("tp_mult", 0.8, 3.0, step=0.1)
        mss_displacement = trial.suggest_float("mss_displacement", 0.0, 2.5, step=0.1)
        adx_threshold = trial.suggest_int("adx_threshold", 12, 35)
        risk_per_trade = trial.suggest_float("risk_per_trade", 0.005, 0.02, step=0.001)
        max_dd_limit = trial.suggest_float("max_dd_limit", 0.04, 0.08, step=0.005)

        # ═══ Initialize Strategy ═══
        strategy = AlphaV6SMCStrategy(
            symbol=self.symbol,
            smc_pivot=smc_pivot,
            ssl_baseline=ssl_baseline,
            tp_mult=tp_mult,
            mss_displacement=mss_displacement,
            adx_threshold=adx_threshold,
        )

        bt = Backtester(
            strategy,
            initial_equity=self.initial_equity,
            risk_per_trade=risk_per_trade,
            max_dd_limit=max_dd_limit,
        )

        # ═══ Run Backtest ═══
        try:
            res = bt.run(self.df, self.symbol, contract_size=1.0, point=0.01)
        except Exception:
            return -9999.0  # Penalty for crash

        # ═══ Filters ═══
        # 1. Minimum trades for statistical significance
        if res.total_trades < 10:
            return -100.0

        # 2. HARD REJECT: DD > 8% is unacceptable
        if res.max_drawdown_pct > 8.0:
            return -5000.0

        # ═══ Scoring ═══
        score = 0.0

        # --- Component 1: Drawdown Score (40% weight) ---
        if res.max_drawdown_pct <= 4.0:
            dd_score = 100.0  # Perfect
        elif res.max_drawdown_pct <= 6.0:
            dd_score = 100.0 - ((res.max_drawdown_pct - 4.0) * 25.0)  # 100→50
        elif res.max_drawdown_pct <= 8.0:
            dd_score = 50.0 - ((res.max_drawdown_pct - 6.0) * 50.0)  # 50→-50
        else:
            dd_score = -100.0

        # --- Component 2: Profit Factor Score (30% weight) ---
        if res.profit_factor >= 2.0:
            pf_score = 100.0
        elif res.profit_factor >= 1.5:
            pf_score = 60.0 + (res.profit_factor - 1.5) * 80.0  # 60→100
        elif res.profit_factor >= 1.2:
            pf_score = 20.0 + (res.profit_factor - 1.2) * 133.0  # 20→60
        elif res.profit_factor >= 1.0:
            pf_score = -20.0 + (res.profit_factor - 1.0) * 200.0  # -20→20
        else:
            pf_score = -100.0

        # --- Component 3: Win Rate Score (20% weight) ---
        if res.win_rate >= 55:
            wr_score = 100.0
        elif res.win_rate >= 45:
            wr_score = 50.0 + (res.win_rate - 45) * 5.0  # 50→100
        elif res.win_rate >= 35:
            wr_score = -50.0 + (res.win_rate - 35) * 10.0  # -50→50
        else:
            wr_score = -100.0

        # --- Component 4: Trade Efficiency (10% weight) ---
        # Reward consistent small profits over big swings
        if res.total_profit_usd > 0:
            profit_per_trade = res.total_profit_usd / res.total_trades
            if profit_per_trade > 50:
                eff_score = 100.0
            elif profit_per_trade > 20:
                eff_score = 50.0
            elif profit_per_trade > 0:
                eff_score = 20.0
            else:
                eff_score = -50.0
        else:
            eff_score = -100.0

        # ═══ Weighted Total ═══
        score = (
            dd_score * 0.40
            + pf_score * 0.30
            + wr_score * 0.20
            + eff_score * 0.10
        )

        # ═══ Report to Optuna ═══
        trial.set_user_attr("win_rate", res.win_rate)
        trial.set_user_attr("profit_factor", res.profit_factor)
        trial.set_user_attr("max_dd_pct", res.max_drawdown_pct)
        trial.set_user_attr("total_trades", res.total_trades)
        trial.set_user_attr("total_profit_usd", res.total_profit_usd)
        trial.set_user_attr("final_equity", res.final_equity)

        return score


def main():
    print("=" * 60)
    print("🎯 BTCUSDm DD-FOCUSED OPTIMIZER")
    print("   Target: Max DD ≤ 6% while maintaining profitability")
    print("=" * 60)

    if not mt5.initialize():
        print("❌ MT5 initialization failed!")
        sys.exit(1)

    symbol = "BTCUSDm"
    days = 30  # 30-day walk-forward for BTC cycles
    initial_equity = 10000.0
    n_trials = 40  # Reduced for faster recovery while maintaining quality

    print(f"\n📊 Fetching {days}-day data for {symbol}...")
    df = fetch_data(symbol, mt5.TIMEFRAME_M15, days)
    if df is None:
        print(f"❌ No data for {symbol}")
        mt5.shutdown()
        sys.exit(1)

    print(f"✅ Loaded {len(df)} candles ({df['time'].iloc[0]} → {df['time'].iloc[-1]})")

    # ═══ Run Optuna Optimization ═══
    objective = BTCDDObjective(symbol, df, initial_equity)

    study = optuna.create_study(
        direction="maximize",
        study_name=f"BTC_DD_Focus_{datetime.now().strftime('%Y%m%d_%H%M')}",
        sampler=optuna.samplers.TPESampler(seed=42),  # Reproducible
    )
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    print(f"\n🔬 Running {n_trials} Optuna trials...")
    start_time = time.time()
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)
    elapsed = time.time() - start_time

    # ═══ Results ═══
    print("\n" + "=" * 60)
    print("🏆 OPTIMIZATION COMPLETE")
    print(f"   Duration: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print("=" * 60)

    if len(study.trials) == 0:
        print("❌ No valid trials found!")
        mt5.shutdown()
        return

    best = study.best_trial
    print(f"\n📈 Best Score: {best.value:.2f}")
    print(f"   Win Rate:       {best.user_attrs.get('win_rate', 'N/A')}%")
    print(f"   Profit Factor:  {best.user_attrs.get('profit_factor', 'N/A')}")
    print(f"   Max Drawdown:   {best.user_attrs.get('max_dd_pct', 'N/A')}%")
    print(f"   Total Trades:   {best.user_attrs.get('total_trades', 'N/A')}")
    print(f"   Total Profit:   ${best.user_attrs.get('total_profit_usd', 'N/A')}")
    print(f"   Final Equity:   ${best.user_attrs.get('final_equity', 'N/A')}")

    print(f"\n🔧 Optimal Parameters:")
    for key, value in best.params.items():
        print(f"   {key}: {value}")

    # ═══ Top 5 Trials (DD ≤ 6%) ═══
    valid_trials = [
        t for t in study.trials
        if t.state == optuna.trial.TrialState.COMPLETE
        and t.user_attrs.get("max_dd_pct", 999) <= 6.0
        and t.user_attrs.get("profit_factor", 0) >= 1.2
    ]
    valid_trials.sort(key=lambda t: t.value, reverse=True)

    print(f"\n{'=' * 60}")
    print(f"📋 Top 5 Trials (DD ≤ 6% AND PF ≥ 1.2):")
    print(f"{'=' * 60}")
    for i, t in enumerate(valid_trials[:5], 1):
        print(
            f"  #{i} Score={t.value:.1f} | "
            f"WR={t.user_attrs.get('win_rate', '?')}% | "
            f"PF={t.user_attrs.get('profit_factor', '?')} | "
            f"DD={t.user_attrs.get('max_dd_pct', '?')}% | "
            f"Trades={t.user_attrs.get('total_trades', '?')} | "
            f"Profit=${t.user_attrs.get('total_profit_usd', '?')}"
        )
        print(f"     Params: {t.params}")

    if not valid_trials:
        print("  ⚠️ No trials found with DD ≤ 6% AND PF ≥ 1.2")
        print("     Consider relaxing constraints or using longer data period.")

    # ═══ Save best config ═══
    output_path = Path(__file__).parent.parent / "data" / "btc_optimized_params.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    best_config = {
        "symbol": symbol,
        "optimized_at": datetime.now().isoformat(),
        "days_tested": days,
        "n_trials": n_trials,
        "best_score": best.value,
        "best_params": best.params,
        "best_metrics": {
            "win_rate": best.user_attrs.get("win_rate"),
            "profit_factor": best.user_attrs.get("profit_factor"),
            "max_dd_pct": best.user_attrs.get("max_dd_pct"),
            "total_trades": best.user_attrs.get("total_trades"),
            "total_profit_usd": best.user_attrs.get("total_profit_usd"),
            "final_equity": best.user_attrs.get("final_equity"),
        },
        "top_5_valid": [
            {
                "score": t.value,
                "params": t.params,
                "metrics": {
                    "win_rate": t.user_attrs.get("win_rate"),
                    "profit_factor": t.user_attrs.get("profit_factor"),
                    "max_dd_pct": t.user_attrs.get("max_dd_pct"),
                    "total_trades": t.user_attrs.get("total_trades"),
                },
            }
            for t in valid_trials[:5]
        ],
    }

    with open(output_path, "w") as f:
        json.dump(best_config, f, indent=2, default=str)
    print(f"\n💾 Results saved to: {output_path}")

    mt5.shutdown()
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
