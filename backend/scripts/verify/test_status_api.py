"""
Test Status API — ทดสอบ endpoints ใหม่.

ทดสอบ:
    1. GET /api/status       — ตรวจว่ามี trend_analysis, active_strategies, tick_volume, patterns
    2. GET /api/status/live   — ตรวจ response เร็ว + per-symbol data
    3. WS  /ws/status         — connect + รับ message + disconnect

Usage:
    cd d:\\VibeCode\\Trade\\backend
    python scripts/verify/test_status_api.py
"""

import sys
import os
import json
import asyncio

# Fix Windows cp874 encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- Config ---
API_BASE = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws/status"

PASS = "[PASS]"
FAIL = "[FAIL]"
results = []


def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    results.append((name, condition))
    msg = f"  {status} {name}"
    if detail:
        msg += f" -- {detail}"
    print(msg)


async def test_full_status():
    """Test GET /api/status -- check new fields."""
    import httpx

    print("\n--- Test 1: GET /api/status ---")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{API_BASE}/api/status")

        check("HTTP 200", resp.status_code == 200, f"got {resp.status_code}")

        if resp.status_code != 200:
            # Try to show error detail
            try:
                print(f"  Response: {resp.text[:500]}")
            except Exception:
                pass
            return

        data = resp.json()

        # Basic fields
        check("bot_alive exists", "bot_alive" in data)
        check("mode exists", "mode" in data)
        check("kill_switch exists", "kill_switch" in data)

        # Enhanced last_decisions
        decisions = data.get("last_decisions", {})
        if decisions:
            first_sym = list(decisions.keys())[0]
            dec = decisions[first_sym]
            check("decision.strategy exists", "strategy" in dec, f"sym={first_sym}")
            check("decision.regime exists", "regime" in dec, f"sym={first_sym}")
            check("decision.session exists", "session" in dec, f"sym={first_sym}")
            check("decision.sl exists", "sl" in dec, f"sym={first_sym}")
            check("decision.tp exists", "tp" in dec, f"sym={first_sym}")
            check("decision.strategies_tried exists", "strategies_tried" in dec, f"sym={first_sym}")
        else:
            check("decisions populated", False, "no decisions yet (bot may not have run a cycle)")

        # New sections
        check("trend_analysis exists", "trend_analysis" in data)
        check("active_strategies exists", "active_strategies" in data)
        check("tick_volume exists", "tick_volume" in data)
        check("patterns exists", "patterns" in data)

        # Trend analysis detail
        trends = data.get("trend_analysis", {})
        if trends:
            first_sym = list(trends.keys())[0]
            t = trends[first_sym]
            check("trend.regime exists", "regime" in t, f"sym={first_sym}, regime={t.get('regime')}")
            check("trend.score exists", "score" in t, f"score={t.get('score')}")
            check("trend.actionable exists", "actionable" in t)
        else:
            check("trend_analysis populated", False, "no trend data yet")

        # Active strategies detail
        strats = data.get("active_strategies", {})
        if strats:
            first_sym = list(strats.keys())[0]
            s = strats[first_sym]
            check("strategy.strategy exists", "strategy" in s, f"strategy={s.get('strategy')}")
            check("strategy.candidates exists", "candidates" in s)

        print(f"\n  Symbols tracked: {data.get('symbols_tracked', [])}")
        print(f"  Strategies registered: {data.get('strategies_registered', 0)}")

    except Exception as e:
        check("API reachable", False, str(e))


async def test_live_status():
    """Test GET /api/status/live -- lightweight endpoint."""
    import httpx

    print("\n--- Test 2: GET /api/status/live ---")

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{API_BASE}/api/status/live")

        check("HTTP 200", resp.status_code == 200, f"got {resp.status_code}")

        if resp.status_code != 200:
            try:
                print(f"  Response: {resp.text[:500]}")
            except Exception:
                pass
            return

        data = resp.json()

        check("bot_alive exists", "bot_alive" in data)
        check("cycle exists", "cycle" in data, f"cycle={data.get('cycle')}")
        check("symbols exists", "symbols" in data)

        symbols = data.get("symbols", {})
        if symbols:
            first_sym = list(symbols.keys())[0]
            s = symbols[first_sym]
            check("symbol.trend exists", "trend" in s, f"trend={s.get('trend')}")
            check("symbol.strategy exists", "strategy" in s, f"strategy={s.get('strategy')}")
            check("symbol.pressure exists", "pressure" in s, f"pressure={s.get('pressure')}")
            check("symbol.actionable exists", "actionable" in s)
            check("symbol.confidence exists", "confidence" in s, f"confidence={s.get('confidence')}")
            print(f"\n  Live snapshot: {json.dumps(s, indent=2, ensure_ascii=False)}")
        else:
            check("symbols populated", False, "no symbol data yet")

    except Exception as e:
        check("Live API reachable", False, str(e))


async def test_websocket():
    """Test WebSocket /ws/status -- connect + receive."""
    print("\n--- Test 3: WebSocket /ws/status ---")

    try:
        import websockets
    except ImportError:
        check("websockets installed", False, "pip install websockets")
        return

    try:
        async with websockets.connect(WS_URL, close_timeout=5) as ws:
            check("WS connected", True)

            # Receive 2 messages
            for i in range(2):
                msg = await asyncio.wait_for(ws.recv(), timeout=10)
                data = json.loads(msg)
                check(f"WS message {i+1} received", True, f"cycle={data.get('cycle')}")
                if i == 0:
                    check("WS has bot_alive", "bot_alive" in data)
                    check("WS has symbols", "symbols" in data)
                    symbols = data.get("symbols", {})
                    if symbols:
                        first_sym = list(symbols.keys())[0]
                        s = symbols[first_sym]
                        check("WS symbol.trend", "trend" in s, f"trend={s.get('trend')}")
                        check("WS symbol.strategy", "strategy" in s, f"strategy={s.get('strategy')}")

            check("WS disconnect clean", True)

    except asyncio.TimeoutError:
        check("WS timeout", False, "no message within 10s")
    except Exception as e:
        check("WS connection", False, str(e))


async def main():
    print("=" * 60)
    print("  Status API Verification -- Trend + Strategy + WebSocket")
    print("=" * 60)

    await test_full_status()
    await test_live_status()
    await test_websocket()

    # Summary
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\n{'=' * 60}")
    print(f"  Result: {passed}/{total} passed")
    print(f"{'=' * 60}")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
