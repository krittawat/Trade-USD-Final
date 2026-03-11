import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from backend.trader.brain.professional_guard import apply_professional_guard


class _FakeProvider:
    def __init__(self, live=None, shadow=None):
        self._live = live or {}
        self._shadow = shadow or {}

    def get_live_stats(self, symbol: str, model: str, days: int):
        return dict(self._live.get((symbol, model), {}))

    def get_shadow_stats(self, symbol: str, model: str, lookback: int):
        return dict(self._shadow.get((symbol, model), {}))


def _signal(**overrides):
    signal = {
        "symbol": "XAUUSD",
        "model": "RAPID_PULLBACK",
        "side": "BUY",
        "confidence": 0.79,
        "rr": 1.6,
        "rationale": [],
    }
    signal.update(overrides)
    return signal


def test_professional_guard_blocks_cold_live_model_without_elite_override():
    provider = _FakeProvider(
        live={
            ("XAUUSD", "RAPID_PULLBACK"): {
                "trades": 8,
                "win_rate": 0.25,
                "profit_factor": 0.62,
                "net_pnl": -18.0,
            }
        }
    )

    out = apply_professional_guard(
        [_signal()],
        {"symbol": "XAUUSD", "session": "LONDON"},
        perf_provider=provider,
        config={"enabled": True},
    )

    assert out == []


def test_professional_guard_allows_elite_override_against_cold_live_stats():
    provider = _FakeProvider(
        live={
            ("XAUUSD", "RAPID_PULLBACK"): {
                "trades": 8,
                "win_rate": 0.25,
                "profit_factor": 0.62,
                "net_pnl": -18.0,
            }
        }
    )

    out = apply_professional_guard(
        [_signal(confidence=0.88, rr=2.1)],
        {"symbol": "XAUUSD", "session": "LONDON"},
        perf_provider=provider,
        config={"enabled": True},
    )

    assert len(out) == 1
    assert any("override" in reason.lower() for reason in out[0]["rationale"])


def test_professional_guard_boosts_hot_live_model():
    provider = _FakeProvider(
        live={
            ("USOIL", "USOIL_ELITE"): {
                "trades": 7,
                "win_rate": 0.71,
                "profit_factor": 1.52,
                "net_pnl": 24.0,
            }
        }
    )

    out = apply_professional_guard(
        [_signal(symbol="USOIL", model="USOIL_ELITE", confidence=0.74, rr=1.9)],
        {"symbol": "USOIL", "session": "NY"},
        perf_provider=provider,
        config={"enabled": True, "live_hot_confidence_boost": 0.03},
    )

    assert len(out) == 1
    assert out[0]["confidence"] > 0.74
