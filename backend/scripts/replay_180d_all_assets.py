#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
═══════════════════════════════════════════════════════════════════════
  ANTIGRAVITY 180-Day Multi-Asset Replay Engine
  📊 โหมด Replay สำหรับเทรนบอทและปรับกลยุทธ์ให้ดีขึ้น
  
  เป้าหมาย:
   - Replay 180 วัน ทุกคู่เทรด (XAUUSD, XAGUSD, BTCUSD, USOILm, US30m, USTECm)
   - วิเคราะห์โครงสร้างราคา (BOS, CHoCH, FVG, Candlestick Patterns)
   - ประเมินกลยุทธ์ทุกตัว ว่าตัวไหนทำกำไรในสถานการณ์ใด
   - ส่งผลลัพธ์กลับไปเทรนบอทที่ D:\VibeCode\Trade\backend\trader
═══════════════════════════════════════════════════════════════════════
"""
import sys
import os
import json
import time
import math
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

# ─── Path Setup ───
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

import numpy as np
import pandas as pd
import MetaTrader5 as mt5

# ─── Trader Pipeline Imports ───
from backend.trader.data.fetcher import fetcher
from backend.trader.features.volatility import add_volatility_features
from backend.trader.features.structure import add_structure_features, detect_displacement
from backend.trader.features.institutional import add_institutional_features
from backend.trader.features.divergence import detect_rsi_divergence
from backend.trader.features.candle_patterns import detect_candle_patterns, get_pattern_signal
from backend.trader.regime.classifier import classify_regime
from backend.trader.liquidity.detector import detect_liquidity_events
from backend.trader.strategy.selector import select_and_generate_signal, reset_cooldown
from backend.trader.observability.logger import setup_logger, C

# ─── Structure Analysis (New) ───
from app.analysis.structure import detect_structure

# ─── Logging ───
setup_logger()
logger = logging.getLogger("opus_logger")

# ═══════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════
REPLAY_DAYS = 180
SYMBOLS = ["XAUUSD", "XAGUSD", "BTCUSD", "USOILm", "US30m", "USTECm"]
TIMEFRAMES = ["M5", "M15", "H1"]  # Key TFs for strategy eval
INITIAL_EQUITY = 100.0  # USD

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1, "M3": mt5.TIMEFRAME_M3, "M5": mt5.TIMEFRAME_M5,
    "M6": mt5.TIMEFRAME_M6, "M12": mt5.TIMEFRAME_M12, "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H2": mt5.TIMEFRAME_H2,
    "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1,
}

TF_MINUTES = {
    "M1": 1, "M3": 3, "M5": 5, "M6": 6, "M10": 10, "M12": 12, "M15": 15,
    "M20": 20, "M30": 30, "H1": 60, "H2": 120, "H4": 240, "D1": 1440
}


# ═══════════════════════════════════════════════════════════════
# REPLAY ENGINE
# ═══════════════════════════════════════════════════════════════
class ReplayResult:
    """เก็บผลลัพธ์ของ Replay สำหรับแต่ละ Symbol+TF"""
    def __init__(self, symbol: str, tf: str):
        self.symbol = symbol
        self.tf = tf
        self.trades = []
        self.pattern_stats = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0.0})
        self.strategy_stats = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0.0})
        self.regime_stats = defaultdict(lambda: {"total": 0, "wins": 0})
        self.structure_events = defaultdict(int)


def simulate_trade_outcome(signal: dict, df: pd.DataFrame, bar_idx: int) -> dict:
    """
    จำลองผลลัพธ์ของเทรด โดยดูว่าราคาหลังสัญญาณไปถึง TP หรือ SL ก่อน.
    ใช้ข้อมูลจริงจาก MT5 (ไม่มี Future Leakage).
    """
    entry = signal.get("entry_price", 0)
    sl = signal.get("sl", 0)
    tp = signal.get("tp1", 0)
    side = signal.get("side", "BUY").upper()
    
    if entry <= 0 or sl <= 0 or tp <= 0:
        return {"outcome": "INVALID", "pnl": 0.0}
    
    # Look forward up to 100 bars
    max_bars = min(100, len(df) - bar_idx - 1)
    
    for j in range(1, max_bars + 1):
        future_bar = df.iloc[bar_idx + j]
        high = future_bar["high"]
        low = future_bar["low"]
        
        if side == "BUY":
            # Check SL first (conservative)
            if low <= sl:
                return {"outcome": "LOSS", "pnl": sl - entry, "bars_held": j}
            if high >= tp:
                return {"outcome": "WIN", "pnl": tp - entry, "bars_held": j}
        else:  # SELL
            if high >= sl:
                return {"outcome": "LOSS", "pnl": entry - sl, "bars_held": j}
            if low <= tp:
                return {"outcome": "WIN", "pnl": entry - tp, "bars_held": j}
    
    # Timeout - close at last known
    last_close = df.iloc[min(bar_idx + max_bars, len(df) - 1)]["close"]
    pnl = (last_close - entry) if side == "BUY" else (entry - last_close)
    return {"outcome": "TIMEOUT", "pnl": pnl, "bars_held": max_bars}


def replay_symbol(symbol: str, tf_str: str) -> ReplayResult:
    """
    Replay 180 วัน สำหรับ 1 symbol + 1 timeframe.
    ใช้ pipeline เดียวกับ live trading (tick_cycle logic).
    """
    result = ReplayResult(symbol, tf_str)
    tf = TIMEFRAME_MAP.get(tf_str, mt5.TIMEFRAME_M5)
    
    # Load 180 days of data from MT5
    tf_minutes = TF_MINUTES.get(tf_str, 5)
    bars_per_day = (24 * 60) // tf_minutes
    total_bars_needed = bars_per_day * REPLAY_DAYS
    # MT5 caps at ~10000 bars per call for some TFs
    bars_to_fetch = min(total_bars_needed, 10000)
    
    logger.info(f"📥 Loading {symbol} {tf_str}: {bars_to_fetch} bars ({REPLAY_DAYS}d)")
    
    from backend.trader.data.mapper import mapper
    broker_symbol = mapper.to_broker(symbol)
    
    rates = mt5.copy_rates_from_pos(broker_symbol, tf, 0, bars_to_fetch)
    if rates is None or len(rates) < 200:
        logger.warning(f"⚠️ {symbol} {tf_str}: ข้อมูลไม่เพียงพอ ({len(rates) if rates else 0} bars)")
        return result
    
    df = pd.DataFrame(rates)
    df['time_dt'] = pd.to_datetime(df['time'], unit='s')
    logger.info(f"✅ {symbol} {tf_str}: {len(df)} bars ({df['time_dt'].iloc[0].date()} → {df['time_dt'].iloc[-1].date()})")
    
    # Sliding window replay
    window_size = 200  # Need ≥200 bars for EMA-200 and features
    total_signals = 0
    
    # Iterate in steps (XAU M5 is 10k bars, keep it fast)
    step = 5 if tf_str == "M5" else 1 
    
    for i in range(window_size, len(df) - 100, step):
        # Get window
        window = df.iloc[i - window_size: i + 1].copy()
        
        # ─── Feature Engineering (Same as tick_cycle) ───
        try:
            work_df = add_volatility_features(window)
            work_df = add_structure_features(work_df)
            work_df = detect_displacement(work_df)
            work_df = add_institutional_features(work_df)
            work_df = detect_rsi_divergence(work_df)
            work_df = detect_candle_patterns(work_df)
        except Exception as e:
            continue
        
        # ─── Regime Classification ───
        regime_res = classify_regime(work_df, {
            "trend_threshold": 0.45,
            "volatility_compression_threshold": 0.5,
            "volatility_expansion_threshold": 1.5
        })
        regime_name = regime_res.get("regime", "UNKNOWN")
        
        # ─── Liquidity Events ───
        events = detect_liquidity_events(work_df, {
            "eqh_eql_threshold_points": 50,
            "sweep_lookback_bars": 80
        })
        
        # ─── Structure Analysis (New SMC module) ───
        structure = detect_structure(work_df)
        for key in ["bos_bull", "bos_bear", "choch_bull", "choch_bear"]:
            if structure.get(key):
                result.structure_events[key] += 1
        for key, val in structure.get("patterns", {}).items():
            if val:
                result.structure_events[f"pattern_{key}"] += 1
        
        # ─── HTF Trend ───
        htf_val = "BULLISH" if work_df.iloc[-1]["close"] > work_df.iloc[-1].get("ema_200", work_df.iloc[-1]["close"]) else "BEARISH"
        
        # ─── Strategy Selection ───
        context = {
            "symbol": symbol,
            "timeframe": tf_str,
            "regime_result": regime_res,
            "htf_ema_align": htf_val
        }
        
        signal = select_and_generate_signal(work_df, context, events, current_bar=i)
        
        if signal:
            total_signals += 1
            
            # ─── Simulate Trade Outcome ───
            outcome = simulate_trade_outcome(signal, df, i)
            
            model = signal.get("model", "UNKNOWN")
            confidence = signal.get("confidence", 0)
            is_win = outcome["outcome"] == "WIN"
            pnl = outcome.get("pnl", 0)
            
            trade_record = {
                "bar_idx": i,
                "time": str(df.iloc[i].get("time_dt", "")),
                "side": signal.get("side"),
                "model": model,
                "confidence": confidence,
                "entry": signal.get("entry_price"),
                "sl": signal.get("sl"),
                "tp": signal.get("tp1"),
                "outcome": outcome["outcome"],
                "pnl": pnl,
                "regime": regime_name,
                "structure_trend": structure.get("trend", ""),
                "bars_held": outcome.get("bars_held", 0),
            }
            
            # Enrich with structure tags
            tags = []
            if structure.get("fvg_bull"): tags.append("fvg_bull")
            if structure.get("fvg_bear"): tags.append("fvg_bear")
            if structure.get("bos_bull"): tags.append("bos_bull")
            if structure.get("bos_bear"): tags.append("bos_bear")
            if structure.get("choch_bull"): tags.append("choch_bull")
            if structure.get("choch_bear"): tags.append("choch_bear")
            for pk, pv in structure.get("patterns", {}).items():
                if pv: tags.append(pk)
            trade_record["tags"] = tags
            
            result.trades.append(trade_record)
            
            # ─── Update Stats ───
            result.strategy_stats[model]["total"] += 1
            result.strategy_stats[model]["pnl"] += pnl
            if is_win:
                result.strategy_stats[model]["wins"] += 1
            
            result.regime_stats[regime_name]["total"] += 1
            if is_win:
                result.regime_stats[regime_name]["wins"] += 1
            
            for tag in tags:
                result.pattern_stats[tag]["total"] += 1
                result.pattern_stats[tag]["pnl"] += pnl
                if is_win:
                    result.pattern_stats[tag]["wins"] += 1
            
            # Reset cooldown after each signal to allow next signal
            reset_cooldown()
    
    logger.info(f"📊 {symbol} {tf_str}: {total_signals} signals generated")
    return result


def print_report(all_results: list):
    """พิมพ์รายงานสรุปผลลัพธ์ Replay"""
    print(f"\n{'═' * 80}")
    print(f"  🏆 ANTIGRAVITY 180-Day Replay Report")
    print(f"  📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═' * 80}\n")
    
    # ─── Per-Symbol Summary ───
    for r in all_results:
        if not r.trades:
            continue
        
        total = len(r.trades)
        wins = sum(1 for t in r.trades if t["outcome"] == "WIN")
        losses = sum(1 for t in r.trades if t["outcome"] == "LOSS")
        total_pnl = sum(t["pnl"] for t in r.trades)
        wr = (wins / total * 100) if total > 0 else 0
        
        gross_profit = sum(t["pnl"] for t in r.trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in r.trades if t["pnl"] < 0))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else 999
        
        print(f"{'─' * 60}")
        print(f"  📈 {r.symbol} ({r.tf})")
        print(f"  Trades: {total} │ Win: {wins} │ Loss: {losses} │ WR: {wr:.1f}%")
        print(f"  PnL: {total_pnl:+.2f} │ PF: {pf:.2f}")
        
        # Strategy breakdown
        if r.strategy_stats:
            print(f"\n  📋 Strategy Performance:")
            sorted_strats = sorted(r.strategy_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)
            for model, stats in sorted_strats:
                s_wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0
                emoji = "🟢" if stats["pnl"] > 0 else "🔴"
                print(f"    {emoji} {model:30s} │ {stats['total']:3d} trades │ WR {s_wr:5.1f}% │ PnL {stats['pnl']:+8.2f}")
        
        # Pattern effectiveness
        if r.pattern_stats:
            print(f"\n  🕯️ Pattern Effectiveness:")
            sorted_pats = sorted(r.pattern_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)
            for pat, stats in sorted_pats[:10]:
                p_wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0
                emoji = "✅" if stats["pnl"] > 0 else "❌"
                print(f"    {emoji} {pat:20s} │ {stats['total']:3d} signals │ WR {p_wr:5.1f}% │ PnL {stats['pnl']:+8.2f}")
        
        # Regime breakdown
        if r.regime_stats:
            print(f"\n  🌡️ Regime Breakdown:")
            for regime, stats in r.regime_stats.items():
                r_wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0
                print(f"    {regime:25s} │ {stats['total']:3d} trades │ WR {r_wr:5.1f}%")
        
        # Structure events count
        if r.structure_events:
            print(f"\n  🏗️ Structure Events (180d):")
            for event, count in sorted(r.structure_events.items(), key=lambda x: -x[1]):
                print(f"    {event:25s} │ {count:5d} occurrences")
        
        print()
    
    # ─── Grand Summary ───
    all_trades = [t for r in all_results for t in r.trades]
    if all_trades:
        total = len(all_trades)
        wins = sum(1 for t in all_trades if t["outcome"] == "WIN")
        total_pnl = sum(t["pnl"] for t in all_trades)
        wr = (wins / total * 100) if total > 0 else 0
        
        print(f"\n{'═' * 80}")
        print(f"  🏆 GRAND TOTAL: {total} trades │ WR: {wr:.1f}% │ PnL: {total_pnl:+.2f}")
        print(f"{'═' * 80}\n")
        
        # Global best strategies
        global_strats = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0.0})
        for r in all_results:
            for model, stats in r.strategy_stats.items():
                global_strats[model]["total"] += stats["total"]
                global_strats[model]["wins"] += stats["wins"]
                global_strats[model]["pnl"] += stats["pnl"]
        
        print("  🏅 Top Strategies (All Assets):")
        sorted_global = sorted(global_strats.items(), key=lambda x: x[1]["pnl"], reverse=True)
        for idx, (model, stats) in enumerate(sorted_global[:15]):
            s_wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0
            medal = "🥇" if idx == 0 else "🥈" if idx == 1 else "🥉" if idx == 2 else "  "
            print(f"  {medal} {model:35s} │ {stats['total']:4d} trades │ WR {s_wr:5.1f}% │ PnL {stats['pnl']:+10.2f}")


def save_results_incremental(result: ReplayResult):
    """บันทึกผลลัพธ์ Replay ลง SQLite ทีละตัว (Incremental)"""
    db_path = Path("d:/VibeCode/Trade/backend/trader/data/replay_180d.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS replay_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, timeframe TEXT, bar_idx INTEGER, trade_time TEXT,
            side TEXT, model TEXT, confidence REAL,
            entry REAL, sl REAL, tp REAL,
            outcome TEXT, pnl REAL, regime TEXT,
            structure_trend TEXT, bars_held INTEGER,
            tags TEXT, replay_date TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS replay_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT, timeframe TEXT, total_trades INTEGER,
            wins INTEGER, losses INTEGER, win_rate REAL, total_pnl REAL,
            profit_factor REAL, best_strategy TEXT, replay_date TEXT
        )
    """)
    
    replay_date = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Save Trades
    for t in result.trades:
        conn.execute("""
            INSERT INTO replay_trades
            (symbol, timeframe, bar_idx, trade_time, side, model, confidence,
                entry, sl, tp, outcome, pnl, regime, structure_trend, bars_held, tags, replay_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            result.symbol, result.tf, t["bar_idx"], t.get("time", ""), t["side"], t["model"],
            t["confidence"], t["entry"], t["sl"], t["tp"],
            t["outcome"], t["pnl"], t["regime"], t.get("structure_trend", ""),
            t.get("bars_held", 0), json.dumps(t.get("tags", [])), replay_date
        ))
    
    # Save Summary
    total = len(result.trades)
    if total > 0:
        wins = sum(1 for t in result.trades if t["outcome"] == "WIN")
        losses = total - wins
        wr = wins / total * 100
        total_pnl = sum(t["pnl"] for t in result.trades)
        gp = sum(t["pnl"] for t in result.trades if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in result.trades if t["pnl"] < 0))
        pf = gp / gl if gl > 0 else 999
        best = max(result.strategy_stats.items(), key=lambda x: x[1]["pnl"])[0] if result.strategy_stats else "N/A"
        
        conn.execute("""
            INSERT INTO replay_summary
            (symbol, timeframe, total_trades, wins, losses, win_rate, total_pnl,
                profit_factor, best_strategy, replay_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (result.symbol, result.tf, total, wins, losses, wr, total_pnl, pf, best, replay_date))
    
    conn.commit()
    conn.close()
    logger.info(f"💾 Results saved for {result.symbol} {result.tf}")


def main():
    """Main entry point."""
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  🚀 ANTIGRAVITY 180-Day Multi-Asset Replay Engine           ║
║  📊 Symbols: {', '.join(SYMBOLS):43s}║
║  ⏱️  TFs: {', '.join(TIMEFRAMES):47s}║
║  📅 Period: {REPLAY_DAYS} days                                        ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    # Connect MT5
    if not mt5.initialize():
        logger.error("❌ MT5 initialization failed!")
        sys.exit(1)
    
    logger.info(f"✅ MT5 connected: {mt5.account_info().login}")
    
    all_results = []
    total_combos = len(SYMBOLS) * len(TIMEFRAMES)
    done = 0
    
    for symbol in SYMBOLS:
        for tf_str in TIMEFRAMES:
            done += 1
            logger.info(f"\n{'─' * 60}")
            logger.info(f"🔄 [{done}/{total_combos}] Replaying {symbol} {tf_str}...")
            
            try:
                result = replay_symbol(symbol, tf_str)
                all_results.append(result)
                
                # Save Incremental
                save_results_incremental(result)
                
                # Quick summary
                if result.trades:
                    wins = sum(1 for t in result.trades if t["outcome"] == "WIN")
                    wr = wins / len(result.trades) * 100 if result.trades else 0
                    pnl = sum(t["pnl"] for t in result.trades)
                    logger.info(f"✅ {symbol} {tf_str}: {len(result.trades)} trades │ WR: {wr:.1f}% │ PnL: {pnl:+.2f}")
                else:
                    logger.info(f"⚪ {symbol} {tf_str}: No signals generated")
            except Exception as e:
                logger.error(f"❌ {symbol} {tf_str} failed: {e}", exc_info=True)
    
    # Print full report
    print_report(all_results)
    
    # Save to DB for AI Brain
    save_results_to_db(all_results)
    
    mt5.shutdown()
    logger.info("🏁 180-Day Replay Complete!")


if __name__ == "__main__":
    main()
