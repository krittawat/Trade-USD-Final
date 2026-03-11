import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from backend.trader.data.mapper import SymbolMapper
from backend.trader.execution.position_manager import (
    _resolve_min_sl_move,
    compute_atr_tp,
)


class _SymbolInfo:
    def __init__(
        self,
        *,
        digits: int,
        point: float,
        trade_tick_size: float,
        trade_stops_level: int = 0,
    ):
        self.digits = digits
        self.point = point
        self.trade_tick_size = trade_tick_size
        self.trade_stops_level = trade_stops_level


def test_symbol_mapper_prefers_canonical_standard_for_duplicate_broker_symbol(tmp_path):
    config_path = tmp_path / "settings.json"
    config_path.write_text(
        json.dumps({"symbols": {"USOIL": "USOILm", "USOILm": "USOILm"}}),
        encoding="utf-8",
    )

    mapper = SymbolMapper(config_path)

    assert mapper.to_standard("USOILm") == "USOIL"


def test_compute_atr_tp_respects_symbol_precision_and_full_stop_distance():
    sym_info = _SymbolInfo(digits=5, point=0.00001, trade_tick_size=0.00001)

    tp = compute_atr_tp(1.23456, "BUY", 0.00100, sym_info)

    assert tp["tp1"] == 1.23906
    assert tp["tp2"] == 1.24356
    assert tp["tp3"] == 1.24956


def test_resolve_min_sl_move_scales_down_for_fx_symbols():
    sym_info = _SymbolInfo(
        digits=5,
        point=0.00001,
        trade_tick_size=0.00001,
        trade_stops_level=10,
    )

    min_move = _resolve_min_sl_move(
        "EURUSD",
        0.0008,
        sym_info,
        {"min_sl_move": 0.5, "atr_multiplier": 2.5, "ghost_buffer_pct": 0.2},
    )

    assert min_move < 0.001
    assert min_move >= 0.0001
