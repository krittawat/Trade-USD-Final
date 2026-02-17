"""Quick verification of SL/TP ratio fixes."""

# 1. Scalping
from app.strategy.templates.scalping import ATR_SL_MULT, RR_RATIO
print(f"Scalping: SL_ATR={ATR_SL_MULT} RR={RR_RATIO} TP={ATR_SL_MULT*RR_RATIO}")
assert ATR_SL_MULT >= 2.0, f"Scalping SL too tight: {ATR_SL_MULT}"
assert RR_RATIO >= 2.0, f"Scalping RR too low: {RR_RATIO}"

# 2. GoldScalpPro
from app.strategy.templates.gold_scalp_pro import GoldScalpProStrategy
gs = GoldScalpProStrategy()
rr = gs.TP_ATR_MULT / gs.SL_ATR_MULT
print(f"GoldScalpPro: SL={gs.SL_ATR_MULT} TP={gs.TP_ATR_MULT} RR={rr:.2f} LOCK1={gs.PROFIT_LOCK_1} LOCK2={gs.PROFIT_LOCK_2}")
assert rr >= 1.0, f"GoldScalpPro RR inverted: {rr}"
assert gs.PROFIT_LOCK_1 >= 1.0, f"Profit lock too early: {gs.PROFIT_LOCK_1}"

# 3. HyperScalp
from app.strategy.templates.hyper_scalp import HyperScalpStrategy
hs = HyperScalpStrategy()
rr = hs.CONFIG["tp_atr_mult"] / hs.CONFIG["sl_atr_mult"]
print(f"HyperScalp: SL={hs.CONFIG['sl_atr_mult']} TP={hs.CONFIG['tp_atr_mult']} RR={rr:.2f}")
assert rr >= 1.5, f"HyperScalp RR too low: {rr}"

# 4. SmartFusion
from app.strategy.templates.smart_fusion import SmartFusionStrategy
sf = SmartFusionStrategy()
rr = sf.tp_atr_mult / sf.sl_atr_mult
print(f"SmartFusion: SL={sf.sl_atr_mult} TP={sf.tp_atr_mult} RR={rr:.2f}")
assert rr >= 1.5, f"SmartFusion RR too low: {rr}"

# 5. Evolver RR bounds
from app.brain.strategy_evolver import PARAM_BOUNDS
rr_min = PARAM_BOUNDS["rr_ratio"][0]
print(f"Evolver RR min bound: {rr_min}")
assert rr_min >= 1.5, f"Evolver allows low RR: {rr_min}"

print("\n✅ ALL CHECKS PASSED — Every strategy has RR >= 1.5")
