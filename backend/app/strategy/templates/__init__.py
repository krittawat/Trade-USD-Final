"""
Strategy Templates Package.

Contains all strategy implementations and the seed registry data
used for auto-populating the strategy_registry DB table.
"""

# ====================================================================
# Seed Registry — auto-populate strategy_registry when DB is empty.
#
# This data mirrors seed_strategy_params.py but is used by
# StrategyFactory._auto_seed_registry() during startup.
# ====================================================================

_SEED_REGISTRY = [
    # ── Gold Strategies ──
    {"strategy_name": "gold_elite", "class_name": "GoldEliteStrategy",
     "module_path": "app.strategy.templates.gold_elite", "timeframe": "M5",
     "asset_class": "gold",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout", "ranging"],
     "priority": 115},

    {"strategy_name": "gold_scalp_pro", "class_name": "GoldScalpProStrategy",
     "module_path": "app.strategy.templates.gold_scalp_pro", "timeframe": "M5",
     "asset_class": "gold",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility"],
     "priority": 110},

    {"strategy_name": "gold_precision", "class_name": "GoldPrecisionStrategy",
     "module_path": "app.strategy.templates.gold_precision", "timeframe": "M5",
     "asset_class": "gold",
     "suitable_regimes": ["ranging", "low_volatility", "trending_up", "trending_down"],
     "priority": 85},

    {"strategy_name": "gold_break_checklist", "class_name": "GoldBreakChecklistStrategy",
     "module_path": "app.strategy.templates.gold_break_checklist", "timeframe": "M5",
     "asset_class": "gold",
     "suitable_regimes": ["breakout", "high_volatility", "trending_up"],
     "priority": 80},

    {"strategy_name": "gold_smart_money", "class_name": "GoldSmartMoneyStrategy",
     "module_path": "app.strategy.templates.gold_smart_money", "timeframe": "M5",
     "asset_class": "gold",
     "suitable_regimes": ["trending_up", "trending_down", "breakout"],
     "priority": 75},

    {"strategy_name": "gold_session_breakout", "class_name": "GoldSessionBreakout",
     "module_path": "app.strategy.templates.gold_session_breakout", "timeframe": "M5",
     "asset_class": "gold",
     "suitable_regimes": ["breakout", "high_volatility"],
     "priority": 70},

    {"strategy_name": "gold_silver_wr60", "class_name": "GoldSilverWr60Strategy",
     "module_path": "app.strategy.templates.gold_silver_wr60", "timeframe": "M5",
     "asset_class": "gold",
     "suitable_regimes": ["trending_up", "trending_down", "ranging"],
     "priority": 60},

    # ── Silver Strategies ──
    {"strategy_name": "silver_elite", "class_name": "SilverEliteStrategy",
     "module_path": "app.strategy.templates.silver_elite", "timeframe": "M5",
     "asset_class": "silver",
     "suitable_regimes": ["trending_up", "trending_down", "ranging", "high_volatility", "breakout"],
     "priority": 115},

    {"strategy_name": "silver_mean_rev", "class_name": "SilverMeanRevStrategy",
     "module_path": "app.strategy.templates.silver_mean_rev", "timeframe": "M5",
     "asset_class": "silver",
     "suitable_regimes": ["ranging", "low_volatility"],
     "priority": 85},

    # ── Crypto Strategies ──
    {"strategy_name": "btc_elite", "class_name": "BtcEliteStrategy",
     "module_path": "app.strategy.templates.btc_elite", "timeframe": "M5",
     "asset_class": "crypto",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout", "ranging"],
     "priority": 100},

    {"strategy_name": "btc_momentum", "class_name": "BtcMomentum",
     "module_path": "app.strategy.templates.btc_momentum", "timeframe": "M5",
     "asset_class": "crypto",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout", "unknown"],
     "priority": 95},

    {"strategy_name": "btc_mean_reversion", "class_name": "BtcMeanReversion",
     "module_path": "app.strategy.templates.btc_mean_reversion", "timeframe": "M15",
     "asset_class": "crypto",
     "suitable_regimes": ["ranging", "low_volatility", "unknown"],
     "priority": 92},

    {"strategy_name": "btc_stop_hunt", "class_name": "BtcStopHuntStrategy",
     "module_path": "app.strategy.templates.btc_stop_hunt", "timeframe": "M5",
     "asset_class": "crypto",
     "suitable_regimes": ["ranging", "high_volatility", "breakout"],
     "priority": 94},

    # ── Forex Strategies ──
    {"strategy_name": "forex_precision", "class_name": "ForexPrecisionStrategy",
     "module_path": "app.strategy.templates.forex_precision", "timeframe": "M5",
     "asset_class": "forex",
     "suitable_regimes": ["trending_up", "trending_down", "ranging"],
     "priority": 90},

    {"strategy_name": "fx_sniper", "class_name": "FxSniperStrategy",
     "module_path": "app.strategy.templates.fx_sniper", "timeframe": "M5",
     "asset_class": "forex",
     "suitable_regimes": ["trending_up", "trending_down", "breakout"],
     "priority": 85},

    {"strategy_name": "usdjpy_elite", "class_name": "UsdjpyEliteStrategy",
     "module_path": "app.strategy.templates.usdjpy_elite", "timeframe": "M5",
     "asset_class": "forex",
     "suitable_regimes": ["trending_up", "trending_down", "ranging", "breakout"],
     "priority": 80},

    # ── Universal Strategies (all asset classes) ──
    {"strategy_name": "scalping", "class_name": "ScalpingStrategy",
     "module_path": "app.strategy.templates.scalping", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility"],
     "priority": 50},

    {"strategy_name": "sniper", "class_name": "SniperStrategy",
     "module_path": "app.strategy.templates.sniper", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "breakout"],
     "priority": 50},

    {"strategy_name": "sniper_pro", "class_name": "SniperProStrategy",
     "module_path": "app.strategy.templates.sniper_pro", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "breakout"],
     "priority": 55},

    {"strategy_name": "trend_rider", "class_name": "TrendRiderStrategy",
     "module_path": "app.strategy.templates.trend_rider", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "strong_trend"],
     "priority": 50},

    {"strategy_name": "ranging_sniper", "class_name": "RangingSniperStrategy",
     "module_path": "app.strategy.templates.ranging_sniper", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["ranging", "low_volatility"],
     "priority": 50},

    # ── ADD_18022026 — New Strategies ──
    {"strategy_name": "btc_pro", "class_name": "BTCProStrategy",
     "module_path": "app.strategy.templates.ADD_18022026.btc_pro_strategy", "timeframe": "M5",
     "asset_class": "crypto",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout"],
     "priority": 85},

    {"strategy_name": "btc_ultimate", "class_name": "OmniscientOracleStrategy",
     "module_path": "app.strategy.templates.ADD_18022026.btc_ultimate_strategy", "timeframe": "M5",
     "asset_class": "crypto",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout", "ranging"],
     "priority": 90},

    {"strategy_name": "gold_scalp_wr60_v2", "class_name": "GoldScalpWR60Strategy",
     "module_path": "app.strategy.templates.ADD_18022026.gold_scalp_wr60", "timeframe": "M5",
     "asset_class": "gold",
     "suitable_regimes": ["trending_up", "trending_down", "ranging", "high_volatility"],
     "priority": 88},

    {"strategy_name": "google_gravity", "class_name": "GoogleGravityStrategy",
     "module_path": "app.strategy.templates.ADD_18022026.google_gravity", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout", "ranging"],
     "priority": 95},

    {"strategy_name": "regime_adaptive", "class_name": "RegimeAdaptiveStrategy",
     "module_path": "app.strategy.templates.ADD_18022026.regime_adaptive", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout", "ranging", "low_volatility"],
     "priority": 92},

    {"strategy_name": "universal_sniper", "class_name": "UniversalSniperStrategy",
     "module_path": "app.strategy.templates.ADD_18022026.universal_sniper", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "breakout", "high_volatility"],
     "priority": 88},

    {"strategy_name": "vfinal", "class_name": "VFinalStrategy",
     "module_path": "app.strategy.templates.ADD_18022026.vfinal_strategy", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout", "ranging"],
     "priority": 93},

    # ── EMA 180 MTF + FVG Strategy ──
    {"strategy_name": "ema180_mtf_fvg", "class_name": "Ema180MtfFvgStrategy",
     "module_path": "app.strategy.templates.ema180_mtf_fvg", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "ranging"],
     "priority": 105},

    # ── Gold Evolution — WR>70% Fusion Strategy (Top Priority) ──
    {"strategy_name": "gold_evolution", "class_name": "GoldEvolutionStrategy",
     "module_path": "app.strategy.templates.gold_evolution", "timeframe": "M5",
     "asset_class": "gold",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "strong_trend", "weak_trend", "breakout"],
      "priority": 120},

    # ── AI Dragon — LSTM-Enhanced Gold Strategy (High Win Rate) ──
    {"strategy_name": "gold_ai_dragon", "class_name": "GoldAIDragonStrategy",
     "module_path": "app.strategy.templates.gold_ai_dragon", "timeframe": "M5",
     "asset_class": "gold",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout", "ranging", "low_volatility"],
      "priority": 98},

    # ── JTG Bounce Zone + FVG (M15, Price Action + SMC) ──
    {"strategy_name": "jtg_zone_fvg", "class_name": "JTGZoneFVGStrategy",
     "module_path": "app.strategy.templates.jtg_zone_fvg", "timeframe": "M15",
     "asset_class": "gold",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility"],
     "priority": 85},

    # ── AI MTF V3 — GRU+Attention Multi-Timeframe Strategy (Universal) ──
    {"strategy_name": "ai_mtf_v3", "class_name": "AIMultiTFStrategy",
     "module_path": "app.strategy.templates.ai_mtf_strategy", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout", "ranging", "low_volatility"],
      "priority": 99},

    # ══════════════════════════════════════════════════════════════
    # OPUS Ghost Protocol — Aggressive Smart Money Hunter
    # ══════════════════════════════════════════════════════════════
    {"strategy_name": "opus_liquidity_hunter", "class_name": "OpusLiquidityHunterStrategy",
     "module_path": "app.strategy.templates.opus_liquidity_hunter", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["vol_expansion", "high_volatility", "distribution", "accumulation",
                          "liquidity_sweep", "trending_up", "trending_down"],
     "priority": 125},

    {"strategy_name": "opus_trend_killer", "class_name": "OpusTrendKillerStrategy",
     "module_path": "app.strategy.templates.opus_trend_killer", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["strong_trend", "vol_expansion", "breakout",
                          "trending_up", "trending_down"],
     "priority": 122},

    # ══════════════════════════════════════════════════════════════
    # Alpha V6 SMC Strategy — SSL Hybrid + Smart Money Concepts
    # ══════════════════════════════════════════════════════════════
    {"strategy_name": "alpha_v6_smc", "class_name": "AlphaV6SMCStrategy",
     "module_path": "app.strategy.templates.alpha_v6_smc", "timeframe": "M15",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout", "liquidity_sweep"],
     "priority": 130},

    {"strategy_name": "opus_oil_momen", "class_name": "OpusOilMomentumStrategy",
     "module_path": "app.strategy.templates.opus_oil_momentum", "timeframe": "M5",
     "asset_class": "oil",
     "suitable_regimes": ["trending_up", "trending_down", "strong_trend", "vol_expansion", "breakout"],
     "priority": 128},

    {"strategy_name": "aether_flow", "class_name": "AetherFlowStrategy",
     "module_path": "app.strategy.templates.aether_flow", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout", "liquidity_sweep"],
     "priority": 135},

    # ══════════════════════════════════════════════════════════════
    # Alpha V7 ICT Strategy — ICT Max Efficiency (Kill Zones + FVG)
    # ══════════════════════════════════════════════════════════════
    {"strategy_name": "alpha_v7_ict", "class_name": "AlphaV7ICTStrategy",
     "module_path": "app.strategy.templates.alpha_v7_ict", "timeframe": "M5",
     "asset_class": "*",
     "suitable_regimes": ["trending_up", "trending_down", "high_volatility", "breakout", "liquidity_sweep", "accumulation"],
     "priority": 140},
]
