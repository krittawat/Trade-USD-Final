import sys
import os
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import numpy as np

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import MetaTrader5 as mt5
    from app.execution.backtester import Backtester
    from app.strategy.templates.alpha_v6_smc import AlphaV6SMCStrategy
    from app.domain.models import SymbolProfile, Decision
    from app.domain.enums import Action, RegimeType
except ImportError as e:
    print(f"FAIL Import error: {e}")
    sys.exit(1)

class FastAlphaV6Strategy(AlphaV6SMCStrategy):
    def analyze(self, df: pd.DataFrame, profile: SymbolProfile, regime: RegimeType | None = None, **kwargs) -> Decision:
        last_row = df.iloc[-1]
        symbol = profile.symbol
        
        # 1. SSL Baseline (HMA)
        baseline_col = f"hma_{self.ssl_baseline}"
        baseline_val = last_row[baseline_col]
        
        # 2. QQE & WAE
        qqe_up = last_row["qqe_up"]
        qqe_down = last_row["qqe_down"]
        wae_buy = last_row["wae_buy"]
        wae_sell = last_row["wae_sell"]
        
        # 3. SMC Structure
        last_ph = last_row[f"smc_ph_{self.smc_pivot}"]
        last_pl = last_row[f"smc_pl_{self.smc_pivot}"]
        struct = last_row[f"smc_struct_{self.smc_pivot}"]
        
        # 4. Sweep & Displacement
        sweep = self._detect_liquidity_sweep(df, last_ph, last_pl)
        atr_val = last_row["atr"]
        curr_range = abs(last_row["high"] - last_row["low"])
        is_displaced = curr_range >= (self.mss_displacement * atr_val)
        
        # 5. ADX
        curr_adx = last_row["adx"]
        is_trending = curr_adx >= self.adx_threshold
        
        current_close = last_row["close"]
        is_bullish_momentum = (current_close > baseline_val) and qqe_up and wae_buy
        is_bearish_momentum = (current_close < baseline_val) and qqe_down and wae_sell
        
        is_bullish_struct = ((struct == "BULLISH_BOS") and is_displaced) or (sweep == "BULLISH_SWEEP")
        is_bearish_struct = ((struct == "BEARISH_BOS") and is_displaced) or (sweep == "BEARISH_SWEEP")
        
        long_signal = (is_bullish_momentum and is_bullish_struct and (is_trending or sweep == "BULLISH_SWEEP"))
        short_signal = (is_bearish_momentum and is_bearish_struct and (is_trending or sweep == "BEARISH_SWEEP"))

        if long_signal:
            sl_price = last_pl - (0.5 * atr_val) if last_pl != float('-inf') and last_pl == last_pl else current_close - (2.5 * atr_val)
            tp_mult = 2.0 if sweep == "BULLISH_SWEEP" else 1.5
            tp_price = current_close + (abs(current_close - sl_price) * tp_mult)
            return Decision(symbol=symbol, action=Action.BUY, strategy_name="fast_alpha_v6", 
                            confidence=0.95, reason="FAST_BUY", stop_loss=sl_price, take_profit=tp_price)
        elif short_signal:
            sl_price = last_ph + (0.5 * atr_val) if last_ph != float('inf') and last_ph == last_ph else current_close + (2.5 * atr_val)
            tp_mult = 2.0 if sweep == "BEARISH_SWEEP" else 1.5
            tp_price = current_close - (abs(sl_price - current_close) * tp_mult)
            return Decision(symbol=symbol, action=Action.SELL, strategy_name="fast_alpha_v6", 
                            confidence=0.95, reason="FAST_SELL", stop_loss=sl_price, take_profit=tp_price)
                            
        return Decision(symbol=symbol, action=Action.HOLD, strategy_name="fast_alpha_v6")

def precompute_all_indicators(df, baseline_range, smc_pivot_range):
    from app.analysis.indicators import hma, qqe_mod, wae, smc_pivots, adx, atr, ema
    print(f"   Calculating base indicators (ADX, ATR, QQE, WAE, EMA)...")
    df["atr"] = atr(df["high"], df["low"], df["close"], 14)
    adx_df = adx(df["high"], df["low"], df["close"], 14)
    df["adx"] = adx_df["ADX_14"]
    
    q = qqe_mod(df["close"])
    df["qqe_up"] = q["QQE_Up"]
    df["qqe_down"] = q["QQE_Down"]
    
    w = wae(df["close"])
    df["wae_buy"] = w["WAE_Buy"]
    df["wae_sell"] = w["WAE_Sell"]
    
    df["ema_200"] = ema(df["close"], 200)
    
    print(f"   Generating {len(baseline_range)} SSL Baseline variants...")
    for blen in baseline_range:
        df[f"hma_{blen}"] = hma(df["close"], blen)
        
    print(f"   Generating {len(smc_pivot_range)} SMC Structure variants...")
    for slen in smc_pivot_range:
        pivots = smc_pivots(df["high"], df["low"], length=slen)
        df[f"smc_ph_{slen}"] = pivots["PivotHigh"].ffill().replace({np.nan: float('inf')})
        df[f"smc_pl_{slen}"] = pivots["PivotLow"].ffill().replace({np.nan: float('-inf')})
        
        def get_struct(row):
            if row["close"] > row["last_ph"]: return "BULLISH_BOS"
            if row["close"] < row["last_pl"]: return "BEARISH_BOS"
            if row["close"] > row["ema_200"]: return "BULLISH_TREND"
            return "BEARISH_TREND"
            
        temp_df = pd.DataFrame({
            "close": df["close"], 
            "last_ph": df[f"smc_ph_{slen}"].shift(1), 
            "last_pl": df[f"smc_pl_{slen}"].shift(1), 
            "ema_200": df["ema_200"]
        })
        df[f"smc_struct_{slen}"] = temp_df.apply(get_struct, axis=1)
        
    return df

def run_optimization(symbol, contract_size, point, digits, days=30):
    print(f"\n{'='*60}\nOptimizing {symbol} ({days} days)\n{'='*60}")
    
    utc_to = datetime.now(timezone.utc)
    utc_from = utc_to - timedelta(days=days)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M15, utc_from, utc_to)
    if rates is None or len(rates) < 300:
        print(f"FAIL Insufficient data for {symbol}"); return None
        
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    
    baseline_range = [30, 60, 100]
    smc_pivot_range = [3, 5, 8]
    mss_range = [0.0, 0.5, 1.0, 1.5]
    adx_range = [0, 15, 20, 25]
    
    df = precompute_all_indicators(df, baseline_range, smc_pivot_range)
    strategy = FastAlphaV6Strategy(symbol=symbol)
    results = []
    
    start_opt = time.time()
    for blen in baseline_range:
        for slen in smc_pivot_range:
            for mss in mss_range:
                for adx_t in adx_range:
                    strategy.ssl_baseline = blen
                    strategy.smc_pivot = slen
                    strategy.mss_displacement = mss
                    strategy.adx_threshold = adx_t
                    
                    bt = Backtester(strategy, initial_equity=10000.0, risk_per_trade=0.01)
                    try:
                        res = bt.run(df, symbol=symbol, contract_size=contract_size, point=point)
                        if res.total_trades > 0:
                            results.append({
                                "baseline": blen, "smc_pivot": slen, "mss_displacement": mss, "adx_threshold": adx_t,
                                "trades": res.total_trades, "win_rate": res.win_rate, "pf": res.profit_factor,
                                "pnl": res.total_profit_usd, "max_dd": res.max_drawdown_pct
                            })
                    except: pass
    
    if not results: return None
    sorted_results = sorted(results, key=lambda x: (x["win_rate"], x["pf"]), reverse=True)
    best = sorted_results[0]
    print(f"   Optimization Finished in {time.time()-start_opt:.1f}s")
    print(f"   BEST WR for {symbol}: WR={best['win_rate']}%, PF={best['pf']} (bl={best['baseline']}, smc={best['smc_pivot']}, mss={best['mss_displacement']}, adx={best['adx_threshold']})")
    return best

if __name__ == "__main__":
    if not mt5.initialize(): sys.exit(1)
    targets = [
        {"symbol": "XAUUSDm", "contract_size": 100.0, "point": 0.001, "digits": 3},
        {"symbol": "XAGUSDm", "contract_size": 5000.0, "point": 0.001, "digits": 3},
        {"symbol": "USOILm", "contract_size": 1000.0, "point": 0.01, "digits": 2},
    ]
    final_summary = {}
    for target in targets:
        best_config = run_optimization(target["symbol"], target["contract_size"], target["point"], target["digits"], days=30)
        if best_config: final_summary[target["symbol"]] = best_config
    print("\n" + "="*60 + "\nFINAL OPTIMIZATION SUMMARY\n" + "="*60)
    print(json.dumps(final_summary, indent=2))
    mt5.shutdown()
