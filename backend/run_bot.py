#!/usr/bin/env python3
"""
Antigravity AI Trading System — Entry Point (Thin Runner).

run_bot.py เป็นแค่ entry point → เรียก uvicorn.run() เท่านั้น.
ทุก component init อยู่ใน main.py lifespan (Single Source of Truth):
    1. SQLite connect
    2. Brain MemoryStore connect
    3. Strategy Factory auto-register
    4. MT5 Client connect (auto-connect to running terminal)
    5. MasterLoop spawn (background task)

Default: DRY_RUN mode.
"""

import sys
import os
import argparse
import subprocess
import uvicorn

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _kill_port(port: int) -> None:
    """Kill any process holding the given port (Windows only)."""
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.strip().split()
                pid = parts[-1]
                if pid.isdigit() and int(pid) != os.getpid():
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        capture_output=True, timeout=5
                    )
                    logger.info("port_freed", extra={"port": port, "killed_pid": int(pid)})
    except Exception as e:
        logger.debug("port_kill_skip", extra={"error": str(e)})


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Antigravity AI Trading System")
    parser.add_argument(
        "--mode", 
        type=str, 
        choices=["LIVE", "DRY_RUN", "REPLAY", "BACKTEST"],
        help="Override TRADING_MODE"
    )
    parser.add_argument(
        "--symbols", 
        type=str, 
        help="Override TRADING_SYMBOLS (e.g. 'XAUUSD,BTCUSD' or 'ALL')"
    )
    parser.add_argument(
        "--profile",
        type=str,
        help="Account profile name (e.g. 'cent_small') — loads from profiles/{name}.env"
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Run bot WITHOUT embedded API server (use separate uvicorn)"
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Override API_PORT to run the bot's API on a specific port"
    )
    return parser.parse_args()


def main() -> None:
    """Start Antigravity Trading System."""
    
    # ─── 0. Parse Args & Override Env ───
    args = parse_args()
    
    # Profile must be set FIRST (before config loads)
    if args.profile:
        os.environ["TRADING_PROFILE"] = args.profile
        print(f"📋 Profile: {args.profile} → profiles/{args.profile}.env")
    
    if args.mode:
        os.environ["TRADING_MODE"] = args.mode.upper()
        print(f"CLI Override: TRADING_MODE = {args.mode.upper()}")
        
    if args.symbols:
        os.environ["TRADING_SYMBOLS"] = args.symbols
        print(f"CLI Override: TRADING_SYMBOLS = {args.symbols}")

    if args.port:
        os.environ["API_PORT"] = str(args.port)
        print(f"CLI Override: API_PORT = {args.port}")

    settings = get_settings()
    logger.info("startup_begin", extra={
        "mode": settings.trading_mode,
        "profile": settings.profile_name,
        "api_port": settings.api_port,
        "symbols": settings.trading_symbols,
        "no_api": args.no_api,
    })

    if args.no_api:
        # ─── Bot-only mode: ไม่เปิด API → ให้ uvicorn แยกรัน ───
        import asyncio
        from app.api.main import _run_bot_standalone
        print(f"🤖 Bot-only mode (no API server) — use separate uvicorn for API")
        try:
            asyncio.run(_run_bot_standalone())
        except KeyboardInterrupt:
            logger.info("shutdown_requested")
        except Exception as e:
            logger.error("fatal_crash", extra={
                "error": str(e),
                "type": type(e).__name__,
            }, exc_info=True)
            sys.exit(1)
    else:
        # ─── Full mode: Bot + API in one process ───
        # NOTE: ไม่ต้องตั้ง custom SIGINT handler — uvicorn จัดการ graceful shutdown เอง
        try:
            _kill_port(settings.api_port)
            uvicorn.run(
                "app.api.main:app",
                host=settings.api_host,
                port=settings.api_port,
                log_level="warning",
                # Single worker — MasterLoop ต้องรันใน main process
                workers=1,
            )
        except KeyboardInterrupt:
            logger.info("shutdown_requested")
        except SystemExit:
            pass
        except Exception as e:
            logger.error("fatal_crash", extra={
                "error": str(e),
                "type": type(e).__name__,
            }, exc_info=True)
            sys.exit(1)


if __name__ == "__main__":
    main()
