"""
Strategy Factory — DB-Driven Strategy Selection.

All data pulled from DB:
    1. strategy_registry   — strategy list + asset_class + regimes + priority
    2. backtest_routing    — (symbol, regime) → best strategy (highest score)
    3. brain recommendation → from AI Brain (optional)

No hardcode — change strategies via DB / seed script / API only.

Components:
    StrategyFactory    — select/create/manage strategies
"""

from typing import Optional, TYPE_CHECKING
if TYPE_CHECKING:
    from app.brain.personality import PersonalityProfile

import json
import importlib
import os
import time

import pandas as pd

from app.core.logging import get_logger
from app.domain.enums import Action, RegimeType
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy

logger = get_logger(__name__)

# Shadow routing constants
SHADOW_MIN_SIGNALS = 10       # Minimum shadow signals before trusting
SHADOW_MIN_WIN_RATE = 55.0    # Minimum win_rate % to consider
SHADOW_CACHE_TTL = 300        # Refresh every 5 minutes

# Backtest routing production gate (env-overridable)
ROUTING_MIN_TRADES = int(os.getenv("ROUTING_MIN_TRADES", "30"))
ROUTING_MIN_WIN_RATE = float(os.getenv("ROUTING_MIN_WIN_RATE", "40"))
ROUTING_MIN_PROFIT_FACTOR = float(os.getenv("ROUTING_MIN_PROFIT_FACTOR", "1.05"))
ROUTING_MAX_DRAWDOWN_PCT = float(os.getenv("ROUTING_MAX_DRAWDOWN_PCT", "25"))
ROUTING_MIN_SCORE = float(os.getenv("ROUTING_MIN_SCORE", "0"))


# ====================================================================
# Asset Class Detection (pure function — no hardcoding of strategies)
# ====================================================================

_ASSET_CLASS_RULES = {
    "gold": ["XAU", "GOLD"],
    "silver": ["XAG", "SILVER"],
    "crypto": ["BTC", "ETH", "LTC", "XRP", "DOGE", "SOL", "BNB", "ADA"],
}

_FOREX_MARKERS = ["EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "USD"]


def detect_asset_class(symbol: str) -> str:
    """Detect asset class from symbol name. Returns: gold/silver/forex/crypto/*"""
    sym = symbol.upper()
    for asset_class, markers in _ASSET_CLASS_RULES.items():
        if any(m in sym for m in markers):
            return asset_class
    # Forex: any two currency codes
    matches = sum(1 for m in _FOREX_MARKERS if m in sym)
    if matches >= 2:
        return "forex"
    return "*"


def _normalize_symbol_lookup(symbol: str) -> str:
    """Normalize broker suffix variations like XAUUSDc/XAUUSDm -> XAUUSD."""
    return str(symbol or "").strip().upper().rstrip("CM.")


def _normalize_strategy_name(name: str) -> str:
    """Normalize strategy key for robust matching (vfinal == V-FINAL)."""
    if not name:
        return ""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


class StrategyFactory:
    """
    DB-Driven Strategy Factory — selects strategies from DB.

    Usage:
        factory = StrategyFactory()
        factory.auto_register(db=sqlite_store)
        decision = factory.get_decision(candles, profile, regime)

    Selection priority:
        1. AI Brain recommendation
        2. backtest_routing DB  — (symbol, regime) → highest score
        3. strategy_registry DB — (asset_class, regime) → highest priority
        4. Default fallback
    """

    def __init__(self) -> None:
        self._strategies: dict[str, BaseStrategy] = {}  # name → instance
        self._strategy_aliases: dict[str, str] = {}      # normalized name -> canonical key
        self._default: str | None = None                 # default strategy name
        # DB-driven caches (loaded from SQLite)
        self._routing_cache: dict[str, dict] = {}       # "SYMBOL:REGIME" → routing row
        self._registry_cache: list[dict] = []            # strategy_registry rows
        self._db = None                                  # SQLiteStore reference
        # Shadow routing cache (live performance from shadow trades)
        self._shadow_routing: dict[str, dict] = {}      # "SYMBOL:REGIME" → {strategy, win_rate, signals}
        self._shadow_confidence: dict[str, dict] = {}   # "STRATEGY:SYMBOL" → {win_rate, signals}
        self._shadow_cache_time: float = 0               # last refresh timestamp

    # ────────────────────────────────────────────────────────────────
    # Auto Register — scan DB registry then register strategies
    # ────────────────────────────────────────────────────────────────

    def auto_register(self, db=None) -> int:
        """
        Register strategies from DB (strategy_registry table).

        Fallback: if DB is empty → scan Python modules in templates/.
        Auto-seed: after scan, persist discovered strategies to DB
        so next startup loads from registry directly (faster).

        Args:
            db: SQLiteStore instance (optional, for loading registry + routing)

        Returns:
            int — number of strategies successfully registered
        """
        self._db = db
        registered = 0

        # ── 1. Load registry from DB ──
        registry_rows = []
        if db:
            try:
                registry_rows = db.get_strategy_registry(active_only=True)
                self._registry_cache = registry_rows
                logger.info("strategy_registry_loaded", extra={
                    "count": len(registry_rows),
                })
            except Exception as e:
                logger.warning("strategy_registry_load_failed", extra={"error": str(e)})

        # ── 2. Register strategies from DB registry ──
        if registry_rows:
            for row in registry_rows:
                try:
                    reg_count = self._register_from_registry(row)
                    registered += reg_count
                except Exception as e:
                    logger.warning("strategy_register_from_db_failed", extra={
                        "strategy": row.get("strategy_name", "?"),
                        "error": str(e),
                        "module_path": row.get("module_path", "?"),
                        "class_name": row.get("class_name", "?"),
                    })
        else:
            # Fallback: scan Python modules when DB is empty
            registered = self._scan_template_modules()

            # ── Auto-seed: persist discovered strategies to DB ──
            # so next startup loads from registry directly (no scan needed)
            if db and registered > 0:
                self._auto_seed_registry(db)

        # ── 2.5 Always scan templates for NEW strategies not yet in DB ──
        # This ensures newly added .py files are discovered without
        # needing to manually insert into strategy_registry table.
        scan_count = self._scan_template_modules()
        if scan_count > 0:
            logger.info("new_strategies_discovered", extra={
                "count": scan_count,
                "strategies": list(self._strategies.keys()),
            })
            registered += scan_count
            # Auto-seed newly found strategies to DB
            if db:
                self._auto_seed_registry(db)

        # ── 3. Also scan per-pair strategies ──
        registered += self._scan_pair_modules()

        # ── 4. Load backtest routing cache ──
        if db:
            self._load_routing_cache(db)

        logger.info("auto_register_complete", extra={
            "total_registered": registered,
            "strategies": list(self._strategies.keys()),
            "routing_entries": len(self._routing_cache),
        })
        return registered

    def _auto_seed_registry(self, db) -> None:
        """
        Auto-seed strategy_registry from discovered strategies.

        Called after the fallback module scan when DB registry is empty.
        Persists all registered strategies so next startup skips scanning.
        """
        from app.strategy.templates import _SEED_REGISTRY
        # Build case-insensitive lookup of registered strategies
        registered_lower = {k.lower(): k for k in self._strategies}
        seeded = 0
        for entry in _SEED_REGISTRY:
            try:
                name = entry["strategy_name"]
                # Match case-insensitively (e.g. GOLD_SCALP_PRO ↔ gold_scalp_pro)
                if name.lower() not in registered_lower:
                    continue
                db.save_strategy_registry(
                    strategy_name=name,
                    class_name=entry["class_name"],
                    module_path=entry["module_path"],
                    timeframe=entry.get("timeframe", "M5"),
                    asset_class=entry.get("asset_class", "*"),
                    suitable_regimes=entry.get("suitable_regimes", []),
                    priority=entry.get("priority", 50),
                    is_active=True,
                )
                seeded += 1
            except Exception as e:
                logger.warning("auto_seed_registry_failed", extra={
                    "strategy": entry.get("strategy_name", "?"),
                    "error": str(e),
                })
        if seeded > 0:
            # Reload cache after seeding
            self._registry_cache = db.get_strategy_registry(active_only=True)
            logger.info("strategy_registry_auto_seeded", extra={
                "seeded": seeded,
                "total_registry": len(self._registry_cache),
            })

    def _register_from_registry(self, row: dict) -> int:
        """Register a strategy from a DB registry row."""
        module_path = row["module_path"]
        class_name = row["class_name"]

        mod = importlib.import_module(module_path)
        cls = getattr(mod, class_name)
        instance = cls()
        self.register(instance)
        logger.info("strategy_from_db_registered", extra={
            "strategy": row["strategy_name"],
            "asset_class": row.get("asset_class", "*"),
            "priority": row.get("priority", 50),
        })
        return 1

    def _scan_template_modules(self) -> int:
        """Fallback: scan app/strategy/templates/ for BaseStrategy subclasses."""
        registered = 0
        templates_dir = os.path.join(os.path.dirname(__file__), "templates")
        if not os.path.exists(templates_dir):
            return 0

        for fname in os.listdir(templates_dir):
            if not fname.endswith(".py") or fname.startswith("_") or fname == "base_strategy.py":
                continue
            module_name = fname[:-3]
            try:
                mod = importlib.import_module(f"app.strategy.templates.{module_name}")
                for attr_name in dir(mod):
                    obj = getattr(mod, attr_name)
                    if (isinstance(obj, type)
                            and issubclass(obj, BaseStrategy)
                            and obj is not BaseStrategy
                            and hasattr(obj, "name")
                            and obj.name != "base"):
                        instance = obj()
                        if instance.name not in self._strategies:
                            self.register(instance)
                            registered += 1
            except Exception as e:
                logger.warning("strategy_template_failed", extra={
                    "module_name": module_name, "error": str(e),
                })
        return registered

    def _scan_pair_modules(self) -> int:
        """Scan app/strategy/pairs/<SYMBOL>/strategy.py."""
        registered = 0
        pairs_dir = os.path.join(os.path.dirname(__file__), "pairs")
        if not os.path.exists(pairs_dir):
            return 0

        for item in os.listdir(pairs_dir):
            pair_path = os.path.join(pairs_dir, item)
            if os.path.isdir(pair_path):
                try:
                    mod = importlib.import_module(f"app.strategy.pairs.{item}.strategy")
                    for name in dir(mod):
                        if name.endswith("Strategy") and name != "BaseStrategy":
                            cls = getattr(mod, name)
                            instance = cls()
                            self.register(instance)
                            registered += 1
                            break
                except ImportError:
                    pass
                except Exception as e:
                    logger.warning("pair_scan_failed", extra={
                        "symbol": item, "error": str(e),
                    })
        return registered

    def _load_routing_cache(self, db) -> None:
        """Load backtest_routing into memory cache for fast lookup."""
        try:
            rows = db.get_backtest_routing()
            self._routing_cache.clear()
            for row in rows:
                key = f"{row['symbol']}:{row['regime']}"
                # Only cache the highest-score entry per (symbol, regime)
                if key not in self._routing_cache or row.get("score", 0) > self._routing_cache[key].get("score", 0):
                    self._routing_cache[key] = row
            if self._routing_cache:
                logger.info("routing_cache_loaded", extra={
                    "entries": len(self._routing_cache),
                })
            else:
                logger.info("router_empty", extra={
                    "detail": "No routing data → run backtest to populate",
                })
        except Exception as e:
            logger.warning("routing_cache_failed", extra={"error": str(e)})

    def reload_routing(self) -> None:
        """Reload routing cache from DB (call after backtest updates)."""
        if self._db:
            self._load_routing_cache(self._db)

    # ────────────────────────────────────────────────────────────────
    # Register — register a single strategy
    # ────────────────────────────────────────────────────────────────

    def _resolve_strategy_name(self, name: str | None) -> str | None:
        """Resolve strategy by exact key, lowercase key, or normalized alias."""
        if not name:
            return None
        if name in self._strategies:
            return name

        lower_map = {k.lower(): k for k in self._strategies.keys()}
        if str(name).lower() in lower_map:
            return lower_map[str(name).lower()]

        alias = self._strategy_aliases.get(_normalize_strategy_name(str(name)))
        if alias in self._strategies:
            return alias
        return None

    def _passes_routing_gate(self, routing: dict) -> tuple[bool, str]:
        """
        Production gate for backtest_routing rows.

        Prevent weak/noisy routes from being used in live selection.
        """
        trades = int(routing.get("total_trades", 0) or 0)
        win_rate = float(routing.get("win_rate", 0) or 0)
        pf = float(routing.get("profit_factor", 0) or 0)
        score = float(routing.get("score", 0) or 0)
        max_dd = float(routing.get("max_drawdown_pct", 0) or 0)

        if trades < ROUTING_MIN_TRADES:
            return False, f"trades<{ROUTING_MIN_TRADES}"
        if win_rate < ROUTING_MIN_WIN_RATE:
            return False, f"wr<{ROUTING_MIN_WIN_RATE}"
        if pf < ROUTING_MIN_PROFIT_FACTOR:
            return False, f"pf<{ROUTING_MIN_PROFIT_FACTOR}"
        if max_dd > 0 and max_dd > ROUTING_MAX_DRAWDOWN_PCT:
            return False, f"dd>{ROUTING_MAX_DRAWDOWN_PCT}"
        if score < ROUTING_MIN_SCORE:
            return False, f"score<{ROUTING_MIN_SCORE}"
        return True, ""

    def _lookup_routing(self, symbol: str, regime_val: str) -> Optional[dict]:
        """Case-insensitive routing lookup with symbol cleanup fallback."""
        normalized_symbol = _normalize_symbol_lookup(symbol)
        candidates = [
            symbol,
            symbol.upper(),
            symbol.lower(),
            symbol.rstrip("cmCM."),
            symbol.rstrip("cmCM.").upper(),
            symbol.rstrip("cmCM.").lower(),
            normalized_symbol,
            normalized_symbol.lower(),
        ]
        regimes = [regime_val, str(regime_val).upper(), str(regime_val).lower()]

        for sym in candidates:
            for reg in regimes:
                row = self._routing_cache.get(f"{sym}:{reg}")
                if row:
                    return row

        symbol_lower = str(symbol).lower()
        regime_upper = str(regime_val).upper()
        for key, row in self._routing_cache.items():
            try:
                sym_key, reg_key = key.split(":", 1)
            except ValueError:
                continue
            if sym_key.lower() == symbol_lower and reg_key.upper() == regime_upper:
                return row
            if _normalize_symbol_lookup(sym_key) == normalized_symbol and reg_key.upper() == regime_upper:
                return row
        return None

    def register(self, strategy: BaseStrategy) -> None:
        """
        Register a new strategy into the factory.

        Args:
            strategy: instance of BaseStrategy (or Bridge)

        Note:
            - The first registered strategy becomes the default automatically
        """
        self._strategies[strategy.name] = strategy
        self._strategy_aliases[_normalize_strategy_name(strategy.name)] = strategy.name
        logger.info("strategy_registered", extra={"strategy_name": strategy.name})

        # First registered strategy → set as default
        if self._default is None:
            self._default = strategy.name

    # ────────────────────────────────────────────────────────────────
    # Select Strategy — DB-Driven Selection
    # ────────────────────────────────────────────────────────────────

    def _is_strategy_allowed(self, strategy_name: str, symbol: str) -> bool:
        """Check if a strategy is allowed to trade the given symbol based on asset class."""
        resolved_name = self._resolve_strategy_name(strategy_name) or strategy_name
        asset_class = detect_asset_class(symbol)
        
        # Look up in registry cache
        allowed_asset = "*"
        for row in self._registry_cache:
            row_name = row.get("strategy_name")
            if row_name == resolved_name or _normalize_strategy_name(row_name) == _normalize_strategy_name(resolved_name):
                allowed_asset = row.get("asset_class", "*")
                break
                
        # Also check hardcoded fallback from the instance itself if available
        if allowed_asset == "*":
            strat = self._strategies.get(resolved_name)
            if strat:
                strat_asset = getattr(strat, "asset_class", getattr(strat, "regimes", "*"))
                # Note: strategy implementations might not have `asset_class` attribute directly, 
                # but if they do, we can fall back to it. (Usually defined in registry)
                
        return allowed_asset == "*" or allowed_asset == asset_class

    def select_strategy(
        self,
        symbol: str,
        regime: RegimeType = RegimeType.UNKNOWN,
        session: str = "CLOSED",
        brain_recommendation: str | None = None,
    ) -> Optional[BaseStrategy]:
        """
        Select the best strategy for current conditions — from DB.

        Priority:
            1. AI Brain recommendation
            2. backtest_routing DB — (symbol, regime) → highest score
            3. strategy_registry DB — (asset_class, regime) → highest priority
            ...
        """
        regime_val = regime.value if isinstance(regime, RegimeType) else str(regime)

        # --- 1. AI Brain recommendation ---
        brain_name = self._resolve_strategy_name(brain_recommendation)
        if brain_name:
            if self._is_strategy_allowed(brain_name, symbol):
                logger.info("strategy_selected_by_brain", extra={
                    "symbol": symbol, "strategy": brain_name,
                })
                return self._strategies[brain_name]
            else:
                logger.warning("brain_strategy_asset_mismatch", extra={
                    "symbol": symbol, "brain_strategy": brain_name,
                    "reason": "Asset class mismatch"
                })

        # --- 1.5 Shadow routing (live shadow performance) ---
        self._refresh_shadow_routing()
        shadow_key = f"{symbol}:{regime_val}"
        shadow = self._shadow_routing.get(shadow_key)
        if shadow:
            strat_name = self._resolve_strategy_name(shadow.get("strategy", ""))
            if strat_name in self._strategies:
                if self._is_strategy_allowed(strat_name, symbol):
                    logger.info("strategy_selected_by_shadow", extra={
                        "symbol": symbol, "regime": regime_val,
                        "strategy": strat_name,
                        "shadow_wr": shadow.get("win_rate", 0),
                        "shadow_signals": shadow.get("signals", 0),
                    })
                    return self._strategies[strat_name]
                else:
                    logger.warning("shadow_strategy_asset_mismatch", extra={
                        "symbol": symbol, "strategy": strat_name,
                    })

        # --- 2. Backtest routing DB (proven performance) ---
        # Try exact (symbol, regime)
        routing = self._lookup_routing(symbol, regime_val)

        if routing:
            eligible, gate_reason = self._passes_routing_gate(routing)
            if not eligible:
                logger.info("routing_rejected_by_gate", extra={
                    "symbol": symbol, "regime": regime_val,
                    "raw_strategy": routing.get("strategy", ""),
                    "reason": gate_reason,
                    "trades": routing.get("total_trades", 0),
                    "wr": routing.get("win_rate", 0),
                    "pf": routing.get("profit_factor", 0),
                    "max_dd": routing.get("max_drawdown_pct", 0),
                    "score": routing.get("score", 0),
                })
            strat_name = self._resolve_strategy_name(routing.get("strategy", ""))
            if eligible and strat_name in self._strategies:
                if self._is_strategy_allowed(strat_name, symbol):
                    logger.info("strategy_selected_by_routing", extra={
                        "symbol": symbol, "regime": regime_val,
                        "strategy": strat_name,
                        "score": routing.get("score", 0),
                        "win_rate": routing.get("win_rate", 0),
                        "profit_factor": routing.get("profit_factor", 0),
                        "max_drawdown_pct": routing.get("max_drawdown_pct", 0),
                    })
                    return self._strategies[strat_name]
                else:
                    logger.warning("routing_strategy_asset_mismatch", extra={
                        "symbol": symbol, "strategy": strat_name,
                    })

        # --- 3. Strategy registry DB (asset_class + regime match) ---
        asset_class = detect_asset_class(symbol)
        best = self._find_from_registry(asset_class, regime_val)
        if best:
            logger.info("strategy_selected_by_registry", extra={
                "symbol": symbol, "regime": regime_val,
                "asset_class": asset_class,
                "strategy": best.name,
            })
            return best

        # --- 4. Default fallback ---
        if self._default and self._default in self._strategies:
            logger.info("strategy_selected_default", extra={
                "symbol": symbol, "strategy": self._default,
            })
            return self._strategies[self._default]

        return None

    def _find_from_registry(
        self,
        asset_class: str,
        regime_val: str,
    ) -> Optional[BaseStrategy]:
        """
        Find best strategy from registry cache matching asset_class + regime.
        Registry is sorted by priority DESC.
        """
        for row in self._registry_cache:
            strat_name = self._resolve_strategy_name(row.get("strategy_name", ""))
            if strat_name not in self._strategies:
                continue

            # Match asset_class
            row_asset = row.get("asset_class", "*")
            if row_asset != "*" and row_asset != asset_class:
                continue

            # Match regime
            suitable = row.get("suitable_regimes", [])
            if suitable and regime_val not in suitable:
                continue

            return self._strategies[strat_name]

        # Fallback: try any strategy for this asset class (ignore regime)
        for row in self._registry_cache:
            strat_name = self._resolve_strategy_name(row.get("strategy_name", ""))
            if strat_name not in self._strategies:
                continue
            row_asset = row.get("asset_class", "*")
            if row_asset == asset_class or row_asset == "*":
                return self._strategies[strat_name]

        return None

    # ────────────────────────────────────────────────────────────────
    # Get Decision — select strategy then analyze → Decision
    # ────────────────────────────────────────────────────────────────

    def get_decision(
        self,
        candles: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType = RegimeType.UNKNOWN,
        session: str = "CLOSED",
        brain_recommendation: str | None = None,
        personality: Optional["PersonalityProfile"] = None,
        pressure: dict | None = None,
    ) -> Decision:
        """
        Select strategy → analyze market → return Decision.

        Args:
            pressure: buy/sell pressure from TickVolumeAnalyzer
                      {buying_pressure, selling_pressure, score, is_climax, ad_line_trend}
        """
        strategy = self.select_strategy(profile.symbol, regime, session, brain_recommendation)

        # Log personality context
        if personality:
            logger.debug("factory_personality_context", extra={
                "symbol": profile.symbol,
                "vol_score": personality.volatility_score,
                "fakeout_prob": round(personality.fakeout_probability, 2)
            })

        # No suitable strategy → HOLD
        if strategy is None:
            return Decision(
                symbol=profile.symbol,
                action=Action.HOLD,
                confidence=0.0,
                reason="No suitable strategy for current market conditions",
            )

        try:
            # Analyze market via selected strategy + pressure data
            decision = strategy.analyze(candles, profile, regime, pressure=pressure)

            # ─── Adapter: SniperSignal → Decision (sniper_pro compat) ───
            if not isinstance(decision, Decision):
                decision = self._adapt_sniper_signal(decision, profile)

            # ─── Shadow Confidence Calibration ───
            if decision.action != Action.HOLD:
                decision = self._calibrate_confidence(decision, strategy.name, profile.symbol, regime)

            # ─── Performance: HOLD → debug level (reduce log noise 80%+) ───
            if decision.action == Action.HOLD:
                logger.debug("strategy_hold", extra={
                    "symbol": profile.symbol,
                    "strategy": strategy.name,
                    "reason": decision.reason[:80],
                })
            else:
                logger.info("strategy_decision", extra={
                    "symbol": profile.symbol,
                    "strategy": strategy.name,
                    "action": decision.action.value,
                    "confidence": decision.confidence,
                    "stage": "signal",
                    "result": "ok",
                    "pressure": pressure.get("score", 0) if pressure else 0,
                })
            return decision
        except Exception as e:
            # Never fail silently — log error then return HOLD
            logger.error("strategy_error", extra={
                "symbol": profile.symbol,
                "strategy": strategy.name,
                "error": str(e),
                "type": type(e).__name__,
                "stage": "signal",
                "result": "error",
            }, exc_info=True)
            return Decision(
                symbol=profile.symbol,
                action=Action.HOLD,
                confidence=0.0,
                reason=f"Strategy error: {e}",
                strategy_name=strategy.name,
            )

    # ─── Adapter: SniperSignal → Decision ───────────────────────
    def _adapt_sniper_signal(self, sig, profile: SymbolProfile) -> Decision:
        """Convert sniper_pro's SniperSignal to standard Decision."""
        try:
            sig_val = getattr(sig, "signal", None)
            if sig_val is None:
                return Decision(
                    symbol=profile.symbol, action=Action.HOLD,
                    confidence=0.0, reason="Unknown signal type",
                )
            # Map Signal enum → Action
            sig_name = sig_val.value if hasattr(sig_val, "value") else str(sig_val)
            if sig_name == "BUY":
                action = Action.BUY
            elif sig_name == "SELL":
                action = Action.SELL
            else:
                action = Action.HOLD

            # Normalize confidence: SniperSignal uses 0-100, Decision uses 0-1
            raw_conf = getattr(sig, "confidence", 0)
            confidence = round(min(1.0, raw_conf / 100.0), 3) if raw_conf > 1 else round(raw_conf, 3)

            return Decision(
                symbol=getattr(sig, "symbol", profile.symbol),
                action=action,
                confidence=confidence,
                reason="; ".join(getattr(sig, "reasons", [])) or "sniper_pro signal",
                stop_loss=getattr(sig, "stop_loss", 0.0),
                take_profit=getattr(sig, "take_profit", 0.0),
                strategy_name="sniper_pro",
                tags=["sniper_pro", "adapted"],
            )
        except Exception as e:
            logger.warning("sniper_signal_adapt_failed", extra={"error": str(e)})
            return Decision(
                symbol=profile.symbol, action=Action.HOLD,
                confidence=0.0, reason=f"Signal adapt error: {e}",
            )

    # ─── Shadow Routing Cache ─────────────────────────────────────

    def _refresh_shadow_routing(self) -> None:
        """
        Refresh shadow routing cache from shadow_scoreboard DB.

        Builds routing dict: {SYMBOL:REGIME} → best strategy (highest win_rate).
        Only includes strategies with ≥SHADOW_MIN_SIGNALS and WR≥SHADOW_MIN_WIN_RATE.
        Cache TTL: 5 minutes.
        """
        now = time.monotonic()
        if now - self._shadow_cache_time < SHADOW_CACHE_TTL:
            return  # Cache still fresh

        self._shadow_cache_time = now

        if not self._db:
            return

        try:
            scoreboard = self._db.get_shadow_scoreboard()
            if not scoreboard:
                return

            routing: dict[str, dict] = {}
            confidence: dict[str, dict] = {}

            for row in scoreboard:
                strat = row.get("strategy_name", "")
                symbol = row.get("symbol", "")
                regime = row.get("regime", "ALL")
                wr = row.get("win_rate", 0) or 0
                signals = row.get("total_signals", 0) or 0

                # Build per-strategy confidence cache
                conf_key = f"{strat}:{symbol}"
                if conf_key not in confidence or signals > confidence[conf_key].get("signals", 0):
                    confidence[conf_key] = {
                        "win_rate": wr,
                        "signals": signals,
                    }

                # Only route if meets thresholds
                if signals < SHADOW_MIN_SIGNALS or wr < SHADOW_MIN_WIN_RATE:
                    continue

                key = f"{symbol}:{regime}"
                # Keep the best (highest win_rate) per key
                if key not in routing or wr > routing[key].get("win_rate", 0):
                    routing[key] = {
                        "strategy": strat,
                        "win_rate": wr,
                        "signals": signals,
                    }

            self._shadow_routing = routing
            self._shadow_confidence = confidence

            if routing:
                logger.info("shadow_routing_refreshed", extra={
                    "routes": len(routing),
                    "confidence_entries": len(confidence),
                })

        except Exception as e:
            logger.debug("shadow_routing_refresh_error", extra={"error": str(e)})

    # ─── Shadow Confidence Calibration ────────────────────────────

    def _calibrate_confidence(
        self, decision: Decision, strategy_name: str, symbol: str, regime
    ) -> Decision:
        """
        Adjust decision confidence based on shadow trading track record.

        Rules:
            Shadow WR ≥ 70% → boost +0.15
            Shadow WR ≥ 60% → boost +0.05
            Shadow WR < 40% → penalty -0.15
            No data or < 5 signals → no change
        """
        conf_key = f"{strategy_name}:{symbol}"
        conf_data = self._shadow_confidence.get(conf_key)

        if not conf_data or conf_data.get("signals", 0) < 5:
            return decision  # Not enough data

        wr = conf_data.get("win_rate", 0)
        boost = 0.0

        if wr >= 70:
            boost = 0.15
        elif wr >= 60:
            boost = 0.05
        elif wr < 40:
            boost = -0.15

        if boost != 0:
            original = decision.confidence
            new_conf = max(0.0, min(1.0, original + boost))
            decision.confidence = round(new_conf, 3)

            logger.debug("shadow_confidence_calibrated", extra={
                "symbol": symbol,
                "strategy": strategy_name,
                "shadow_wr": wr,
                "boost": boost,
                "original": round(original, 3),
                "calibrated": decision.confidence,
            })

        return decision

