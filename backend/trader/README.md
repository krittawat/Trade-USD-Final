# OPUS Trading Engine

Production-ready Python trading engine integrated with MetaTrader 5 (MT5), focused on aggressive smart-money hunting (liquidity sweeps + volatility expansion), with strict survival-first risk controls.

## Architecture & Module Responsibilities

- **/config**: `settings.json` and configurations for risk limits, regime thresholds, session hours, symbol mappings.
- **/data**: MT5 integration layer. Handles symbol mapping (`mapper.py`), data fetching with rate limits (`fetcher.py`), and time alignment/session labeling (`time_utils.py`).
- **/features**: Computes technical and price-action features from raw data. Includes volatility metrics (`volatility.py`) and market structure/BOS (`structure.py`).
- **/regime**: Classifies the current market state (Strong Trend, Weak Trend, Distribution, Accumulation, Compression, Expansion) with confidence scoring (`classifier.py`).
- **/liquidity**: Detects manipulation events such as EQH/EQL sweeps, structural displacements, and re-acceptances (`detector.py`).
- **/strategy**: Two core models (`trend_killer.py`, `liquidity_hunter.py`) and a strategy selector (`selector.py`) that uses regime and liquidity inputs to generate raw entry signals.
- **/strategy**: Includes directional trend, liquidity, and short-term pullback models. `rapid_pullback.py` is the intraday "buy the pullback / sell the bounce" module for short-horizon entries with tight ATR-based exits.
- **/risk**: The survival-first hard gate (`gate.py`). Validates all incoming signals against dynamic drawdowns, daily limits, sizing constraints, and news windows. Implements a mandatory kill switch.
- **/execution**: Translates approved signals into MT5 orders (`mt5_order.py`). Handles LIVE (via MT5), DRY_RUN (virtual logging), and BACKTEST logic.
- **/storage**: SQLite persistence layer (`sqlite_db.py`). Logs trades, daily stats, operational incidents, and versioned parameter sets. Ensures append-only safety.
- **/observability**: Contextual JSON logging (`logger.py`) to trace exactly why a trade was taken or blocked.

## Run Commands

**Live Mode:**
```bash
python main.py --mode live
```

**Dry-Run (Paper) Mode:**
```bash
python main.py --mode dry_run
```

**Backtest Mode:**
```bash
python main.py --mode backtest --symbol XAUUSD --start 2025-01-01 --end 2025-12-31
```

**Selector Backtest (single strategy):**
```bash
python -m backend.trader.scripts.run_backtest --symbol XAUUSD --timeframe M5 --days 20 --strategy rapid_pullback
```
