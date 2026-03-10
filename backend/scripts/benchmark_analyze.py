import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np
import time
from app.strategy.templates.alpha_v6_smc import AlphaV6SMCStrategy
from app.domain.models import SymbolProfile, RegimeContext, Decision
from app.domain.enums import RegimeType, Action

# Mock data
df = pd.DataFrame({
    'open': np.random.randn(1000),
    'high': np.random.randn(1000),
    'low': np.random.randn(1000),
    'close': np.random.randn(1000),
    'tick_volume': np.random.randn(1000)
})

strategy = AlphaV6SMCStrategy(symbol="XAUUSDm")
profile = SymbolProfile(symbol="XAUUSDm", contract_size=100.0, point=0.001, digits=3, volume_min=0.01, volume_max=100.0, volume_step=0.01)
regime = RegimeContext(regime=RegimeType.TRENDING_UP, actionable=True, reason="MOCK", details={})

print("Benchmarking 100 iterations of analyze()...")
start = time.time()
for _ in range(100):
    strategy.analyze(df, profile, regime)
end = time.time()
print(f"Time for 100 iterations: {end - start:.2f}s")
print(f"Time per iteration: {(end-start)/100:.4f}s")

# Final smoke test: Get one decision
decision = strategy.analyze(df, profile, regime)
print(f"Sample Decision for Gold: {decision.action} ({decision.reason})")
