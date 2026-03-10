"""
OPUS backtest engine.
Uses the same signal pipeline as live mode and reports win rate, PF, DD.

Usage:
  python trader/scripts/run_backtest.py --symbol XAUUSD --bars 5000 --equity 1000
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from trader.features.candle_patterns import detect_candle_patterns
from trader.features.structure import add_structure_features, detect_displacement
from trader.features.volatility import add_volatility_features
from trader.liquidity.detector import detect_liquidity_events
from trader.regime.classifier import classify_regime
from trader.risk.gate import RiskEngine
from trader.strategy.selector import select_and_generate_signal

CONFIG_PATH = PROJECT_ROOT / "trader" / "config" / "settings.json"
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

risk_engine = RiskEngine()


class BacktestEngine:
    def __init__(self, symbol: str, initial_equity: float = 1000.0, cooldown_bars: int = 5):
        self.symbol = symbol
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.peak_equity = initial_equity
        self.trades = []
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.max_dd = 0.0
        self.cooldown_bars = max(0, int(cooldown_bars))
        self.last_trade_index = -10**9
        self.current_day = None

    def _reset_daily_counters_if_needed(self, bar_time):
        if bar_time is None:
            return
        if isinstance(bar_time, (int, float, np.integer, np.floating)):
            dt = pd.to_datetime(bar_time, unit="s", utc=True)
        else:
            dt = pd.to_datetime(bar_time, utc=True)
        day = dt.date()
        if self.current_day is None or day != self.current_day:
            self.current_day = day
            self.daily_pnl = 0.0
            self.consecutive_losses = 0

    def _enrich_once(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Precompute causal features once.
        Structure remains per-window to avoid future leakage.
        """
        enriched = df.copy()
        enriched = add_volatility_features(enriched)
        enriched = detect_displacement(enriched)
        enriched = detect_candle_patterns(enriched)
        return enriched

    def simulate_trade(self, signal: dict, future_bars: pd.DataFrame) -> dict:
        """
        Simulate a trade outcome using future bars.
        Checks if SL or TP1 gets hit first.
        """
        entry = float(signal["entry_price"])
        sl = float(signal["sl"])
        tp1 = float(signal["tp1"])
        side = signal["side"]

        for _, bar in future_bars.iterrows():
            high = float(bar["high"])
            low = float(bar["low"])
            if side == "BUY":
                if low <= sl:
                    return {"result": "SL", "pnl": sl - entry, "exit_price": sl}
                if high >= tp1:
                    return {"result": "TP1", "pnl": tp1 - entry, "exit_price": tp1}
            else:
                if high >= sl:
                    return {"result": "SL", "pnl": entry - sl, "exit_price": sl}
                if low <= tp1:
                    return {"result": "TP1", "pnl": entry - tp1, "exit_price": tp1}

        last_close = float(future_bars.iloc[-1]["close"])
        pnl = (last_close - entry) if side == "BUY" else (entry - last_close)
        return {"result": "TIMEOUT", "pnl": pnl, "exit_price": last_close}

    def run(self, df: pd.DataFrame, lookback: int = 100, hold_bars: int = 50):
        print("\n" + "=" * 60)
        print(f"  OPUS Backtest - {self.symbol}")
        print(f"  Bars: {len(df)} | Lookback: {lookback} | Hold: {hold_bars} | Cooldown: {self.cooldown_bars}")
        print(f"  Initial Equity: ${self.initial_equity:.2f}")
        print("=" * 60 + "\n")

        total_signals = 0
        blocked_signals = 0
        base_df = self._enrich_once(df)

        for i in range(lookback, len(base_df) - hold_bars):
            if i - self.last_trade_index < self.cooldown_bars:
                continue

            window = base_df.iloc[i - lookback: i].copy()
            window = add_structure_features(window)

            regime_cfg = CONFIG.get("regime", {})
            liq_cfg = CONFIG.get("liquidity", {})
            regime_res = classify_regime(window, regime_cfg)
            events = detect_liquidity_events(window, liq_cfg)

            context = {"symbol": self.symbol, "regime_result": regime_res}
            signal = select_and_generate_signal(window, context, events)
            if signal is None:
                continue
            total_signals += 1

            bar_time = base_df.iloc[i].get("time", None)
            self._reset_daily_counters_if_needed(bar_time)
            account_state = {
                "equity": self.equity,
                "daily_pnl": self.daily_pnl,
                "consecutive_losses": self.consecutive_losses,
            }
            market_state = {"spread": 10, "is_news": False}
            gate = risk_engine.risk_gate(signal, account_state, market_state)
            if not gate["allowed"]:
                blocked_signals += 1
                continue

            future = base_df.iloc[i: i + hold_bars]
            outcome = self.simulate_trade(signal, future)

            # Simplified PnL normalization.
            lot = 0.01
            point_value = 1.0
            pnl_usd = outcome["pnl"] * lot * point_value * 100

            self.equity += pnl_usd
            self.daily_pnl += pnl_usd
            self.last_trade_index = i

            if self.equity > self.peak_equity:
                self.peak_equity = self.equity
            dd = (self.peak_equity - self.equity) / self.peak_equity * 100
            if dd > self.max_dd:
                self.max_dd = dd

            if outcome["result"] == "SL":
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0

            self.trades.append(
                {
                    "bar_index": i,
                    "side": signal["side"],
                    "model": signal["model"],
                    "entry": signal["entry_price"],
                    "sl": signal["sl"],
                    "tp1": signal["tp1"],
                    "exit": outcome["exit_price"],
                    "result": outcome["result"],
                    "pnl_usd": round(pnl_usd, 2),
                    "equity": round(self.equity, 2),
                    "dd_pct": round(dd, 2),
                }
            )

        self._print_results(total_signals, blocked_signals)
        return self.get_metrics()

    def get_metrics(self) -> dict:
        if not self.trades:
            return {
                "win_rate": 0,
                "profit_factor": 0,
                "max_dd": 0,
                "total_trades": 0,
                "net_pnl": 0,
                "final_equity": round(self.equity, 2),
            }

        wins = [t for t in self.trades if t["pnl_usd"] > 0]
        losses = [t for t in self.trades if t["pnl_usd"] <= 0]
        gross_profit = sum(t["pnl_usd"] for t in wins) if wins else 0.0
        gross_loss = abs(sum(t["pnl_usd"] for t in losses)) if losses else 1e-9

        return {
            "total_trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(self.trades) * 100, 1),
            "profit_factor": round(gross_profit / gross_loss, 2),
            "max_dd": round(self.max_dd, 2),
            "net_pnl": round(sum(t["pnl_usd"] for t in self.trades), 2),
            "final_equity": round(self.equity, 2),
        }

    def _print_results(self, total_signals: int, blocked_signals: int):
        m = self.get_metrics()
        print("\n" + "=" * 60)
        print(f"  BACKTEST RESULTS - {self.symbol}")
        print("=" * 60)
        print(f"  Total Signals Generated : {total_signals}")
        print(f"  Signals Blocked by Risk : {blocked_signals}")
        print(f"  Trades Executed         : {m['total_trades']}")
        print(f"  Wins / Losses           : {m.get('wins', 0)} / {m.get('losses', 0)}")
        print(f"  Win Rate                : {m['win_rate']}%")
        print(f"  Profit Factor           : {m['profit_factor']}")
        print(f"  Max Drawdown            : {m['max_dd']}%")
        print(f"  Net PnL                 : ${m['net_pnl']}")
        print(f"  Final Equity            : ${m['final_equity']}")
        print("=" * 60)
        if self.trades:
            print("\n  Last 10 trades:")
            for t in self.trades[-10:]:
                side = "+" if t["pnl_usd"] > 0 else "-"
                print(
                    f"    {side} Bar {t['bar_index']}: {t['side']} {t['model']} | "
                    f"Entry={t['entry']:.2f} SL={t['sl']:.2f} Exit={t['exit']:.2f} | "
                    f"PnL=${t['pnl_usd']} | Equity=${t['equity']}"
                )


def load_mt5_data(symbol: str, bars: int) -> pd.DataFrame:
    """Try MT5 first, then fallback to synthetic data."""
    try:
        import MetaTrader5 as mt5
        from trader.data.mapper import mapper

        if not mt5.initialize():
            raise RuntimeError("MT5 init failed")
        broker_sym = mapper.to_broker(symbol)
        rates = mt5.copy_rates_from_pos(broker_sym, mt5.TIMEFRAME_M5, 0, bars)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            print(f"  Loaded {len(df)} bars from MT5 for {broker_sym}")
            return df
    except Exception as e:
        print(f"  MT5 unavailable ({e}), using synthetic data")

    np.random.seed(123)
    base = 2000.0
    closes = base + np.cumsum(np.random.randn(bars) * 3)
    df = pd.DataFrame(
        {
            "time": pd.date_range("2024-01-01", periods=bars, freq="5min"),
            "open": closes + np.random.randn(bars) * 0.5,
            "high": closes + np.abs(np.random.randn(bars) * 4),
            "low": closes - np.abs(np.random.randn(bars) * 4),
            "close": closes,
            "tick_volume": np.random.randint(100, 5000, bars),
        }
    )
    df["high"] = df[["open", "close", "high"]].max(axis=1)
    df["low"] = df[["open", "close", "low"]].min(axis=1)
    print(f"  Generated {bars} synthetic bars for {symbol}")
    return df


def main():
    parser = argparse.ArgumentParser(description="OPUS Backtest Engine")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--bars", type=int, default=5000)
    parser.add_argument("--equity", type=float, default=1000.0)
    parser.add_argument("--lookback", type=int, default=100)
    parser.add_argument("--hold", type=int, default=50)
    parser.add_argument("--cooldown", type=int, default=5)
    args = parser.parse_args()

    df = load_mt5_data(args.symbol, args.bars)
    engine = BacktestEngine(symbol=args.symbol, initial_equity=args.equity, cooldown_bars=args.cooldown)
    metrics = engine.run(df, lookback=args.lookback, hold_bars=args.hold)

    results_dir = PROJECT_ROOT / "trader" / "data"
    results_dir.mkdir(parents=True, exist_ok=True)
    results_path = results_dir / f"backtest_{args.symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)
    print(f"\n  Results saved to {results_path}")

    return metrics


if __name__ == "__main__":
    main()
