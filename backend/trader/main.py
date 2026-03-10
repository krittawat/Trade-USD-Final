import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
import math
from copy import deepcopy

# Ensure project root is on sys.path so `backend.trader.*` imports work from any CWD
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend")) # Allow `from app...` imports

import pandas as pd
import logging
from backend.trader.data.fetcher import fetcher
from backend.trader.features.volatility import add_volatility_features
from backend.trader.features.structure import add_structure_features, detect_displacement
from backend.trader.features.institutional import add_institutional_features
from backend.trader.features.divergence import detect_rsi_divergence
from backend.trader.features.candle_patterns import detect_candle_patterns, get_pattern_signal
from backend.trader.regime.classifier import classify_regime
from backend.trader.liquidity.detector import detect_liquidity_events
from backend.trader.strategy.selector import select_and_generate_signal, reset_cooldown
from backend.trader.risk.gate import risk_engine, _cooldown_until
from backend.trader.risk.opus_governor import governor
from backend.trader.execution.mt5_order import Executor
from backend.trader.execution.position_manager import manage_open_positions
from backend.trader.observability.logger import setup_logger, C
from backend.trader.storage.sqlite_db import db
from backend.trader.notification.telegram import notify_trade_signal
from backend.trader.brain.shadow_engine import shadow_engine
from backend.trader.brain.feedback_loop import feedback_loop
from backend.trader.brain.symbol_tuner import symbol_tuner
from backend.trader.features.smt_divergence import smt_tracker
from backend.trader.data.time_utils import time_utils
from backend.trader.services.maintenance import MaintenanceScheduler
from backend.trader.brain.brain_bridge import brain_bridge
from backend.trader.risk.sizing import sizer  # [NEW] Dynamic Sizing Engine


logger = logging.getLogger("opus_logger")

import json as _json
with open("d:/VibeCode/Trade/backend/trader/config/settings.json") as _f:
    _SETTINGS = _json.load(_f)

CONFIG = {
    # Focus universe for production growth: XAU, BTC, USOIL, USTEC, US30
    "symbols": ["XAUUSDm", "BTCUSDm", "USOILm", "US30m", "USTECm"],
    "shadow_symbols": ["XAGUSDm"],
    "timeframes": ["M3", "M5", "M6", "M12", "M15", "M30", "H1", "H2", "H4", "D1"],
    "symbol_timeframes": {
        "BTCUSDm": ["M3", "M5", "M6", "M12", "M15", "M30", "H1", "H2", "H4", "D1"],
        "USOILm": ["M3", "M5", "M6", "M12", "M15", "M30", "H1", "H2", "H4", "D1"],
        "XAUUSDm": ["M3", "M5", "M6", "M12", "M15", "M30", "H1", "H2", "H4", "D1"],
        "XAGUSDm": ["M3", "M5", "M6", "M12", "M15", "M30", "H1", "H2", "H4", "D1"],
        "US30m": ["M3", "M5", "M6", "M12", "M15", "M30", "H1", "H2", "H4", "D1"],
        "USTECm": ["M3", "M5", "M6", "M12", "M15", "M30", "H1", "H2", "H4", "D1"]
    },
    # Entry TF per asset to reduce noise/over-trading.
    "execution_timeframes": {
        "XAUUSDm": ["M5", "M15", "H1"],
        "XAGUSDm": ["M5", "M15", "H1"],
        "BTCUSDm": ["M5", "M15", "H1"],
        "USOILm": ["M5", "M15", "H1"],
        "US30m": ["M5", "M15", "H1"],
        "USTECm": ["M5", "M15", "H1"]
    },
    "safety_cap": {
        "equity_threshold": 120.0,
        "max_lot": 0.01
    }
}
DEFAULT_EXECUTION_TIMEFRAMES = deepcopy(CONFIG.get("execution_timeframes", {}))



import MetaTrader5 as mt5
TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1, "M2": mt5.TIMEFRAME_M2, "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4, "M5": mt5.TIMEFRAME_M5, "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10, "M12": mt5.TIMEFRAME_M12, "M15": mt5.TIMEFRAME_M15,
    "M20": mt5.TIMEFRAME_M20, "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2, "H3": mt5.TIMEFRAME_H3, "H4": mt5.TIMEFRAME_H4,
    "H6": mt5.TIMEFRAME_H6, "H8": mt5.TIMEFRAME_H8, "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1,
}
TF_MINUTES = {
    "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5, "M6": 6, "M10": 10, "M12": 12, "M15": 15,
    "M20": 20, "M30": 30, "H1": 60, "H2": 120, "H3": 180, "H4": 240, "H6": 360, "H8": 480,
    "H12": 720, "D1": 1440
}
LAST_PROCESSED_BAR = {}

SYM_ICON = {"XAUUSD": "🥇", "XAGUSD": "🥈", "BTCUSD": "₿ ", "BTCUSDm": "₿ ", "USOIL": "🛢️", "USOILm": "🛢️", "XAUUSDm": "🥇", "XAGUSDm": "🥈", "US30m": "📈", "USTECm": "💻"}
SYM_COLOR = {"XAUUSD": C.YELLOW, "XAGUSD": C.CYAN, "BTCUSD": C.MAGENTA, "BTCUSDm": C.MAGENTA, "USOIL": C.BLUE, "USOILm": C.BLUE, "XAUUSDm": C.YELLOW, "XAGUSDm": C.CYAN, "US30m": C.GREEN, "USTECm": C.BLUE}

# Track PnL and deals for the entire CALENDAR DAY (00:00 UTC)
APP_START_TIME = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
SESSION_START_TIME = datetime.now()

def set_app_start_time():
    global SESSION_START_TIME
    SESSION_START_TIME = datetime.now()

def _sym(symbol: str) -> str:
    icon = SYM_ICON.get(symbol, "")
    color = SYM_COLOR.get(symbol, C.WHITE)
    return f"{color}{icon}{symbol}{C.RST}"

def _bar(pct: float, width: int = 10) -> str:
    filled = int(pct * width)
    return f"{'█' * filled}{'░' * (width - filled)}"

def _bar_bucket_id(time_value, timeframe_str: str) -> int:
    ts = pd.to_datetime(time_value, unit="s", errors="coerce")
    if pd.isna(ts): ts = pd.to_datetime(time_value, errors="coerce")
    if pd.isna(ts): return int(time.time())
    tf_minutes = TF_MINUTES.get(timeframe_str, 5)
    tf_seconds = max(60, tf_minutes * 60)
    return int(ts.timestamp()) // tf_seconds

def parse_args():
    parser = argparse.ArgumentParser(description="OPUS Trading Engine")
    parser.add_argument("--mode", default="dry_run", choices=["live", "dry_run", "backtest"])
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--reset-pnl", action="store_true", help="Reset daily PnL counter to 0")
    parser.add_argument(
        "--use-backtest-overrides",
        dest="use_backtest_overrides",
        action="store_true",
        default=None,
        help="Apply latest DB-backed top-2 TF overrides to runtime execution_timeframes",
    )
    parser.add_argument(
        "--no-backtest-overrides",
        dest="use_backtest_overrides",
        action="store_false",
        help="Disable DB-backed TF overrides and use default execution_timeframes",
    )
    parser.add_argument(
        "--override-min-trades",
        type=int,
        default=3,
        help="Minimum trades filter when applying backtest TF overrides",
    )
    parser.add_argument(
        "--override-min-win-rate",
        type=float,
        default=35.0,
        help="Minimum win rate (%%) filter when applying backtest TF overrides",
    )
    parser.add_argument(
        "--override-min-pf",
        type=float,
        default=1.05,
        help="Minimum profit factor filter when applying backtest TF overrides",
    )
    parser.add_argument(
        "--override-max-dd",
        type=float,
        default=25.0,
        help="Maximum drawdown (%%) filter when applying backtest TF overrides",
    )
    parser.add_argument(
        "--override-search-completed-runs",
        type=int,
        default=30,
        help="How many recent COMPLETED backtest runs to scan for usable TF overrides",
    )
    parser.add_argument(
        "--override-require-positive-score",
        dest="override_require_positive_score",
        action="store_true",
        default=None,
        help="Require override score > 0 when selecting TF overrides",
    )
    parser.add_argument(
        "--override-allow-nonpositive-score",
        dest="override_require_positive_score",
        action="store_false",
        help="Allow score <= 0 when selecting TF overrides (temporary relaxed mode)",
    )
    parser.add_argument(
        "--override-require-positive-pnl",
        dest="override_require_positive_pnl",
        action="store_true",
        default=True,
        help="Require net_pnl > 0 when selecting TF overrides",
    )
    parser.add_argument(
        "--override-allow-nonpositive-pnl",
        dest="override_require_positive_pnl",
        action="store_false",
        help="Allow net_pnl <= 0 when selecting TF overrides",
    )
    return parser.parse_args()


def _resolve_override_toggle(args) -> bool:
    if args.use_backtest_overrides is not None:
        return bool(args.use_backtest_overrides)
    return args.mode in {"live", "dry_run"}


def _resolve_override_require_positive_score(args) -> bool:
    if args.override_require_positive_score is not None:
        return bool(args.override_require_positive_score)
    return True


def _apply_backtest_tf_overrides(
    *,
    enabled: bool,
    min_trades: int = 3,
    min_win_rate: float = 35.0,
    min_pf: float = 1.05,
    max_dd: float = 25.0,
    require_positive_pnl: bool = True,
    search_completed_runs: int = 30,
    require_positive_score: bool = True,
) -> None:
    from backend.trader.data.mapper import mapper

    CONFIG["execution_timeframes"] = deepcopy(DEFAULT_EXECUTION_TIMEFRAMES)

    if not enabled:
        logger.info("  📚 Backtest TF overrides: disabled (using default execution_timeframes)")
        return

    payload = db.get_latest_backtest_tf_overrides(
        top_k=2,
        min_trades=max(0, int(min_trades)),
        min_win_rate=max(0.0, float(min_win_rate)),
        min_profit_factor=max(0.0, float(min_pf)),
        max_drawdown_pct=max(0.0, float(max_dd)),
        require_positive_pnl=bool(require_positive_pnl),
        require_positive_score=bool(require_positive_score),
        search_completed_runs=max(1, int(search_completed_runs)),
    )
    run_meta = payload.get("run")
    by_symbol = payload.get("by_symbol", {}) or {}
    checked_runs = int(payload.get("checked_runs", 0) or 0)

    if not run_meta:
        logger.info("  📚 Backtest TF overrides: no completed run found, using defaults")
        return

    overridden = []
    fallback = []
    for broker_symbol, default_tfs in DEFAULT_EXECUTION_TIMEFRAMES.items():
        std_symbol = mapper.to_standard(broker_symbol).upper()
        symbol_keys = [std_symbol, broker_symbol.upper()]
        if std_symbol.endswith("M"):
            symbol_keys.append(std_symbol[:-1])
        if broker_symbol.upper().endswith("M"):
            symbol_keys.append(broker_symbol.upper()[:-1])
        rows = []
        for sym_key in symbol_keys:
            rows = by_symbol.get(sym_key, [])
            if rows:
                break
        chosen_tfs = []
        for row in rows:
            tf = str(row.get("timeframe", "")).upper()
            if tf and tf not in chosen_tfs:
                chosen_tfs.append(tf)
            if len(chosen_tfs) >= 2:
                break

        if chosen_tfs:
            CONFIG["execution_timeframes"][broker_symbol] = chosen_tfs
            overridden.append((broker_symbol, chosen_tfs, rows))
        else:
            CONFIG["execution_timeframes"][broker_symbol] = list(default_tfs)
            fallback.append((broker_symbol, list(default_tfs)))

    logger.info(
        f"  📚 Backtest TF overrides run={run_meta.get('run_id')} "
        f"engine={run_meta.get('engine')} overridden={len(overridden)} fallback={len(fallback)}"
        f"{f' checked={checked_runs}' if checked_runs else ''}"
        f" filters[min_trades={max(0, int(min_trades))},wr>={max(0.0, float(min_win_rate)):.1f},"
        f"pf>={max(0.0, float(min_pf)):.2f},dd<={max(0.0, float(max_dd)):.1f},"
        f"pnl>{'0' if require_positive_pnl else 'ANY'},score>{'0' if require_positive_score else 'ANY'}]"
    )
    if not overridden:
        logger.info("    ℹ️ No qualified TF rows met filters (trades/wr/pf/dd/pnl/score); defaults retained.")
    for sym, tfs, rows in overridden:
        src = ",".join([f"{r.get('engine')}:{r.get('score', 0):.2f}" for r in rows[:2]])
        logger.info(f"    ✅ {sym} -> {','.join(tfs)} ({src})")
    for sym, tfs in fallback:
        logger.info(f"    ↩ {sym} -> {','.join(tfs)} (default)")

def get_real_account_state(mt5_module, start_time: datetime = APP_START_TIME) -> dict:
    info = mt5_module.account_info()
    if info is None:
        return {"equity": 0, "daily_pnl": 0, "consecutive_losses": 0, "margin_level": 0, "balance": 0, "margin_free": 0}

    deals = mt5_module.history_deals_get(start_time, datetime.now(timezone.utc))
    pnl = 0.0
    consecutive_losses = 0
    if deals:
        for d in deals:
            if d.entry == 1 and d.type in (0, 1):
                pnl += d.profit
                if d.profit < 0: consecutive_losses += 1
                else: consecutive_losses = 0
    return {
        "equity": info.equity, "balance": info.balance, "daily_pnl": pnl,
        "real_daily_pnl": pnl, 
        "consecutive_losses": consecutive_losses, 
        "margin": info.margin,
        "margin_free": info.margin_free,
        "margin_level": info.margin_level if info.margin_level else 9999,
    }

def get_real_market_state(mt5_module, broker_symbol: str) -> dict:
    tick = mt5_module.symbol_info_tick(broker_symbol)
    sym_info = mt5_module.symbol_info(broker_symbol)
    if tick is None or sym_info is None:
        return {"spread": 9999, "is_news": False, "tick_value": 1.0, "tick_size": 0.01, "volume_min": 0.01, "volume_max": 200.0, "volume_step": 0.01}
    return {
        "spread": sym_info.spread, "bid": tick.bid, "ask": tick.ask, "is_news": False,
        "tick_value": sym_info.trade_tick_value or 1.0, "tick_size": sym_info.trade_tick_size or 0.01,
        "contract_size": sym_info.trade_contract_size or 100.0, "volume_min": sym_info.volume_min or 0.01,
        "volume_max": sym_info.volume_max or 200.0, "volume_step": sym_info.volume_step or 0.01,
    }

def compute_lot_size(equity: float, risk_pct: float, sl_distance: float,
                      tick_value: float = 1.0, tick_size: float = 0.01,
                      volume_min: float = 0.01, volume_step: float = 0.01) -> float:
    if sl_distance <= 0 or tick_size <= 0 or tick_value <= 0: return volume_min
    risk_usd = equity * (risk_pct / 100.0)
    ticks_at_risk = sl_distance / tick_size
    raw_lot = risk_usd / (ticks_at_risk * tick_value)
    lot = math.floor(raw_lot / volume_step) * volume_step
    return max(volume_min, round(lot, 2))

def count_open_positions(mt5_module, symbol: str) -> tuple:
    positions = mt5_module.positions_get(symbol=symbol)
    pos_count = len(positions) if positions else 0
    orders = mt5_module.orders_get(symbol=symbol)
    order_count = len(orders) if orders else 0
    return pos_count, order_count

def _regime_badge(regime: str) -> str:
    r = regime.lower()
    if "strong" in r and "up" in r: return f"{C.GREEN}▲ STR UP{C.RST}"
    elif "strong" in r and "down" in r: return f"{C.RED}▼ STR DN{C.RST}"
    elif "weak" in r and "up" in r: return f"{C.GREEN}△ WK UP{C.RST}"
    elif "weak" in r and "down" in r: return f"{C.RED}▽ WK DN{C.RST}"
    elif "range" in r or "chop" in r: return f"{C.YELLOW}◆ RANGE{C.RST}"
    return f"{C.GRAY}○ {regime[:8]}{C.RST}"

# Shared State for Correlation Followers
_LAST_XAU_SIGNAL = {} 

def _get_trend_icon(df: pd.DataFrame) -> str:
    if df is None or 'ema_200' not in df.columns or 'close' not in df.columns: return "⚪"
    latest = df.iloc[-1]
    return f"{C.GREEN}▲{C.RST}" if latest['close'] > latest['ema_200'] else f"{C.RED}▼{C.RST}"

def _vol_badge(latest: pd.Series) -> str:
    vr = latest.get('vol_ratio', 0)
    if latest.get('vol_spike'): return f"{C.RED}VOL⚡{vr:.1f}{C.RST}"
    elif latest.get('vol_dryup'): return f"{C.GRAY}VOL💤{vr:.1f}{C.RST}"
    return f"VOL {vr:.1f}"

def _safe_float(value, default: float = 0.0) -> float:
    try:
        out = float(value)
        if pd.isna(out):
            return default
        return out
    except Exception:
        return default


def _extract_model_from_comment(comment: str) -> str:
    text = str(comment or "").strip()
    if not text:
        return "UNKNOWN"
    if "OPUS_LM_" in text:
        return text.split("OPUS_LM_", 1)[-1] or "UNKNOWN"
    if "OPUS_" in text:
        return text.split("OPUS_", 1)[-1] or "UNKNOWN"
    return text[:40]


def _deal_closed_at_iso(deal_obj) -> str:
    try:
        ts = int(getattr(deal_obj, "time", 0) or 0)
        if ts <= 0:
            return datetime.now(timezone.utc).isoformat()
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def _persist_closed_deal(deal_obj):
    from backend.trader.data.mapper import mapper

    std_symbol = mapper.to_standard(getattr(deal_obj, "symbol", "UNKNOWN"))
    inserted = db.record_closed_trade({
        "deal_ticket": getattr(deal_obj, "ticket", None),
        "order_ticket": getattr(deal_obj, "order", None),
        "position_id": getattr(deal_obj, "position_id", None),
        "symbol": getattr(deal_obj, "symbol", std_symbol),
        "standard_symbol": std_symbol,
        "model": _extract_model_from_comment(getattr(deal_obj, "comment", "")),
        "side": "BUY" if getattr(deal_obj, "type", 0) == 1 else "SELL",
        "volume": getattr(deal_obj, "volume", 0.0),
        "price": getattr(deal_obj, "price", 0.0),
        "profit": getattr(deal_obj, "profit", 0.0),
        "commission": getattr(deal_obj, "commission", 0.0),
        "swap": getattr(deal_obj, "swap", 0.0),
        "closed_at": _deal_closed_at_iso(deal_obj),
        "close_reason": getattr(deal_obj, "comment", ""),
    })
    return std_symbol, inserted


def _bootstrap_closed_deals_360d(mt5_module):
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=360)
    deals = mt5_module.history_deals_get(start_time, end_time)
    if not deals:
        logger.info("  📈 360D Stats bootstrap: no historical deals found.")
        return

    inserted_count = 0
    for d in deals:
        if getattr(d, "entry", None) == 1 and getattr(d, "type", None) in (0, 1):
            _, inserted = _persist_closed_deal(d)
            if inserted:
                inserted_count += 1
    logger.info(f"  📈 360D Stats bootstrap complete: +{inserted_count} closed deals synced.")


def _compute_atr_deviation(df: pd.DataFrame) -> float:
    if "atr" not in df.columns or df.empty:
        return 1.0
    atr_now = _safe_float(df["atr"].iloc[-1], 0.0)
    if atr_now <= 0:
        return 1.0
    atr_baseline = _safe_float(df["atr"].tail(20).mean(), atr_now)
    if atr_baseline <= 0:
        return 1.0
    return atr_now / atr_baseline


def _build_xag_follower_signal(last_xau: dict, latest_price: float, atr: float) -> dict:
    side = str(last_xau.get("side", "")).upper()
    if side not in {"BUY", "SELL"}:
        return {}
    dist = max(atr * 1.5, 0.01)
    signal = {
        "symbol": "XAGUSDm",
        "side": side,
        "model": "XAU_FOLLOWER",
        "entry_type": "MARKET",
        "entry_price": latest_price,
        "confidence": max(0.75, _safe_float(last_xau.get("confidence"), 0.80)),
        "rationale": ["Follow confirmed XAU direction within 5m window"],
    }
    if side == "BUY":
        signal["sl"] = latest_price - dist
        signal["tp1"] = latest_price + (dist * 2)
    else:
        signal["sl"] = latest_price + dist
        signal["tp1"] = latest_price - (dist * 2)
    return signal


def tick_cycle(mode: str, symbol: str, timeframe_str: str, executor: Executor, cycle_num: int = 0, cycle_cache: dict = None):
    try:
        from backend.trader.data.mapper import mapper
        from backend.trader.execution.order_manager import manage_pending_orders

        if cycle_cache is None:
            cycle_cache = {}
        htf_cache = cycle_cache.setdefault("htf_bias", {})
        raw_rates_cache = cycle_cache.setdefault("raw_rates", {})

        tf = TIMEFRAME_MAP.get(timeframe_str, mt5.TIMEFRAME_M5)
        broker_symbol = mapper.to_broker(symbol)
        is_shadow = symbol in CONFIG.get("shadow_symbols", [])
        display_sym = f"{f'{C.CYAN}[SHADOW]{C.RST} ' if is_shadow else ''}{_sym(symbol)}({timeframe_str})"

        cache_key = (symbol, timeframe_str)
        df = raw_rates_cache.get(cache_key)
        if df is None:
            df = fetcher.get_rates(symbol, tf, 400)
            if df is not None:
                raw_rates_cache[cache_key] = df
        if df is None or len(df) < 80: return

        work_df = df.iloc[:-1].copy()
        if work_df.empty: return

        latest = work_df.iloc[-1]
        current_bar = _bar_bucket_id(latest.get("time", work_df.index[-1]), timeframe_str)
        dedup_key = f"{symbol}:{timeframe_str}"
        if LAST_PROCESSED_BAR.get(dedup_key) == current_bar: return
        LAST_PROCESSED_BAR[dedup_key] = current_bar

        # [NEW] Pre-fetch account state to check Governor BEFORE high-CPU feature calc
        acct = get_real_account_state(mt5)
        # Apply Session Reset logic if active
        if "--reset-pnl" in sys.argv:
            session_deals = mt5.history_deals_get(SESSION_START_TIME, datetime.now())
            pnl_session = sum(d.profit for d in session_deals) if session_deals else 0.0
            acct['daily_pnl'] = pnl_session
            acct['session_reset'] = True

        market_state = get_real_market_state(mt5, mapper.to_broker(symbol))
        opus_status = governor.compute_status(acct, market_state)
        
        if not opus_status.trade_allowed:
            # Only log if it's the first time we're blocked this cycle to avoid spam
            if cycle_num % 5 == 1:
                logger.info(f"{display_sym:<25} 🏛️ {C.YELLOW}GOVERNOR BLOCKED: {opus_status.block_reason}{C.RST}")
            return

        work_df = add_volatility_features(work_df)
        work_df = add_structure_features(work_df)
        work_df = detect_displacement(work_df)
        work_df = add_institutional_features(work_df)
        work_df = detect_rsi_divergence(work_df)
        work_df = detect_candle_patterns(work_df)
        
        regime_res = classify_regime(work_df, {"trend_threshold": 0.45, "volatility_compression_threshold": 0.5, "volatility_expansion_threshold": 1.5})
        events = detect_liquidity_events(work_df, {"eqh_eql_threshold_points": 50, "sweep_lookback_bars": 80})
        latest_features = work_df.iloc[-1]
        market_state["vol_ratio"] = _safe_float(latest_features.get("vol_ratio", 1.0), 1.0)
        market_state["atr_deviation"] = _compute_atr_deviation(work_df)

        if mode == "live" and not is_shadow:
            manage_pending_orders(symbol, work_df, timeframe_str)

        pat = get_pattern_signal(latest_features)
        rsi_now = _safe_float(latest_features.get("rsi", 50.0), 50.0)
        macd_hist_now = _safe_float(latest_features.get("macd_hist", 0.0), 0.0)
        momentum_now = _safe_float(latest_features.get("momentum_14", 100.0), 100.0)
        logger.info(
            f"{display_sym:<25} {C.BOLD}{latest['close']:>10.2f}{C.RST} │ {_regime_badge(regime_res['regime'])} │ "
            f"EMA {_get_trend_icon(work_df)} │ {_vol_badge(work_df.iloc[-1])} │ "
            f"Pwr {work_df.iloc[-1].get('net_power_25', work_df.iloc[-1].get('net_power', 0)):+.1f} │ "
            f"RSI {rsi_now:>5.1f} │ MACD {macd_hist_now:+.4f} │ MOM {momentum_now:>6.2f}"
            f"{f' │ Liq:{len(events)}' if events else ''}"
            f"{f' 🕯️{','.join(pat['patterns'])}' if pat['patterns'] else ''}"
        )

        # ─── 6. HTF Trend Alignment from Context ─────────────
        # Prioritize H1 EMA 200 for institutional trend filter (cached once/symbol/cycle)
        if symbol in htf_cache:
            htf_val = htf_cache[symbol]
        else:
            h1_df = fetcher.get_rates(symbol, mt5.TIMEFRAME_H1, 250)
            if h1_df is not None and not h1_df.empty:
                h1_df = add_volatility_features(h1_df) # Ensure ema_200 is computed
                h1_latest = h1_df.iloc[-1]
                htf_val = 'BULLISH' if h1_latest['close'] > h1_latest.get('ema_200', h1_latest['close']) else 'BEARISH'
            else:
                # Fallback to current TF EMA 200
                htf_val = 'BULLISH' if latest['close'] > latest_features.get('ema_200', latest['close']) else 'BEARISH'
            htf_cache[symbol] = htf_val

        context = {
            "symbol": symbol, 
            "timeframe": timeframe_str, 
            "regime_result": regime_res, 
            "htf_ema_align": htf_val
        }
        signal = select_and_generate_signal(work_df, context, events, current_bar=current_bar)

        # 🥈 [INSTITUTIONAL] XAG Follower Logic: If no signal but XAU recently fired
        if symbol == "XAGUSDm" and not signal:
            last_xau = _LAST_XAU_SIGNAL.get("XAUUSDm")
            if last_xau and (datetime.now(timezone.utc) - last_xau['time']).total_seconds() < 300: # 5 min window
                logger.info(f"🥈 [FOLLOWER] XAG following XAU {last_xau['side']} signal...")
                atr = _safe_float(work_df["atr"].iloc[-1] if "atr" in work_df.columns else 0.05, 0.05)
                signal = _build_xag_follower_signal(last_xau, _safe_float(latest["close"], 0.0), atr)

        if not signal:
            return

        signal["symbol"] = symbol
        signal["side"] = str(signal.get("side", "")).upper()
        signal.setdefault("entry_type", "MARKET")
        signal.setdefault("confidence", 0.5)

        required_fields = ("side", "entry_price", "sl", "tp1")
        missing = [k for k in required_fields if signal.get(k) in (None, "")]
        if missing or signal["side"] not in {"BUY", "SELL"}:
            logger.warning(f"{display_sym} 🚫 Invalid signal payload dropped: missing={missing} side={signal.get('side')}")
            return

        logger.info(
            f"{display_sym} 🎯 {C.BOLD}{'🟢 BUY' if signal['side'] == 'BUY' else '🔴 SELL'}{C.RST} "
            f"{signal.get('model', 'UNKNOWN')} @ {signal['entry_price']:.2f} "
            f"SL={signal['sl']:.2f} TP={signal['tp1']:.2f} "
            f"Conf={_bar(signal['confidence'])} {signal['confidence']:.0%}"
        )
        db.record_signal(signal)
        shadow_engine.capture_practice_signal(signal, work_df, context=context, events=events)
        if is_shadow:
            return

        if not opus_status.trade_allowed:
            return

        pos_count, order_count = count_open_positions(mt5, broker_symbol)
        if signal.get('entry_type') == 'LIMIT' and (pos_count == 0 or signal.get('model') == 'MOMENTUM_SCALPER_V2'):
            signal['entry_type'] = 'MARKET'
            # Ensure price adjustment respects tick size/digits
            info = mt5.symbol_info(broker_symbol)
            if info:
                tick = mt5.symbol_info_tick(broker_symbol)
                current_market_price = tick.ask if signal['side'] == 'BUY' else tick.bid
                diff = current_market_price - signal['entry_price']

                signal['entry_price'] = current_market_price
                signal['sl'] += diff
                for tp in ['tp1', 'tp2', 'tp3']:
                    if tp in signal:
                        signal[tp] += diff

                # Log adjustment
                logger.debug(f"  ⚡ Adjusted LIMIT -> MARKET for {symbol} (Diff: {diff:.5f})")

        if (signal.get('entry_type') != 'LIMIT' and pos_count >= 2) or (signal.get('entry_type') == 'LIMIT' and order_count >= 2):
            return

        # Per-symbol position limit check
        std_sym = mapper.to_standard(symbol)
        max_pos = _SETTINGS.get("risk_limits", {}).get("max_positions_per_symbol", {}).get(std_sym, 2)
        if (signal.get('entry_type') != 'LIMIT' and pos_count >= max_pos) or (signal.get('entry_type') == 'LIMIT' and order_count >= max_pos):
            logger.debug(f"  🚫 BLOCKED: {symbol} at max positions ({pos_count}/{max_pos})")
            return

        # Calculate session-based losses for the gate
        session_acct = get_real_account_state(mt5, start_time=SESSION_START_TIME)
        gate_res = risk_engine.risk_gate(
            signal,
            acct,
            market_state,
            opus_status=opus_status,
            current_session=time_utils.assign_session(datetime.now(timezone.utc)),
            session_consecutive_losses=session_acct['consecutive_losses']
        )
        if gate_res['allowed']:
            # ─── Dynamic Sizing (Fractional Kelly) ──────────
            strategy_name = signal.get('model', signal.get('strategy', 'unknown'))
            regime = regime_res.get('regime', 'ALL')

            # Get Alpha-Specific Dynamic Risk % (including FOMO penalty)
            fomo_mult = signal.get('fomo_mult', 1.0)
            dynamic_risk_pct = sizer.get_dynamic_risk(strategy_name, symbol, regime, fomo_penalty=fomo_mult)

            # Apply Governor Multiplier (Vault/Defensive)
            mult = opus_status.lot_multiplier
            effective_risk = dynamic_risk_pct * mult

            # Final Lot Calculation
            raw_lot = compute_lot_size(
                acct['equity'],
                effective_risk,
                abs(signal['entry_price'] - signal['sl']),
                tick_value=market_state['tick_value'],
                tick_size=market_state['tick_size'],
                volume_min=market_state['volume_min'],
                volume_step=market_state['volume_step']
            )

            lot = max(market_state['volume_min'], round(raw_lot, 2))

            # ─── Safety Cap (Scalable Growth) ──────────
            safety = CONFIG.get("safety_cap", {"equity_threshold": 300.0, "max_lot": 0.01})
            if acct['equity'] < safety["equity_threshold"]:
                if lot > safety["max_lot"]:
                    logger.info(f"🛡️ [SAFETY] Equity ${acct['equity']:.2f} < ${safety['equity_threshold']}: capping lot {lot} -> {safety['max_lot']}")
                    lot = safety["max_lot"]

            if mult < 1.0:
                logger.info(f"🛡️ [GOVERNOR] Mult x{mult:.1f} Active (Risk: {dynamic_risk_pct}% -> {effective_risk:.2f}%)")

            if gate_res.get("vol_warning"):
                lot = max(market_state['volume_min'], round(lot * 0.5, 2))
                logger.warning(f"⚠️ [VOL] Warning Active - Reducing lot by 50% -> {lot}")

            margin_req = mt5.order_calc_margin(
                mt5.ORDER_TYPE_BUY if signal['side'] == 'BUY' else mt5.ORDER_TYPE_SELL,
                broker_symbol,
                lot,
                signal['entry_price']
            )
            if margin_req is None:
                logger.warning(f"  🚫 BLOCKED: margin calculation failed for {symbol} (lot={lot})")
                return

            utilization_after = ((acct.get('margin', 0) + margin_req) / max(acct['equity'], 1)) * 100
            logger.info(f"🛡️ [AUDIT] Margin Check: Req=${margin_req:.2f} | Est Utilization={utilization_after:.1f}%")

            # Ensure margin_req is affordable
            if margin_req <= acct['margin_free'] * 0.3:
                order_result = executor.place_order(signal, lot_size=lot)
                if isinstance(order_result, dict) and order_result.get("status") == "ok":
                    notify_trade_signal(signal, acct['equity'], acct['daily_pnl'], lot=lot, vol_warning=gate_res.get("vol_warning"))

                    # 🥈 [INSTITUTIONAL] XAU Signal Success Cache
                    if symbol == "XAUUSDm":
                        _LAST_XAU_SIGNAL["XAUUSDm"] = {
                            "side": signal['side'],
                            "time": datetime.now(timezone.utc),
                            "model": signal.get('model', 'UNKNOWN'),
                            "confidence": signal.get("confidence", 0.8),
                        }
                        logger.info("🥇 [CACHE] XAU signal saved for XAG follower")
                else:
                    err = mt5.last_error()
                    logger.error(f"❌ [MT5 ERROR] Order failed for {symbol}: {err} | executor={order_result}")
            else:
                logger.warning(f"  🚫 BLOCKED: margin_req ${margin_req:.2f} exceeds 30% of free margin ${acct['margin_free']:.2f}")
        else:
            block_msg = f"  🚫 {C.RED}BLOCKED{C.RST}: {gate_res['reasons']}"
            logger.info(block_msg)
            notify_trade_signal(signal, acct['equity'], acct['daily_pnl'], blocked=True, block_reasons=gate_res['reasons'])
    except Exception as e:
        logger.error(f"{_sym(symbol)} ❌ {e}", exc_info=True)

def _print_startup_header(mode: str, daily_target: float):
    mode_display = {"live": f"{C.RED}{C.BOLD}🔴 LIVE TRADING{C.RST}", "dry_run": f"{C.YELLOW}📋 DRY RUN (PAPER TRADE){C.RST}", "backtest": f"{C.CYAN}🧪 BACKTEST MODE{C.RST}"}.get(mode, f"MODE: {mode}")
    all_assets = CONFIG["symbols"] + CONFIG.get("shadow_symbols", [])
    logger.info(f"\n{C.CYAN}╔{'═' * 50}╗{C.RST}\n{C.CYAN}║{C.RST}  🚀 ANTIGRAVITY ENGINE {mode_display:^34} {C.CYAN}║{C.RST}\n{C.CYAN}║{C.RST}  Assets: {', '.join(all_assets):<38}{C.CYAN}║{C.RST}\n{C.CYAN}║{C.RST}  TFs: {', '.join(CONFIG['timeframes']):<9} │ Target: $60/day          {C.CYAN}║{C.RST}\n{C.CYAN}╚{'═' * 50}╝{C.RST}")

def _print_header(cycle: int, mode: str, opus_status=None, news_blocked: bool = False):
    badges = "".join([f" {C.RED}{C.BOLD}● LIVE{C.RST}" if mode == "live" else f" {C.YELLOW}● DRY{C.RST}", f" {C.CYAN}[VAULT]{C.RST}" if opus_status and opus_status.risk_level == "VAULT_LOCK" else "", f" {C.RED}[NEWS]{C.RST}" if news_blocked else ""])
    logger.info(f"\n{C.CYAN}{'━' * 52}{C.RST}\n{badges}  Cycle {C.BOLD}#{cycle}{C.RST}  │  {time.strftime('%H:%M:%S')}  │  {time_utils.get_current_session_label()}\n{C.CYAN}{'━' * 52}{C.RST}")

def main():
    args = parse_args()
    use_overrides = _resolve_override_toggle(args)
    require_positive_score = _resolve_override_require_positive_score(args)
    _apply_backtest_tf_overrides(
        enabled=use_overrides,
        min_trades=args.override_min_trades,
        min_win_rate=args.override_min_win_rate,
        min_pf=args.override_min_pf,
        max_dd=args.override_max_dd,
        require_positive_pnl=bool(args.override_require_positive_pnl),
        search_completed_runs=args.override_search_completed_runs,
        require_positive_score=require_positive_score,
    )
    DAILY_TARGET = governor.daily_target_usd
    _print_startup_header(args.mode, DAILY_TARGET)
    executor = Executor(mode=args.mode)
    scheduler = MaintenanceScheduler(executor)

    if args.mode in ["live", "dry_run"]:
        if not fetcher.connect(): return
        brain_bridge.connect()
        _bootstrap_closed_deals_360d(mt5)
        symbol_tuner.set_symbol_universe(CONFIG["symbols"] + CONFIG.get("shadow_symbols", []))
        symbol_tuner.refresh(force=True)
        set_app_start_time()

        from backend.trader.services.news_filter import news_filter
        import asyncio
        
        cycle, LAST_DEAL_TIME, LAST_NEWS_REFRESH = 0, datetime.now(), datetime.now() - timedelta(hours=7)
        reset_cooldowns_applied = False
        try:
            while True:
                cycle += 1
                if (datetime.now() - LAST_NEWS_REFRESH).total_seconds() > 21600:
                    try:
                        asyncio.run(news_filter.refresh_async())
                        LAST_NEWS_REFRESH = datetime.now()
                    except Exception as _news_err:
                        logger.warning(f"News refresh failed: {_news_err}")

                # Calculate session PnL (since bot started or reset)
                server_time = datetime.now(timezone.utc)
                session_deals = mt5.history_deals_get(SESSION_START_TIME, server_time)
                pnl_session = sum(d.profit for d in session_deals) if session_deals else 0.0

                acct = get_real_account_state(mt5)
                # If reset pnl, we also want to clear the global cooldowns to let it trade
                if args.reset_pnl:
                    acct['session_reset'] = True
                    acct['daily_pnl'] = pnl_session
                    if not reset_cooldowns_applied:
                        _cooldown_until.clear()
                        reset_cooldown()
                        reset_cooldowns_applied = True
                        logger.info("🔄 [RESET] PnL Reset Active: Session PnL used, Cooldowns cleared (one-time).")
                
                opus = governor.compute_status(acct)
                news_blocked = any(not news_filter.is_safe(s) for s in CONFIG["symbols"])
                _print_header(cycle, args.mode, opus, news_blocked)

                if cycle % 10 == 1:
                    pnl_daily = acct.get('real_daily_pnl', acct['daily_pnl']) 
                    # Note: acct['daily_pnl'] might be overridden if reset_pnl is on
                    
                    if args.reset_pnl:
                        # If reset-pnl is on, we treat session_pnl as our "daily_pnl" for the bypass
                        pnl_to_check = pnl_session
                    else:
                        pnl_to_check = pnl_daily

                    logger.info(f"  📊 Balance ${acct['balance']:.2f} │ Equity ${acct['equity']:.2f}")
                    logger.info(f"  📊 PnL วันนี้ {C.GREEN if pnl_daily >= 0 else C.RED}${pnl_daily:+.2f}{C.RST} │ Session {C.CYAN}${pnl_session:+.2f}{C.RST}")

                # ─── RISK LIMITS (Unified with settings.json) ───
                loss_limit_currency = _SETTINGS.get("risk_limits", {}).get("max_daily_loss_currency", 14.0)
                
                # Check for breach
                pnl_check = pnl_session if args.reset_pnl else acct['daily_pnl']
                
                # If reset_pnl is active, we ALSO override the acct['daily_pnl'] used by the governor
                if args.reset_pnl:
                    acct['daily_pnl'] = pnl_session
                
                if pnl_check <= -abs(loss_limit_currency):
                    from backend.trader.notification.telegram import notify_status
                    msg = f"🛡️ KILL-SWITCH ACTIVATED: Loss ${pnl_check:.2f} reached limit -${loss_limit_currency}. Bot paused 15 min."
                    notify_status(msg)
                    logger.critical(f"  ✖ ERROR   LOSS LIMIT BREACHED ${pnl_check:.2f} (Limit: -${loss_limit_currency}) -- paused 15 min")
                    time.sleep(900)
                    continue

                if pnl_check >= DAILY_TARGET:
                    from backend.trader.notification.telegram import notify_status
                    msg = f"🏆 DAILY TARGET HIT! Profit ${pnl_check:.2f} >= ${DAILY_TARGET:.2f}. Trading locked."
                    notify_status(msg)
                    logger.info(f"  TARGET HIT! ${pnl_check:.2f} -- locking profits")
                    time.sleep(300)
                    continue

                # ─── MAINTENANCE & GAP SAFETY ───
                scheduler.run_cycle(acct)
                maint_blocked, maint_reason = scheduler.is_safety_blocked()
                if maint_blocked:
                    logger.info(f"  🕒 MAINTENANCE: {maint_reason} — รอ 60 วินาที")
                    time.sleep(60)
                    continue

                if news_blocked and not news_filter.is_safe(CONFIG["symbols"][0]):
                    logger.info(f"  🏛️ NEWS BLOCK — รอ 60 วินาที")
                    time.sleep(60)
                    continue

                # ─── DEALS MONITORING ───
                try:
                    now = datetime.now()
                    deals = mt5.history_deals_get(LAST_DEAL_TIME, now)
                    if deals:
                        from backend.trader.notification.telegram import notify_trade_close
                        for d in deals:
                            if d.entry == 1:
                                std_symbol, _ = _persist_closed_deal(d)
                                if d.profit < 0:
                                    risk_engine.trigger_cooldown(std_symbol, "Loss")

                                a_now = get_real_account_state(mt5)
                                notify_trade_close(
                                    symbol=std_symbol,
                                    side="BUY" if d.type == 1 else "SELL",
                                    profit=d.profit,
                                    lot=d.volume,
                                    equity=a_now['equity'],
                                    daily_pnl=a_now['daily_pnl'],
                                    comment=d.comment
                                )
                        LAST_DEAL_TIME = now
                except Exception as _deal_err:
                    logger.error(f"Deal monitoring error: {_deal_err}", exc_info=True)

                # ─── AI FEEDBACK LOOP: Refresh dynamic thresholds ───
                feedback_loop.refresh(cycle)
                symbol_tuner.refresh(cycle)

                # ─── SMT DIVERGENCE ───
                try:
                    gold_df_smt = fetcher.get_rates("XAUUSD", mt5.TIMEFRAME_M15, 200)
                    silver_df_smt = fetcher.get_rates("XAGUSD", mt5.TIMEFRAME_M15, 200)
                    smt_result = smt_tracker.refresh(gold_df_smt, silver_df_smt, cycle)
                except Exception as _smt_err:
                    logger.debug(f"SMT refresh skipped: {_smt_err}")

                # ─── SIGNAL GENERATION CYCLE ───
                cycle_cache = {"htf_bias": {}, "raw_rates": {}}
                for symbol in CONFIG["symbols"] + CONFIG.get("shadow_symbols", []):
                    from backend.trader.data.mapper import mapper
                    broker_sym = mapper.to_broker(symbol)
                    symbol_tfs = CONFIG.get("symbol_timeframes", {}).get(broker_sym, CONFIG["timeframes"])
                    execution_tfs = CONFIG.get("execution_timeframes", {}).get(broker_sym, symbol_tfs)
                    
                    trend_map = []
                    for tf_str in symbol_tfs:
                        df_tf = fetcher.get_rates(symbol, TIMEFRAME_MAP.get(tf_str), 400)
                        if df_tf is not None:
                            cycle_cache["raw_rates"][(symbol, tf_str)] = df_tf
                            closed_df = df_tf.iloc[:-1].copy()
                            if closed_df.empty:
                                continue
                            closed_df = add_volatility_features(closed_df)
                            v_curr = int(closed_df['tick_volume'].iloc[-1]) if 'tick_volume' in closed_df.columns else 0
                            v_avg = int(closed_df['tick_volume'].tail(20).mean()) if v_curr else 0
                            trend_map.append(f"{tf_str}:EMA{_get_trend_icon(closed_df)} {'HI:' if v_curr > v_avg*1.5 else 'LOW:' if v_curr < v_avg*0.5 else 'AVG:'}{v_curr}/{v_avg}")
                    
                    logger.info(
                        f"  {f'{C.CYAN}[SHADOW]{C.RST} ' if symbol in CONFIG.get('shadow_symbols', []) else ''}"
                        f"{_sym(symbol)} TREND: {' '.join(trend_map)} | EXEC TF: {','.join(execution_tfs)}"
                    )
                    for tf_str in execution_tfs:
                        tick_cycle(args.mode, symbol, tf_str, executor, cycle, cycle_cache=cycle_cache)

                # ─── ALWAYS MANAGE OPEN POSITIONS (SAFETY FIRST) ───
                if args.mode == "live":
                    try:
                        m = manage_open_positions()
                        logger.info(f"  🛡️ Shield: managed {m if isinstance(m, int) else '?'} positions")
                    except Exception as _pos_err:
                        logger.error(f"Position manager error: {_pos_err}", exc_info=True)
                
                for sym in CONFIG["symbols"] + CONFIG.get("shadow_symbols", []):
                    tk = mt5.symbol_info_tick(sym)
                    if tk: shadow_engine.track_experience(sym, tk.bid or tk.last)

                logger.info(f"  {C.DIM}⏳ รอรอบถัดไป 10 วินาที...{C.RST}"); time.sleep(10)
        except KeyboardInterrupt: logger.info(f"\n{C.YELLOW}⚡ ปิดระบบ...{C.RST}")
        finally: fetcher.disconnect()
    
    elif args.mode == "backtest":
        # ─── BACKTEST EXECUTION (Offline Simulation) ───
        if not fetcher.connect(): return
        brain_bridge.connect()
        symbol_tuner.set_symbol_universe(CONFIG["symbols"] + CONFIG.get("shadow_symbols", []))
        symbol_tuner.refresh(force=True)
        
        symbol = args.symbol
        broker_sym = "BTCUSD" if "BTC" in symbol else symbol # Simplified mapper
        logger.info(f"🧪 STARTING BACKTEST: {symbol} (Last 1000 Bars)")
        
        # In backtest mode, we simulate the tick_cycle by feeding historical data
        # Note: A real institutional backtester would use a dedicated engine, 
        # but here we reuse the tick_cycle for logic parity.
        for tf_str in CONFIG["timeframes"]:
            logger.info(f"  Processing {symbol} {tf_str}...")
            tick_cycle("backtest", symbol, tf_str, executor, 0)
            
        logger.info("🧪 BACKTEST COMPLETE.")
        fetcher.disconnect()

if __name__ == "__main__":
    main()
