import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Ensure project root is on sys.path so `trader.*` imports work from any CWD
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trader.data.fetcher import fetcher
from trader.execution.mt5_order import Executor
from trader.execution.position_manager import manage_open_positions
from trader.features.candle_patterns import detect_candle_patterns, get_pattern_signal
from trader.features.structure import add_structure_features
from trader.features.volatility import add_volatility_features
from trader.liquidity.detector import detect_liquidity_events
from trader.observability.logger import setup_logger
from trader.regime.classifier import classify_regime
from trader.risk.gate import risk_engine
from trader.storage.sqlite_db import db
from trader.strategy.selector import select_and_generate_signal

logger = logging.getLogger("opus_logger")
setup_logger("opus_logger")

CONFIG = {
    "symbols": ["XAUUSD", "BTCUSD", "XAGUSD"],
    "timeframe_minutes": 5,
}

# Per-symbol state to prevent duplicate entries and overtrading.
LAST_PROCESSED_BAR = {}
LAST_TRADE_TIME = {}


def parse_args():
    parser = argparse.ArgumentParser(description="OPUS Trading Engine")
    parser.add_argument("--mode", default="dry_run", choices=["live", "dry_run", "backtest"])
    parser.add_argument("--symbols", default=",".join(CONFIG["symbols"]), help="Comma-separated standard symbols")
    parser.add_argument("--poll-seconds", type=int, default=10, help="Main loop sleep interval")
    parser.add_argument("--cooldown-bars", type=int, default=30, help="Min bars between entries per symbol")
    return parser.parse_args()


def get_real_account_state(mt5_module) -> dict:
    """Pull account state from MT5."""
    info = mt5_module.account_info()
    if info is None:
        return {"equity": 0.0, "daily_pnl": 0.0, "consecutive_losses": 0}

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    deals = mt5_module.history_deals_get(today_start, datetime.now())

    daily_pnl = 0.0
    consecutive_losses = 0
    if deals:
        for deal in deals:
            if deal.entry == 1:  # OUT deals (closed)
                daily_pnl += deal.profit
                if deal.profit < 0:
                    consecutive_losses += 1
                else:
                    consecutive_losses = 0

    return {
        "equity": float(info.equity),
        "balance": float(info.balance),
        "daily_pnl": float(daily_pnl),
        "consecutive_losses": int(consecutive_losses),
        "margin_free": float(info.margin_free),
    }


def get_real_market_state(mt5_module, broker_symbol: str) -> dict:
    """Pull spread and tick data from MT5."""
    tick = mt5_module.symbol_info_tick(broker_symbol)
    sym_info = mt5_module.symbol_info(broker_symbol)
    if tick is None or sym_info is None:
        return {"spread": 9999, "is_news": False}
    return {
        "spread": int(sym_info.spread),
        "bid": float(tick.bid),
        "ask": float(tick.ask),
        "is_news": False,  # News filter hook
    }


def compute_lot_size(equity: float, risk_pct: float, sl_distance: float, point_value: float = 1.0) -> float:
    """Simple dynamic lot sizing: risk_pct of equity divided by SL distance."""
    if sl_distance <= 0:
        return 0.01
    risk_usd = equity * (risk_pct / 100.0)
    raw_lot = risk_usd / (sl_distance * point_value)
    return max(0.01, min(0.05, round(raw_lot, 2)))


def count_open_positions(mt5_module, symbol: str) -> int:
    positions = mt5_module.positions_get(symbol=symbol)
    return len(positions) if positions else 0


def _in_cooldown(symbol: str, bar_time: pd.Timestamp, cooldown_bars: int) -> bool:
    if cooldown_bars <= 0:
        return False
    last_trade_time = LAST_TRADE_TIME.get(symbol)
    if last_trade_time is None:
        return False
    cooldown_until = last_trade_time + timedelta(minutes=CONFIG["timeframe_minutes"] * cooldown_bars)
    return bar_time < cooldown_until


def tick_cycle(mode: str, symbol: str, executor: Executor, cycle_num: int, cooldown_bars: int):
    """Single pipeline cycle per symbol."""
    try:
        import MetaTrader5 as mt5
        from trader.data.mapper import mapper
        from trader.features.structure import detect_displacement

        tf = mt5.TIMEFRAME_M5
        broker_symbol = mapper.to_broker(symbol)

        logger.info(f"[Cycle {cycle_num}] {symbol} - Fetching data...")
        df = fetcher.get_rates(symbol, tf, 220)
        if df is None or len(df) < 120:
            logger.warning(f"[Cycle {cycle_num}] {symbol} - No/insufficient data from MT5")
            return

        # Use only closed bars to avoid repaint/noise from the currently forming candle.
        work_df = df.iloc[:-1].copy()
        latest_closed = work_df.iloc[-1]
        bar_time = pd.to_datetime(latest_closed["time"])
        bar_key = f"{symbol}:{bar_time.isoformat()}"

        if LAST_PROCESSED_BAR.get(symbol) == bar_key:
            logger.info(f"[Cycle {cycle_num}] {symbol} - Skipping; bar already processed")
            return
        LAST_PROCESSED_BAR[symbol] = bar_key

        logger.info(
            f"[Cycle {cycle_num}] {symbol} - Closed bar O={latest_closed['open']:.2f} "
            f"H={latest_closed['high']:.2f} L={latest_closed['low']:.2f} C={latest_closed['close']:.2f}"
        )

        work_df = add_volatility_features(work_df)
        work_df = add_structure_features(work_df)
        work_df = detect_displacement(work_df)
        work_df = detect_candle_patterns(work_df)

        latest = work_df.iloc[-1]
        regime_res = classify_regime(
            work_df,
            {
                "trend_threshold": 0.45,
                "volatility_compression_threshold": 0.5,
                "volatility_expansion_threshold": 1.5,
            },
        )
        events = detect_liquidity_events(
            work_df,
            {"eqh_eql_threshold_points": 50, "sweep_lookback_bars": 80},
        )
        pat = get_pattern_signal(latest)
        pat_desc = ",".join(pat["patterns"]) if pat["patterns"] else "-"
        logger.info(
            f"[Cycle {cycle_num}] {symbol} - Regime={regime_res['regime']} conf={regime_res['confidence']:.2f} "
            f"events={len(events)} vol_ratio={latest.get('vol_ratio', 0):.2f} "
            f"net_power={latest.get('net_power', 0):.2f} patterns={pat_desc}"
        )

        context = {"symbol": symbol, "regime_result": regime_res}
        signal = select_and_generate_signal(work_df, context, events)
        if not signal:
            logger.info(f"[Cycle {cycle_num}] {symbol} - No signal")
            return

        if _in_cooldown(symbol, bar_time, cooldown_bars):
            reason = f"Cooldown active ({cooldown_bars} bars)"
            logger.info(f"[Cycle {cycle_num}] {symbol} - BLOCKED: {reason}")
            db.log_incident(symbol, reason, "BLOCKED")
            return

        logger.info(
            f"[Cycle {cycle_num}] {symbol} - SIGNAL {signal['side']} {signal['model']} "
            f"entry={signal['entry_price']:.2f} sl={signal['sl']:.2f} tp1={signal['tp1']:.2f} "
            f"conf={signal['confidence']:.2f}"
        )
        db.record_signal(signal)

        account_state = get_real_account_state(mt5)
        market_state = get_real_market_state(mt5, broker_symbol)
        logger.info(
            f"[Cycle {cycle_num}] {symbol} - equity=${account_state['equity']:.2f} "
            f"daily_pnl=${account_state['daily_pnl']:.2f} losses={account_state['consecutive_losses']} "
            f"spread={market_state['spread']}"
        )

        open_pos = count_open_positions(mt5, broker_symbol)
        if open_pos >= 2:
            reason = f"Max positions reached ({open_pos}/2)"
            logger.info(f"[Cycle {cycle_num}] {symbol} - BLOCKED: {reason}")
            db.log_incident(symbol, reason, "BLOCKED")
            return

        gate_res = risk_engine.risk_gate(signal, account_state, market_state)
        if not gate_res["allowed"]:
            logger.info(f"[Cycle {cycle_num}] {symbol} - BLOCKED by risk gate: {gate_res['reasons']}")
            db.log_incident(symbol, str(gate_res["reasons"]), "BLOCKED")
            return

        sl_dist = abs(signal["entry_price"] - signal["sl"])
        lot = compute_lot_size(account_state["equity"], 2.0, sl_dist)
        logger.info(f"[Cycle {cycle_num}] {symbol} - Executing {lot:.2f} lot")
        order_res = executor.place_order(signal, lot_size=lot)
        if order_res.get("status") == "ok":
            LAST_TRADE_TIME[symbol] = bar_time
        else:
            db.log_incident(symbol, f"Order error: {order_res}", "ERROR")

    except Exception as e:
        logger.error(f"[Cycle {cycle_num}] {symbol} - ERROR: {e}", exc_info=True)
        db.log_incident(symbol, str(e), "ERROR")


def main():
    args = parse_args()
    logger.info(f"Starting OPUS engine in {args.mode.upper()} mode")

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        symbols = CONFIG["symbols"]

    executor = Executor(mode=args.mode)

    if args.mode in ["live", "dry_run"]:
        if not fetcher.connect():
            logger.error("Failed to connect to MT5. Exiting.")
            return

        cycle = 0
        try:
            while True:
                cycle += 1
                logger.info("-" * 56)
                logger.info(f"CYCLE {cycle} | {time.strftime('%H:%M:%S')}")
                logger.info("-" * 56)

                for symbol in symbols:
                    tick_cycle(args.mode, symbol, executor, cycle, args.cooldown_bars)

                if args.mode == "live":
                    try:
                        manage_open_positions()
                    except Exception as e:
                        logger.error(f"Position manager error: {e}", exc_info=True)

                logger.info(f"Next cycle in {args.poll_seconds}s")
                time.sleep(max(1, args.poll_seconds))
        except KeyboardInterrupt:
            logger.info("Graceful shutdown initiated.")
        finally:
            fetcher.disconnect()
    else:
        logger.info("Backtest mode is handled by trader/scripts/run_backtest.py")


if __name__ == "__main__":
    main()
