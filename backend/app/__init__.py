"""
Antigravity AI Trading System — Application Package.

This is the root package for all trading system modules.
Sub-packages:
    core/       — Config, logging, time, errors, mode
    domain/     — Typed models and enums
    db/         — QuestDB, SQLite, DuckDB wrappers
    mt5/        — MetaTrader 5 client and market data
    risk/       — Pre-trade gate, sizing, guards, postfill, breakeven
    strategy/   — Strategy interface, factory, templates
    brain/      — AI memory, regime classifier, recommender
    execution/  — Unified execution pipeline, simulator, replay
    api/        — FastAPI application and routes
    observability/ — Audit, trace, metrics
    services/   — News filter, session detector
"""
__version__ = "0.1.0"
