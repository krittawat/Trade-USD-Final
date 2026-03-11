# -*- coding: utf-8 -*-
"""
Symbol Threshold Tuner
----------------------
Auto-adjusts per-symbol selector thresholds from REAL closed trades (rolling window).
No per-symbol hardcoded values are required.
"""

import json
import logging
from typing import Dict

from backend.trader.config.paths import SETTINGS_PATH
from backend.trader.data.mapper import mapper
from backend.trader.storage.sqlite_db import db

logger = logging.getLogger("opus_logger")

with open(SETTINGS_PATH, "r", encoding="utf-8") as _f:
    _CFG = json.load(_f)


class SymbolThresholdTuner:
    def __init__(self):
        strategy_cfg = _CFG.get("strategy", {})
        opus_cfg = _CFG.get("opus_governor", {})
        symbols_cfg = _CFG.get("symbols", {})
        tuner_cfg = _CFG.get("symbol_tuner", {})

        self.lookback_days = int(tuner_cfg.get("lookback_days", 360))
        self.min_trades_for_tune = int(tuner_cfg.get("min_trades_for_tune", 12))
        self.refresh_interval_cycles = int(tuner_cfg.get("refresh_interval_cycles", 10))
        self.snapshot_retention_days = int(tuner_cfg.get("snapshot_retention_days", 400))
        self.prune_interval_cycles = int(tuner_cfg.get("prune_interval_cycles", 72))

        self.base_confidence = float(strategy_cfg.get("min_confidence_trade", 0.60))
        self.base_rr = float(opus_cfg.get("rr_minimum", 1.20))
        self.base_cooldown_bars = int(strategy_cfg.get("trade_cooldown_bars", 6))
        self.symbol_universe = self._normalize_symbols(list(symbols_cfg.keys()))

        self._profile_cache: Dict[str, Dict] = {}
        self._stats_cache: Dict[str, Dict] = {}
        self._last_refresh_cycle = -1
        self._last_prune_cycle = -1

    def _normalize_symbols(self, symbols) -> list[str]:
        out = []
        seen = set()
        for sym in symbols or []:
            std = self._to_standard_symbol(sym)
            if not std or std in seen:
                continue
            out.append(std)
            seen.add(std)
        return out

    def _to_standard_symbol(self, symbol: str) -> str:
        raw = str(symbol or "").strip()
        if not raw:
            return ""
        std = mapper.to_standard(raw)
        if std == raw and raw.endswith(("m", "c", "M", "C")):
            std = raw[:-1]
        return std.upper()

    def set_symbol_universe(self, symbols) -> None:
        normalized = self._normalize_symbols(symbols)
        if normalized:
            self.symbol_universe = normalized

    def _default_profile(self) -> Dict:
        return {
            "min_confidence": self.base_confidence,
            "min_rr": self.base_rr,
            "cooldown_bars": self.base_cooldown_bars,
            "source": "default",
            "trades_360d": 0,
            "win_rate_360d": 0.0,
            "profit_factor_360d": 0.0,
            "net_pnl_360d": 0.0,
        }

    def _build_profile(self, stats: Dict) -> Dict:
        profile = self._default_profile()
        if not stats:
            return profile

        trades = int(stats.get("trades", 0) or 0)
        win_rate = float(stats.get("win_rate", 0.0) or 0.0)
        pf = float(stats.get("profit_factor", 0.0) or 0.0)
        net_pnl = float(stats.get("net_pnl", 0.0) or 0.0)
        wins = int(stats.get("wins", 0) or 0)
        losses = int(stats.get("losses", 0) or 0)

        profile["trades_360d"] = trades
        profile["win_rate_360d"] = win_rate
        profile["profit_factor_360d"] = pf
        profile["net_pnl_360d"] = net_pnl

        if trades < self.min_trades_for_tune:
            return profile

        conf = self.base_confidence
        rr = self.base_rr
        cooldown = self.base_cooldown_bars

        wr_edge = win_rate - 0.50
        pf_edge = max(-0.50, min(0.80, pf - 1.00))
        quality = (wr_edge * 1.8) + (pf_edge * 0.35)

        if quality <= -0.30:
            conf += 0.07
            rr += 0.18
            cooldown += 2
        elif quality <= -0.15:
            conf += 0.04
            rr += 0.10
            cooldown += 1
        elif quality >= 0.35:
            conf -= 0.04
            rr -= 0.12
            cooldown -= 2
        elif quality >= 0.18:
            conf -= 0.02
            rr -= 0.06
            cooldown -= 1

        if losses > wins and net_pnl < 0:
            conf += 0.02
            rr += 0.05
            cooldown += 1

        profile["min_confidence"] = max(0.55, min(0.90, conf))
        profile["min_rr"] = max(1.00, min(2.20, rr))
        profile["cooldown_bars"] = max(3, min(14, int(cooldown)))
        profile["source"] = "auto_tuned"
        return profile

    def refresh(self, current_cycle: int = 0, force: bool = False) -> Dict[str, Dict]:
        if (
            not force
            and current_cycle > 0
            and self._last_refresh_cycle >= 0
            and (current_cycle - self._last_refresh_cycle) < self.refresh_interval_cycles
        ):
            return self._profile_cache

        self._last_refresh_cycle = current_cycle

        try:
            rows = db.get_symbol_performance(days=self.lookback_days, min_trades=0)
            stats_map = {self._to_standard_symbol(r.get("symbol", "")): r for r in rows}
            self._stats_cache = stats_map

            profiles = {}
            for sym in self.symbol_universe:
                std = self._to_standard_symbol(sym)
                profiles[std] = self._build_profile(stats_map.get(std))
            for std, st in stats_map.items():
                if std and std not in profiles:
                    profiles[std] = self._build_profile(st)
            for std, profile in profiles.items():
                db.upsert_symbol_tuner_snapshot(
                    symbol=std,
                    stats=stats_map.get(std, {}),
                    profile=profile,
                    lookback_days=self.lookback_days,
                )

            self._profile_cache = profiles

            tracked = [s for s in rows if int(s.get("trades", 0) or 0) > 0]
            if tracked:
                top = sorted(tracked, key=lambda x: x.get("trades", 0), reverse=True)[:4]
                msg = ", ".join(
                    f"{self._to_standard_symbol(x['symbol'])}:WR={x['win_rate']:.0%},N={x['trades']},PF={x['profit_factor']:.2f}"
                    for x in top
                )
                logger.info(f"  📈 360D Winrate Stats: {msg}")

            should_prune = (
                force
                or self._last_prune_cycle < 0
                or current_cycle <= 0
                or (current_cycle - self._last_prune_cycle) >= self.prune_interval_cycles
            )
            if should_prune:
                deleted = db.prune_symbol_tuner_snapshots(self.snapshot_retention_days)
                self._last_prune_cycle = current_cycle
                if deleted > 0:
                    logger.info(
                        f"  🧹 Symbol tuner snapshots pruned: {deleted} rows "
                        f"(retention={self.snapshot_retention_days}d)"
                    )

            return self._profile_cache
        except Exception as e:
            logger.error(f"SYMBOL TUNER refresh error: {e}", exc_info=True)
            return self._profile_cache

    def get_profile(self, symbol: str) -> Dict:
        std = self._to_standard_symbol(symbol)
        if not std:
            return self._default_profile()
        return self._profile_cache.get(std, self._default_profile())

    def get_symbol_stats(self, symbol: str) -> Dict:
        std = self._to_standard_symbol(symbol)
        return self._stats_cache.get(std, {})


symbol_tuner = SymbolThresholdTuner()
