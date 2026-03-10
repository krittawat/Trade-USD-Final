import json
from app.strategy.factory import StrategyFactory
from app.db.sqlite import SQLiteStore
from app.core.config import get_settings

def main():
    settings = get_settings()
    db = SQLiteStore(settings)
    db.connect()  # Must call connect to initialize
    
    factory = StrategyFactory()
    loaded = factory.auto_register(db)
    print(f"Loaded {loaded} strategies")
    
    # Try select_strategy with mismatched brain prediction
    print("\n--- Testing Mismatch Rejection ---")
    strat = factory.select_strategy(
        symbol="XAUUSDc", 
        regime="UNKNOWN",
        brain_recommendation="silver_elite"
    )
    print(f"select_strategy for XAUUSDc + silver_elite returns: {strat.name if strat else 'None'}")
    
    # Try a shadow mismatch
    factory._shadow_routing["XAUUSDc:UNKNOWN"] = {"strategy": "btc_elite", "win_rate": 80}
    strat = factory.select_strategy(
        symbol="XAUUSDc", 
        regime="UNKNOWN",
        brain_recommendation=None
    )
    print(f"select_strategy for XAUUSDc + shadow btc_elite returns: {strat.name if strat else 'None'}")
    
    # Try a correct fallback
    strat = factory.select_strategy(
        symbol="XAUUSDc", 
        regime="UNKNOWN",
        brain_recommendation=None
    )
    print(f"select_strategy for XAUUSDc + NO brain + NO shadow (expect registry fallback) returns: {strat.name if strat else 'None'}")

if __name__ == "__main__":
    main()
