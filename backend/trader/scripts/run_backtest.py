"""
OPUS Backtest Engine V2 - Confluence-Optimized
Features:
- Trade cooldown tracking (10 bars default)
- Realistic PnL scaling per symbol (contract size)
- Spread simulation (deducted from every trade)
- Per-model breakdown (TREND vs LIQUIDITY stats)
- Uses same pipeline as live (single source of truth)

Usage:
  cd D:\\VibeCode\\Trade
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
from typing import Dict
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


def load_strategy_params_arg(raw: str | None, strategy_mode: str = "all") -> dict:
    text = str(raw or "").strip()
    if not text:
        return {}

    payload = text
    candidate = Path(text)
    if candidate.exists() and candidate.is_file():
        payload = candidate.read_text(encoding="utf-8")

    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("strategy params must decode to a JSON object")

    mode = str(strategy_mode or "all").strip().lower()
    if mode and mode != "all" and mode not in data:
        scalar_values = all(not isinstance(v, dict) for v in data.values())
        if scalar_values:
            return {mode: data}
    return data

# --- Symbol-specific contract specs / strategy profiles ---
SYMBOL_SPECS = {
    "XAUUSD": {"point_value_per_lot": 1.0, "spread_points": 30, "contract_size": 100.0, "point": 0.01, "tick_size": 0.01, "volume_min": 0.01, "volume_step": 0.01},
    "XAGUSD": {"point_value_per_lot": 5.0, "spread_points": 30, "contract_size": 5000.0, "point": 0.001, "tick_size": 0.001, "volume_min": 0.01, "volume_step": 0.01},
    "BTCUSD": {"point_value_per_lot": 1.0, "spread_points": 500, "contract_size": 1.0, "point": 1.0, "tick_size": 1.0, "volume_min": 0.01, "volume_step": 0.01},
    "USOIL": {"point_value_per_lot": 1.0, "spread_points": 50, "contract_size": 1000.0, "point": 0.001, "tick_size": 0.001, "volume_min": 0.01, "volume_step": 0.01},
    "UKOIL": {"point_value_per_lot": 1.0, "spread_points": 50, "contract_size": 1000.0, "point": 0.001, "tick_size": 0.001, "volume_min": 0.01, "volume_step": 0.01},
    "US30": {"point_value_per_lot": 1.0, "spread_points": 120, "contract_size": 1.0, "point": 1.0, "tick_size": 1.0, "volume_min": 0.01, "volume_step": 0.01},
    "USTEC": {"point_value_per_lot": 1.0, "spread_points": 160, "contract_size": 1.0, "point": 0.1, "tick_size": 0.1, "volume_min": 0.01, "volume_step": 0.01},
    "EURUSD": {"point_value_per_lot": 10.0, "spread_points": 18, "contract_size": 100000.0, "point": 0.00001, "tick_size": 0.00001, "volume_min": 0.01, "volume_step": 0.01},
    "GBPUSD": {"point_value_per_lot": 10.0, "spread_points": 22, "contract_size": 100000.0, "point": 0.00001, "tick_size": 0.00001, "volume_min": 0.01, "volume_step": 0.01},
    "USDJPY": {"point_value_per_lot": 9.0, "spread_points": 18, "contract_size": 100000.0, "point": 0.001, "tick_size": 0.001, "volume_min": 0.01, "volume_step": 0.01},
}

REALISTIC_SPREAD_CAP = {
    "XAUUSD": 50,
    "XAGUSD": 80,
    "BTCUSD": 800,
    "USOIL": 100,
    "UKOIL": 100,
    "US30": 250,
    "USTEC": 300,
    "EURUSD": 35,
    "GBPUSD": 45,
    "USDJPY": 35,
}

STRATEGY_PRESETS = {
    "all": {"whitelist": None, "force_enabled_models": [], "min_confidence": 0.62, "profile": "all"},
    "momentum": {
        "whitelist": ["MOMENTUM_RIDER", "MOMENTUM_SCALPER_V2", "USOIL_MOMENTUM"],
        "force_enabled_models": ["MOMENTUM_RIDER", "MOMENTUM_SCALPER_V2", "USOIL_MOMENTUM"],
        "min_confidence": 0.70,
        "profile": "momentum",
    },
    "rapid_pullback": {
        "whitelist": ["RAPID_PULLBACK"],
        "force_enabled_models": ["RAPID_PULLBACK"],
        "min_confidence": 0.62,
        "profile": "rapid_pullback",
    },
    "indicator_confluence": {
        "whitelist": ["INDICATOR_CONFLUENCE"],
        "force_enabled_models": ["INDICATOR_CONFLUENCE"],
        "min_confidence": 0.62,
        "profile": "indicator_confluence",
    },
    "momentum_rider": {
        "whitelist": ["MOMENTUM_RIDER"],
        "force_enabled_models": ["MOMENTUM_RIDER"],
        "min_confidence": 0.70,
        "profile": "momentum_rider",
    },
    "momentum_scalper_v2": {
        "whitelist": ["MOMENTUM_SCALPER_V2"],
        "force_enabled_models": ["MOMENTUM_SCALPER_V2"],
        "min_confidence": 0.70,
        "profile": "momentum_scalper_v2",
    },
    "usoil_momentum": {
        "whitelist": ["USOIL_MOMENTUM"],
        "force_enabled_models": ["USOIL_MOMENTUM"],
        "min_confidence": 0.70,
        "profile": "usoil_momentum",
    },
}

_SYMBOL_DETAIL_CACHE: Dict[str, dict] = {}

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

def _normalize_symbol_key(symbol: str) -> str:
    from backend.trader.data.mapper import mapper

    raw = str(mapper.to_standard(str(symbol or "").strip()) or symbol or "").upper()
    if raw.endswith(("M", "C")) and raw[:-1] in SYMBOL_SPECS:
        raw = raw[:-1]
    if "XAU" in raw:
        return "XAUUSD"
    if "XAG" in raw:
        return "XAGUSD"
    if "BTC" in raw:
        return "BTCUSD"
    if "OIL" in raw:
        return "USOIL"
    if "30" in raw:
        return "US30"
    if "TEC" in raw or "NAS" in raw:
        return "USTEC"
    return raw


def _resolve_strategy_controls(strategy_mode: str) -> dict:
    key = str(strategy_mode or "all").strip().lower()
    return dict(STRATEGY_PRESETS.get(key, STRATEGY_PRESETS["all"]))


def _fetch_mt5_symbol_details(symbol: str) -> dict:
    cache_key = _normalize_symbol_key(symbol)
    cached = _SYMBOL_DETAIL_CACHE.get(cache_key)
    if cached is not None:
        return dict(cached)

    try:
        import MetaTrader5 as mt5
        from backend.trader.data.mapper import mapper
        if not mt5.initialize():
            return {}
        broker_sym = mapper.to_broker(symbol)
        info = mt5.symbol_info(broker_sym)
        if info is not None:
            details = {
                "broker_symbol": broker_sym,
                "spread_points": int(getattr(info, "spread", 0) or 0),
                "point": float(getattr(info, "point", 0.0) or 0.0),
                "tick_size": float(getattr(info, "trade_tick_size", 0.0) or getattr(info, "point", 0.0) or 0.0),
                "point_value_per_lot": float(getattr(info, "trade_tick_value", 0.0) or 0.0),
                "contract_size": float(getattr(info, "trade_contract_size", 0.0) or 0.0),
                "volume_min": float(getattr(info, "volume_min", 0.01) or 0.01),
                "volume_step": float(getattr(info, "volume_step", 0.01) or 0.01),
            }
            print(f"  [MT5] symbol details for {broker_sym}: spread={details['spread_points']} point={details['point']} tick_size={details['tick_size']}")
            _SYMBOL_DETAIL_CACHE[cache_key] = dict(details)
            return details
    except Exception:
        pass
    _SYMBOL_DETAIL_CACHE[cache_key] = {}
    return {}


def _resolve_symbol_specs(symbol: str) -> dict:
    symbol_key = _normalize_symbol_key(symbol)
    specs = dict(SYMBOL_SPECS.get(symbol_key, SYMBOL_SPECS["XAUUSD"]))
    mt5_details = _fetch_mt5_symbol_details(symbol)
    if mt5_details:
        for field in ["point", "tick_size", "point_value_per_lot", "contract_size", "volume_min", "volume_step"]:
            if float(mt5_details.get(field, 0.0) or 0.0) > 0.0:
                specs[field] = float(mt5_details[field])

        real_spread = int(mt5_details.get("spread_points", 0) or 0)
        realistic_cap = REALISTIC_SPREAD_CAP.get(symbol_key, 100)
        if real_spread > 0:
            if real_spread <= realistic_cap:
                specs["spread_points"] = real_spread
                print(f"  [OK] Using MT5 real spread: {real_spread} points")
            else:
                specs["spread_points"] = realistic_cap
                print(f"  [WARN] MT5 spread {real_spread}pts is off-session inflated -> capped to {realistic_cap}pts")
    else:
        print(f"  [WARN] Using fallback spread: {specs['spread_points']} points")

    specs.setdefault("point", specs.get("tick_size", 0.01) or 0.01)
    specs.setdefault("tick_size", specs.get("point", 0.01) or 0.01)
    specs.setdefault("volume_min", 0.01)
    specs.setdefault("volume_step", 0.01)
    return specs

# --- Backtest Core ---
class BacktestEngine:
    def __init__(
        self,
        symbol: str,
        initial_equity: float = 1000.0,
        timeframe: str = "M5",
        strategy_mode: str = "all",
        brain_params: dict | None = None,
    ):
        self.symbol = symbol
        self.standard_symbol = _normalize_symbol_key(symbol)
        self.initial_equity = initial_equity
        self.timeframe = str(timeframe or "M5").upper()
        self.strategy_mode = str(strategy_mode or "all").lower()
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
        self.strategy_controls = _resolve_strategy_controls(self.strategy_mode)
        self.specs = _resolve_symbol_specs(symbol)
        self.brain_params = dict(brain_params or {})

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
        tick_value = float(self.specs.get("point_value_per_lot", 1.0) or 1.0)
        tick_size = float(self.specs.get("tick_size", self.specs.get("point", 0.01)) or 0.01)
        volume_min = float(self.specs.get("volume_min", 0.01) or 0.01)
        volume_step = float(self.specs.get("volume_step", 0.01) or 0.01)
        
        # Check tiered scaling
        tiered_cfg = CONFIG.get("tiered_scaling", {})
        max_lot_allowed = 200.0
        if tiered_cfg.get("enabled", False):
            for tier in reversed(tiered_cfg.get("tiers", [])):
                if self.equity >= tier.get("min_equity", 0):
                    max_lot_allowed = (
                        tier.get(f"max_lot_{self.symbol}")
                        or tier.get(f"max_lot_{self.standard_symbol}")
                        or tier.get(f"max_lot_{self.standard_symbol}m")
                        or 0.05
                    )
                    break
        
        raw_lot = compute_lot_size(
            self.equity, effective_risk, sl_dist,
            tick_value=tick_value, tick_size=tick_size,
            volume_min=volume_min, volume_step=volume_step
        )
        lot = max(volume_min, round(raw_lot, 2))
        return min(lot, max_lot_allowed)

    def simulate_trade(self, signal: dict, future_bars: pd.DataFrame) -> dict:
        entry = signal["entry_price"]
        sl = signal["sl"]
        tp1 = signal["tp1"]
        side = signal["side"]
        spread_cost = float(self.specs.get("spread_points", 0.0) or 0.0) * float(self.specs.get("point", 0.01) or 0.01)

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
        print(f"  Timeframe: {self.timeframe} | Strategy: {self.strategy_mode}")
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
        strategy_whitelist = self.strategy_controls.get("whitelist")
        force_enabled_models = self.strategy_controls.get("force_enabled_models", [])
        strategy_eval_min_confidence = float(self.strategy_controls.get("min_confidence", 0.62) or 0.62)
        strategy_profile = str(self.strategy_controls.get("profile", self.strategy_mode))

        for i in range(lookback, len(df) - hold_bars):
            window = df.iloc[i - lookback: i]
            regime_res = classify_regime(window, regime_cfg)
            events = detect_liquidity_events(window, liq_cfg)
            row_now = df.iloc[i]
            session_label = str(row_now.get("session", "")).upper().strip()
            if not session_label:
                try:
                    ts = pd.Timestamp(row_now.get("time"))
                    if ts.tzinfo is None:
                        ts = ts.tz_localize("UTC")
                    session_label = time_utils.assign_session(ts.to_pydatetime())
                except Exception:
                    session_label = "UNKNOWN"
            context = {
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "regime_result": regime_res,
                "session": session_label,
                "current_session": session_label,
                "backtest_mode": True,
                "current_time": row_now.get("time"),
                "strategy_profile": strategy_profile,
            }
            if strategy_whitelist:
                context["strategy_whitelist"] = strategy_whitelist
                context["strategy_eval_mode"] = True
                context["strategy_eval_min_confidence"] = strategy_eval_min_confidence
            if force_enabled_models:
                context["force_enabled_models"] = list(force_enabled_models)
            if self.brain_params:
                context["brain_params"] = self.brain_params
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
            market_state = {
                "spread": self.specs["spread_points"],
                "is_news": False,
                "vol_ratio": float(window.iloc[-1].get("vol_ratio", 1.0) or 1.0),
                "backtest_mode": True,
            }
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
    parser.add_argument(
        "--strategy",
        default="all",
        choices=[
            "all",
            "momentum",
            "momentum_rider",
            "momentum_scalper_v2",
            "usoil_momentum",
            "rapid_pullback",
            "indicator_confluence",
        ],
        help="Backtest all selector models or force a single model.",
    )
    parser.add_argument("--days", type=int, default=0, help="If >0, fetch by days instead of bars")
    parser.add_argument("--equity", type=float, default=100.0)
    parser.add_argument(
        "--strategy-params-json",
        default="",
        help="JSON object or path to JSON file with brain_params overrides for the selected strategy.",
    )
    args = parser.parse_args()
    df = load_mt5_data(args.symbol, args.bars, timeframe=args.timeframe, days=args.days)
    try:
        strategy_params = load_strategy_params_arg(args.strategy_params_json, args.strategy)
    except Exception as e:
        print(f"[ERR] invalid --strategy-params-json: {e}")
        return 2
    engine = BacktestEngine(
        symbol=args.symbol,
        initial_equity=args.equity,
        timeframe=args.timeframe,
        strategy_mode=args.strategy,
        brain_params=strategy_params,
    )
    metrics = engine.run(df)
    suffix_base = f"{args.timeframe.upper()}_{args.days}d" if args.days and args.days > 0 else f"{args.timeframe.upper()}_{args.bars}b"
    suffix = f"{args.strategy}_{suffix_base}"
    results_path = f"d:/VibeCode/Trade/backend/trader/data/backtest_{args.symbol}_{suffix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(results_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"\n  [FILE] Results saved to {results_path}")
    return metrics

if __name__ == "__main__":
    main()
