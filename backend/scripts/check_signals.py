"""Quick signal scanner for XAUUSDc and XAGUSDc."""
import asyncio
import MetaTrader5 as mt5

from app.core.config import Settings
from app.mt5.client import MT5Client
from app.mt5.market_data import fetch_candles
from app.strategy.factory import StrategyFactory
from app.brain.regime import classify_regime
from app.domain.enums import MarketSession

from app.domain.models import SymbolProfile

async def scan():
    settings = Settings()
    mt5_client = MT5Client(settings)
    
    if not mt5_client.connect():
        print("Failed to connect to MT5")
        return

    # Auto-register strategies from registry
    factory = StrategyFactory()
    factory.auto_register()
    
    symbols = ["XAUUSDc", "XAGUSDc"]
    
    print("=" * 50)
    print("CURRENT ENTRY SIGNALS")
    print("=" * 50)

    for symbol in symbols:
        print(f"\n--- Scanning {symbol} ---")
        
        # Fetch candles
        m5_candles = fetch_candles(symbol, "M5", 500)
        h1_candles = fetch_candles(symbol, "H1", 300)
        
        if m5_candles is None or m5_candles.empty:
            print(f"[{symbol}] Failed to fetch candles")
            continue

        # Classify regime
        regime_ctx = classify_regime(m5_candles, h1_candles)
        print(f"Regime: {regime_ctx.regime.name} | Score: {regime_ctx.score} | Actionable: {regime_ctx.actionable}")

        # Test strategies via get_decision
        profile = SymbolProfile(symbol=symbol)
        
        decision = factory.get_decision(
            candles=m5_candles,
            profile=profile,
            regime=regime_ctx.regime,
            session=MarketSession.NEW_YORK.value
        )
        
        if decision.action.name in ["BUY", "SELL"]:
            print(f"\n✅ ACTUAL SIGNAL FOR {symbol}:")
            print(f"  Action: {decision.action.name}")
            print(f"  Strategy: {decision.strategy_name}")
            print(f"  Confidence: {decision.confidence:.2f}")
            print(f"  Reason: {decision.reason}")
            print(f"  SL: {decision.stop_loss} | TP: {decision.take_profit}")
        else:
            print(f"\n❌ NO STRONG SIGNALS FOR {symbol} RIGHT NOW")
            print(f"  Reason: {decision.reason}")

if __name__ == "__main__":
    asyncio.run(scan())
