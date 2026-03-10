"""
OPUS Backtest Engine V2 - Confluence-Optimized
Features:
- Trade cooldown tracking (10 bars default)
- Realistic PnL scaling per symbol (contract size)
- Spread simulation (deducted from every trade)
- Per-model breakdown (TREND vs LIQUIDITY stats)
- Uses same pipeline as live (single source of truth)

Usage:
  cd D:\VibeCode\Trade
  python -m backend.trader.scripts.run_backtest --symbol XAUUSD --bars 25920
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import argparse
import json
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from backend.trader.features.volatility import add_volatility_features
from backend.trader.features.structure import add_structure_features, detect_displacement
from backend.trader.features.institutional import add_institutional_features
from backend.trader.features.divergence import detect_rsi_divergence
from backend.trader.features.candle_patterns import detect_candle_patterns
from backend.trader.data.time_utils import time_utils
from backend.trader.regime.classifier import classify_regime
from backend.trader.liquidity.detector import detect_liquidity_events
from backend.trader.strategy.selector import select_and_generate_signal, reset_cooldown
from backend.trader.risk.gate import RiskEngine
from backend.trader.storage.sqlite_db import DataStore
from backend.trader.main import compute_lot_size
from backend.trader.risk.sizing import sizer

# --- Config ---
with open("d:/VibeCode/Trade/backend/trader/config/settings.json") as f:
    CONFIG = json.load(f)

risk_engine = RiskEngine()

# --- Symbol-specific contract specs (Exness Standard USD) ---
SYMBOL_SPECS = {
    "XAUUSD": {"point_value_per_lot": 1.0, "spread_points": 30, "contract_size": 100},
    "XAGUSD": {"point_value_per_lot": 0.5, "spread_points": 30, "contract_size": 5000},
    "BTCUSD": {"point_value_per_lot": 0.01, "spread_points": 500, "contract_size": 1},
    "UKOIL": {"point_value_per_lot": 0.01, "spread_points": 50, "contract_size": 1000},
}

REALISTIC_SPREAD_CAP = {
    "XAUUSD": 50,
    "XAGUSD": 80,
    "BTCUSD": 800,
    "UKOIL": 100,
}

TIMEFRAME_MAP = {
    "M1": "TIMEFRAME_M1",
    "M2": "TIMEFRAME_M2",
    "M3": "TIMEFRAME_M3",
    "M4": "TIMEFRAME_M4",
    "M5": "TIMEFRAME_M5",
    "M6": "TIMEFRAME_M6",
    "M10": "TIMEFRAME_M10",
    "M12": "TIMEFRAME_M12",
    "M15": "TIMEFRAME_M15",
    "M20": "TIMEFRAME_M20",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H2": "TIMEFRAME_H2",
    "H3": "TIMEFRAME_H3",
    "H4": "TIMEFRAME_H4",
    "H6": "TIMEFRAME_H6",
    "H8": "TIMEFRAME_H8",
    "H12": "TIMEFRAME_H12",
    "D1": "TIMEFRAME_D1",
}

def _fetch_mt5_spread(symbol: str) -> int:
    try:
        import MetaTrader5 as mt5
        from backend.trader.data.mapper import mapper
        if not mt5.initialize():
            return None
        broker_sym = mapper.to_broker(symbol)
        info = mt5.symbol_info(broker_sym)
        if info is not None:
            real_spread = info.spread
            print(f"  [MT5] real spread for {broker_sym}: {real_spread} points")
            return real_spread
    except Exception:
        pass
    return None

# --- Backtest Core ---
class BacktestEngine:
    def __init__(self, symbol: str, initial_equity: float = 1000.0):
        self.symbol = symbol
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.peak_equity = initial_equity
        self.trades = []
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.max_dd = 0.0
        self._cooldown_until_bar = 0
        self.lot_multiplier = 1.0
        self.config = CONFIG
        self._block_reasons = {}
        self.specs = SYMBOL_SPECS.get(symbol, SYMBOL_SPECS["XAUUSD"]).copy()

        mt5_spread = _fetch_mt5_spread(symbol)
        realistic_cap = REALISTIC_SPREAD_CAP.get(symbol, 100)
        
        if mt5_spread is not None:
            if mt5_spread <= realistic_cap:
                self.specs["spread_points"] = mt5_spread
                print(f"  [OK] Using MT5 real spread: {mt5_spread} points")
            else:
                self.specs["spread_points"] = realistic_cap
                print(f"  [WARN] MT5 spread {mt5_spread}pts is off-session inflated -> capped to {realistic_cap}pts")
        else:
            print(f"  [WARN] Using fallback spread: {self.specs['spread_points']} points")

    def _calculate_lot(self, signal: dict) -> float:
        # Dynamic lot sizing matching main.py logic
        sl_dist = abs(signal["entry_price"] - signal["sl"])
        
        # [MODIFIED] Use the same dynamic sizing as production
        strategy_name = signal.get("model", "unknown")
        regime = signal.get("regime", "ALL")
        fomo_mult = signal.get("fomo_mult", 1.0)
        
        # Get Alpha-Specific Dynamic Risk % (including FOMO penalty)
        dynamic_risk_pct = sizer.get_dynamic_risk(strategy_name, self.symbol, regime, fomo_penalty=fomo_mult)
        
        # Simulating Governor Multiplier (1.0 for normal backtest)
        effective_risk = dynamic_risk_pct * self.lot_multiplier
        
        # Symbol specifics
        tick_value = self.specs.get("point_value_per_lot", 1.0)
        tick_size = 0.01 if "XAU" in self.symbol or "XAG" in self.symbol else 1.0 
        if "BTC" in self.symbol: tick_size = 1.0
        
        # Check tiered scaling
        tiered_cfg = CONFIG.get("tiered_scaling", {})
        max_lot_allowed = 200.0
        if tiered_cfg.get("enabled", False):
            for tier in reversed(tiered_cfg.get("tiers", [])):
                if self.equity >= tier.get("min_equity", 0):
                    max_lot_allowed = tier.get(f"max_lot_{self.symbol}", 0.05)
                    break
        
        raw_lot = compute_lot_size(
            self.equity, effective_risk, sl_dist,
            tick_value=tick_value, tick_size=tick_size,
            volume_min=0.01, volume_step=0.01
        )
        lot = max(0.01, round(raw_lot, 2))
        return min(lot, max_lot_allowed)

    def simulate_trade(self, signal: dict, future_bars: pd.DataFrame) -> dict:
        entry = signal["entry_price"]
        sl = signal["sl"]
        tp1 = signal["tp1"]
        side = signal["side"]
        spread_cost = self.specs["spread_points"] * 0.01

        for _, bar in future_bars.iterrows():
            if side == "BUY":
                effective_entry = entry + spread_cost / 2
                if bar["low"] <= sl:
                    pnl = sl - effective_entry
                    return {"result": "SL", "pnl": pnl, "exit_price": sl}
                if bar["high"] >= tp1:
                    pnl = tp1 - effective_entry
                    return {"result": "TP1", "pnl": pnl, "exit_price": tp1}
            else:
                effective_entry = entry - spread_cost / 2
                if bar["high"] >= sl:
                    pnl = effective_entry - sl
                    return {"result": "SL", "pnl": pnl, "exit_price": sl}
                if bar["low"] <= tp1:
                    pnl = effective_entry - tp1
                    return {"result": "TP1", "pnl": pnl, "exit_price": tp1}

        last_close = future_bars.iloc[-1]["close"]
        if side == "BUY":
            pnl = last_close - (entry + spread_cost / 2)
        else:
            pnl = (entry - spread_cost / 2) - last_close
        return {"result": "TIMEOUT", "pnl": pnl, "exit_price": last_close}

    def run(self, df: pd.DataFrame, lookback: int = 100, hold_bars: int = 50):
        print(f"\n{'='*60}")
        print(f"  OPUS Backtest V2 - {self.symbol}")
        print(f"  Bars: {len(df)} | Lookback: {lookback} | Hold: {hold_bars}")
        print(f"  Initial Equity: ${self.initial_equity:.2f}")
        print(f"  Spread sim: {self.specs['spread_points']} points")
        print(f"{'='*60}\n")

        print("  Computing features on full dataset...")
        df = time_utils.add_session_features(df)
        df = add_volatility_features(df)
        df = add_structure_features(df)
        df = detect_displacement(df)
        df = add_institutional_features(df)
        df = detect_rsi_divergence(df)
        df = detect_candle_patterns(df)
        print("  Features computed. Starting walk-forward...\n")

        reset_cooldown()
        total_signals = 0
        blocked_signals = 0
        regime_cfg = CONFIG.get("regime", {})
        liq_cfg = CONFIG.get("liquidity", {})

        for i in range(lookback, len(df) - hold_bars):
            window = df.iloc[i - lookback: i]
            regime_res = classify_regime(window, regime_cfg)
            events = detect_liquidity_events(window, liq_cfg)
            context = {"symbol": self.symbol, "regime_result": regime_res}
            signal = select_and_generate_signal(window, context, events, current_bar=i)

            if signal is None:
                continue
            total_signals += 1

            if i > lookback:
                current_time = df.iloc[i].get("time", None)
                prev_time = df.iloc[i - 1].get("time", None)
                if current_time is not None and prev_time is not None:
                    try:
                        import pandas as _pdt
                        ct = _pdt.Timestamp(current_time)
                        pt = _pdt.Timestamp(prev_time)
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
                else:
                    self.consecutive_losses = 0
                    self._cooldown_until_bar = 0

            account_state = {
                "equity": self.equity, "daily_pnl": self.daily_pnl,
                "consecutive_losses": self.consecutive_losses
            }
            market_state = {"spread": self.specs["spread_points"], "is_news": False}
            gate = risk_engine.risk_gate(signal, account_state, market_state)

            if not gate["allowed"]:
                blocked_signals += 1
                for r in gate.get("reasons", []):
                    self._block_reasons[r] = self._block_reasons.get(r, 0) + 1
                if blocked_signals <= 5:
                    print(f"    [BLOCK] Bar {i}: {signal.get('model')} BLOCKED - {gate['reasons']}")
                continue

            lot = self._calculate_lot(signal)
            if lot <= 0:
                blocked_signals += 1
                continue

            future = df.iloc[i: i + hold_bars]
            outcome = self.simulate_trade(signal, future)
            contract_size = self.specs["contract_size"]
            pnl_usd = outcome["pnl"] * lot * contract_size
            self.equity += pnl_usd
            self.daily_pnl += pnl_usd

            if self.equity > self.peak_equity:
                self.peak_equity = self.equity
            dd = (self.peak_equity - self.equity) / self.peak_equity * 100
            if dd > self.max_dd:
                self.max_dd = dd

            if outcome["result"] == "SL":
                self.consecutive_losses += 1
            else:
                self.consecutive_losses = 0
                self._cooldown_until_bar = 0

            self.trades.append({
                "bar_index": i, "time": df.iloc[i].get("time", i),
                "side": signal["side"], "model": signal["model"],
                "regime": regime_res.get("regime", "UNKNOWN"),
                "entry": signal["entry_price"], "sl": signal["sl"],
                "tp1": signal["tp1"], "exit": outcome["exit_price"],
                "result": outcome["result"], "lot": lot,
                "pnl_usd": round(pnl_usd, 4), "equity": round(self.equity, 4),
                "dd_pct": round(dd, 2), "confidence": signal.get("confidence", 0),
                "rationale": signal.get("rationale", []),
            })

        self._print_results(total_signals, blocked_signals)
        return self.get_metrics()

    def get_metrics(self) -> dict:
        if not self.trades:
            return {"win_rate": 0, "profit_factor": 0, "max_dd": 0,
                    "total_trades": 0, "wins": 0, "losses": 0,
                    "net_pnl": 0, "final_equity": self.equity}
        wins = [t for t in self.trades if t["pnl_usd"] > 0]
        losses = [t for t in self.trades if t["pnl_usd"] <= 0]
        gross_profit = sum(t["pnl_usd"] for t in wins) if wins else 0
        gross_loss = abs(sum(t["pnl_usd"] for t in losses)) if losses else 1e-9
        return {
            "total_trades": len(self.trades), "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(self.trades) * 100, 1),
            "profit_factor": round(gross_profit / gross_loss, 2),
            "max_dd": round(self.max_dd, 2),
            "net_pnl": round(sum(t["pnl_usd"] for t in self.trades), 4),
            "final_equity": round(self.equity, 4),
        }

    def _print_results(self, total_signals, blocked_signals):
        m = self.get_metrics()
        print(f"\n{'='*60}")
        print(f"  BACKTEST RESULTS - {self.symbol}")
        print(f"{'='*60}")
        print(f"  Total Signals Generated : {total_signals}")
        print(f"  Signals Blocked by Risk : {blocked_signals}")
        print(f"  Trades Executed         : {m['total_trades']}")
        print(f"  Win Rate                : {m['win_rate']}%")
        print(f"  Profit Factor           : {m['profit_factor']}")
        print(f"  Max Drawdown            : {m['max_dd']}%")
        print(f"  Net P&L                 : ${m['net_pnl']}")
        print(f"  Final Equity            : ${m['final_equity']}")
        print(f"{'='*60}")

        if self.trades:
            models = set(t["model"] for t in self.trades)
            print(f"\n  [ANALYSIS] Per-Model Breakdown:")
            for model in sorted(models):
                mt = [t for t in self.trades if t["model"] == model]
                mw = [t for t in mt if t["pnl_usd"] > 0]
                ml = [t for t in mt if t["pnl_usd"] <= 0]
                wr = len(mw) / len(mt) * 100 if mt else 0
                gp = sum(t["pnl_usd"] for t in mw) if mw else 0
                gl = abs(sum(t["pnl_usd"] for t in ml)) if ml else 1e-9
                pf = gp / gl
                print(f"    [{model}] Trades={len(mt)} | WR={wr:.1f}% | PF={pf:.2f} | Net=${sum(t['pnl_usd'] for t in mt):.4f}")

            print(f"\n  Last 10 trades:")
            for t in self.trades[-10:]:
                res = "[WIN]" if t["pnl_usd"] > 0 else "[LOSS]"
                print(f"    {res} Bar {t['bar_index']}: {t['side']} {t['model']} [{t['regime'][:15]}] | PnL=${t['pnl_usd']:.4f} | Conf={t['confidence']:.2f}")

        if self._block_reasons:
            print(f"\n  [DIAG] Block Reason Breakdown:")
            for reason, count in sorted(self._block_reasons.items(), key=lambda x: -x[1]):
                print(f"    [{count:>4}x] {reason}")

def load_mt5_data(symbol: str, bars: int, timeframe: str = "M5", days: int = 0) -> pd.DataFrame:
    try:
        import MetaTrader5 as mt5
        from backend.trader.data.mapper import mapper
        tf_name = (timeframe or "M5").upper()
        tf_attr = TIMEFRAME_MAP.get(tf_name, "TIMEFRAME_M5")
        tf_const = getattr(mt5, tf_attr, mt5.TIMEFRAME_M5)
        mt5.shutdown()
        if not mt5.initialize():
            raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
        broker_sym = mapper.to_broker(symbol)
        mode_text = f"{days} days" if days and days > 0 else f"{bars} bars"
        print(f"  [MT5] Connected. Fetching {mode_text} for {broker_sym} ({tf_name})...")
        if not mt5.symbol_select(broker_sym, True):
            print(f"  [ERR] symbol_select({broker_sym}) failed: {mt5.last_error()}")
        if days and days > 0:
            utc_to = datetime.now(timezone.utc)
            utc_from = utc_to - timedelta(days=int(days))
            rates = mt5.copy_rates_range(broker_sym, tf_const, utc_from, utc_to)
        else:
            rates = mt5.copy_rates_from_pos(broker_sym, tf_const, 0, bars)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            print(f"  [DATA] Loaded {len(df)} bars from MT5 for {broker_sym} ({tf_name})")
            return df
        else:
            raise ValueError(f"MT5 returned no data for {broker_sym} ({tf_name})")
    except Exception as e:
        print(f"  [ERR] MT5 failed: {e}")
        import sys
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="OPUS Backtest Engine V2")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--bars", type=int, default=2000)
    parser.add_argument("--timeframe", default="M5")
    parser.add_argument("--days", type=int, default=0, help="If >0, fetch by days instead of bars")
    parser.add_argument("--equity", type=float, default=100.0)
    args = parser.parse_args()
    df = load_mt5_data(args.symbol, args.bars, timeframe=args.timeframe, days=args.days)
    engine = BacktestEngine(symbol=args.symbol, initial_equity=args.equity)
    metrics = engine.run(df)
    suffix = f"{args.timeframe.upper()}_{args.days}d" if args.days and args.days > 0 else f"{args.timeframe.upper()}_{args.bars}b"
    results_path = f"d:/VibeCode/Trade/backend/trader/data/backtest_{args.symbol}_{suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(results_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"\n  [FILE] Results saved to {results_path}")
    return metrics

if __name__ == "__main__":
    main()
