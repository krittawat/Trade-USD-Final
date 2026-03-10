import sys
sys.path.append('d:/VibeCode/Trade')
from backend.trader.risk.gate import RiskEngine
engine = RiskEngine()
print("gate.py config:", engine.config)
sig = {"symbol": "XAUUSD", "side": "BUY", "sl": 1990.0, "model": "TEST"}
state = {"equity": 1000, "daily_pnl": -110, "consecutive_losses": 1}
print("running gate...")
res = engine.risk_gate(sig, state, {"spread": 10, "is_news": False})
print("Result allowed:", res['allowed'])
print("Reasons:", res['reasons'])
