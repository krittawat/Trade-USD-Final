import datetime
from typing import Any, Optional
import pandas as pd
import numpy as np

from app.strategy.base import BaseStrategy
from app.domain.models import SymbolProfile, Decision
from app.domain.enums import Action, RegimeType
from app.core.logging import get_logger
from app.analysis.indicators import ema, atr, supertrend, hma, qqe_mod, wae, smc_pivots, adx, macd, rsi, bulls_power, bears_power, momentum, psar

logger = get_logger(__name__)

class AlphaV6SMCStrategy(BaseStrategy):
    """
    Antigravity Alpha V6 (SSL Hybrid + Smart Money Concepts)
    
    This strategy merges:
    1. Trend & Momentum from SSL Hybrid (Baseline HMA, QQE, WAE)
    2. Smart Money Concepts (Swing Structure, Order Blocks, Liquidity Sweeps)
    3. OPUS EDITION: Mandatory Liquidity Sweep verification
    """
    
    name = "alpha_v6_smc"
    asset_class = "*"
    
    @classmethod
    def get_name(cls) -> str:
        return "alpha_v6_smc"

    @classmethod
    def get_supported_timeframes(cls) -> list[str]:
        return ["M5", "M15", "H1", "D1"]
        
    def __init__(self, symbol: Optional[str] = None, **kwargs):
        super().__init__()
        self.symbol = symbol or "UNKNOWN"
        self._initialized_for_symbol = None
        
        # 1. Define hardcoded DEFAULTS as code-level fallback
        defaults = {
            "smc_pivot": 5,
            "ssl_baseline": 60,
            "tp_mult": 1.5,
            "mss_displacement": 1.2, # Multiple of ATR
            "adx_threshold": 20,
            "min_rr": 1.5,
            "min_liquidity_conf": 0.60,
            "sl_mult": 1.0,
            "risk_per_trade": kwargs.get("risk_per_trade", 0.01)
        }
        
        # 2. Try to load from DB via ParamLoader (if exists)
        from app.strategy.param_loader import get_param_loader
        loader = get_param_loader()
        
        if symbol and loader:
            self.p = loader.get_params("alpha_v6_smc", symbol, defaults)
        else:
            self.p = defaults

        # Inject some static logic if needed (hardcoded legacy overrides)
        self._apply_legacy_overrides(symbol, kwargs) if symbol else None

        # Map to instance attributes for backward compatibility with existing code
        self.smc_pivot = self.p.get("smc_pivot")
        self.ssl_baseline = self.p.get("ssl_baseline")
        self.tp_mult = self.p.get("tp_mult")
        self.mss_displacement = self.p.get("mss_displacement")
        self.adx_threshold = self.p.get("adx_threshold")
        self.min_rr = self.p.get("min_rr")
        self.min_liquidity_conf = self.p.get("min_liquidity_conf")
        self.sl_mult = self.p.get("sl_mult", 1.0)
        self.risk_per_trade = self.p.get("risk_per_trade")
        
        # OPUS EDITION: Liquidity Hunter Integration
        from app.brain.liquidity_hunter import LiquidityHunter
        self._hunter = LiquidityHunter()
        
        if symbol:
            self._initialized_for_symbol = symbol

    def _apply_legacy_overrides(self, symbol: str, kwargs: dict):
        """
        Keeps the original hardcoded values as highest priority IF NOT in DB.
        This provides a safety bridge during migration.
        """
        # If we didn't find specific params in DB, we use the old hardcoded logic
        # Note: If the user explicitly passed kwargs, they take precedence
        for k, v in kwargs.items():
            if k in self.p:
                self.p[k] = v

    def _apply_symbol_params(self, symbol: str, kwargs: dict):
        # Deprecated: functionality moved to __init__ via ParamLoader
        pass


    def _detect_smc_structure(self, df: pd.DataFrame, length: int | None = None) -> dict:
        """
        Calculates simple internal SMC structure context.
        """
        pivot_len = length if length is not None else self.smc_pivot
        pivots = smc_pivots(df['high'], df['low'], length=pivot_len)
        df['PivotHigh'] = pivots['PivotHigh']
        df['PivotLow'] = pivots['PivotLow']
        
        # Forward fill the last known pivots to understand current structure
        df['LastPH'] = df['PivotHigh'].ffill()
        df['LastPL'] = df['PivotLow'].ffill()
        
        # Check for Break of Structure (BOS)
        # Simplified: If close breaks last PH -> Bullish BOS, if it breaks last PL -> Bearish BOS
        # Note: True SMC looks for body closes, not just wicks, for strong structural breaks.
        close = df['close'].values
        last_ph = df['LastPH'].values
        last_pl = df['LastPL'].values
        
        # To avoid Look-Ahead Bias, we check structure *up to the last closed candle*
        current_ph = last_ph[-2] if not np.isnan(last_ph[-2]) else float('inf')
        current_pl = last_pl[-2] if not np.isnan(last_pl[-2]) else float('-inf')
        
        current_close = close[-1]
        
        bullish_bos = current_close > current_ph
        bearish_bos = current_close < current_pl
        
        structure = "NEUTRAL"
        if bullish_bos:
            structure = "BULLISH_BOS"
        elif bearish_bos:
            structure = "BEARISH_BOS"
        elif df['close'].iloc[-1] > ema(df['close'], 200).iloc[-1]: # Fallback trend
            structure = "BULLISH_TREND"
        else:
            structure = "BEARISH_TREND"
            
        return {
            "structure": structure,
            "last_ph": current_ph,
            "last_pl": current_pl
        }

    def _detect_liquidity_sweep(self, df: pd.DataFrame, last_ph: float, last_pl: float) -> str:
        """
        Detects if the current candle is a liquidity sweep.
        """
        curr_high = df['high'].iloc[-1]
        curr_low = df['low'].iloc[-1]
        curr_close = df['close'].iloc[-1]
        
        if curr_high > last_ph and curr_close < last_ph:
            return "BEARISH_SWEEP"
        if curr_low < last_pl and curr_close > last_pl:
            return "BULLISH_SWEEP"
        return "NONE"

    def _detect_fvg(self, df: pd.DataFrame) -> str:
        """
        Detects if current price is within a Fair Value Gap (FVG).
        A high-probability entry occurs when price retraces into an UNFILLED FVG.
        """
        if len(df) < 5: return "NONE"
        
        # Bullish FVG (Imbalance): Low of candle [i] > High of candle [i-2]
        # We look back a bit to see if there's a recent unfilled FVG
        for i in range(len(df)-1, len(df)-5, -1):
            low_i = df['low'].iloc[i]
            high_i_2 = df['high'].iloc[i-2]
            close_i_1 = df['close'].iloc[i-1]
            open_i_1 = df['open'].iloc[i-1]
            
            # Significant displacement candle [i-1]
            # Loosened from 0.001 to 0.0005 for better sample size
            if low_i > high_i_2 and abs(close_i_1 - open_i_1) > (df['close'].iloc[i-1] * 0.0005):
                # Price is currently inside or touching the gap
                curr_close = df['close'].iloc[-1]
                if curr_close >= high_i_2 and curr_close <= low_i:
                    return "BULLISH_FVG"
                    
        # Bearish FVG: High of candle [i] < Low of candle [i-2]
        for i in range(len(df)-1, len(df)-5, -1):
            high_i = df['high'].iloc[i]
            low_i_2 = df['low'].iloc[i-2]
            close_i_1 = df['close'].iloc[i-1]
            open_i_1 = df['open'].iloc[i-1]
            
            if high_i < low_i_2 and abs(close_i_1 - open_i_1) > (df['close'].iloc[i-1] * 0.0005):
                curr_close = df['close'].iloc[-1]
                if curr_close <= low_i_2 and curr_close >= high_i:
                    return "BEARISH_FVG"
                    
        return "NONE"

    def _is_session_active(self, symbol: str, current_time: Optional[datetime.datetime] = None) -> bool:
        """
        Returns True if the current time is within the optimal session for the symbol.
        For BTC/Crypto, we allow 24/7 if volatility is sufficient.
        """
        if current_time is None:
            current_time = datetime.datetime.now(datetime.timezone.utc)
        
        # Ensure current_time has hour attribute
        try:
            hour = current_time.hour
        except AttributeError:
            # Fallback for unexpected types
            return True
        
        # Crypto efficiency: 24/7 allowed
        if any(x in symbol.upper() for x in ["BTC", "ETH", "SOL"]):
            return True

        if "USOIL" in symbol.upper():
            return 13 <= hour <= 20 # NY Session for Energy
        if any(x in symbol.upper() for x in ["XAU", "XAG"]):
            return 12 <= hour <= 22 # NY Session for Metals (Extended)
        if any(x in symbol.upper() for x in ["US30", "USTEC", "NAS100", "DJ30"]):
            return 13 <= hour <= 21 # NY Session for Indices (Volume focus)
        if any(x in symbol.upper() for x in ["EURUSD", "GBPUSD", "USDJPY"]):
            # Global Majors: London (08-16) + NY (13-21)
            return 7 <= hour <= 21
        return True

    def analyze(
        self,
        df: pd.DataFrame,
        profile: SymbolProfile,
        regime: RegimeType | None = None,
        pressure: dict | None = None,
        **kwargs
    ) -> Decision:
        symbol = profile.symbol
        
        # Dynamic Initialization for Factory
        if self._initialized_for_symbol != symbol:
            self._apply_symbol_params(symbol, kwargs)
            
        if len(df) < 200:
            return Decision(
                symbol=symbol,
                action=Action.HOLD,
                confidence=0,
                reason="Not enough data (<200 candles)",
                strategy_name="alpha_v6_smc"
            )
            
        # --- 0. Session Filter (Efficiency Check) ---
        candle_time = None
        if 'time' in df.columns:
            try:
                candle_time = pd.to_datetime(df['time'].iloc[-1])
            except Exception:
                pass
        elif isinstance(df.index, pd.DatetimeIndex):
            candle_time = df.index[-1]

        if not self._is_session_active(symbol, candle_time):
            # print(f"DEBUG: {symbol} Out of Session: {candle_time}")
            return Decision(
                symbol=symbol,
                action=Action.HOLD,
                confidence=0,
                reason=f"Out of Session {candle_time.hour if candle_time else 'N/A'}",
                strategy_name="alpha_v6_smc"
            )
            
        # --- 1. SSL Hybrid Baseline (HMA) ---
        baseline_val = hma(df['close'], self.ssl_baseline).iloc[-1]
        
        # --- 2. QQE Mod ---
        qqe = qqe_mod(df['close'])
        qqe_up = qqe['QQE_Up'].iloc[-1]
        qqe_down = qqe['QQE_Down'].iloc[-1]
        
        # --- 3. Waddah Attar Explosion (WAE) ---
        w = wae(df['close'])
        wae_buy = w['WAE_Buy'].iloc[-1]
        wae_sell = w['WAE_Sell'].iloc[-1]
        
        # --- 4. SMC Structure Context ---
        structure_context = self._detect_smc_structure(df)
        struct = structure_context['structure']
        last_ph = structure_context['last_ph']
        last_pl = structure_context['last_pl']
        
        current_close = df['close'].iloc[-1]
        atr_val = atr(df['high'], df['low'], df['close'], 14).iloc[-1]
        
        # OPUS EDITION: Mandatory Liquidity Sweep
        liq_signal = self._hunter.scan(df, atr=atr_val)
        sweep = "NONE"
        if liq_signal.sweep_detected and liq_signal.confidence >= self.min_liquidity_conf:
            if liq_signal.sweep_direction == "BUY":
                sweep = "BULLISH_SWEEP"
            elif liq_signal.sweep_direction == "SELL":
                sweep = "BEARISH_SWEEP"
        
        # --- 4.5 New Filter Indicators (Bulls/Bears/Momentum/MACD/RSI) for BTC ---
        if "BTCUSD" in symbol:
            bp = bulls_power(df['high'], df['close'], 25).iloc[-1]
            brp = bears_power(df['low'], df['close'], 25).iloc[-1]
            mom = momentum(df['close'], 14).iloc[-1]
            m = macd(df['close'], 12, 26, 9)
            macdh = m['MACDh_12_26_9'].iloc[-1]
            r = rsi(df['close'], 14).iloc[-1]
            
            # --- Visual Indicators from Screenshot ---
            psar_val = psar(df['high'], df['low']).iloc[-1]
            ema20 = ema(df['close'], 20).iloc[-1]
            ema50 = ema(df['close'], 50).iloc[-1]
            ema100 = ema(df['close'], 100).iloc[-1]
            ema200 = ema(df['close'], 200).iloc[-1]
            
            # Trend alignment (Price vs EMAs)
            bullish_ribbon = (current_close > ema20 > ema50 > ema100 > ema200)
            bearish_ribbon = (current_close < ema20 < ema50 < ema100 < ema200)
            
            # PSAR alignment
            psar_bullish = current_close > psar_val
            psar_bearish = current_close < psar_val
            
            # Optimized confluence: Pulse + Momentum + (PSAR OR Ribbon) + (MACD OR RSI)
            is_bull_momentum_extra = (bp > 0) and (mom >= 100) and (psar_bullish or bullish_ribbon) and ((macdh > 0) or (r > 50))
            is_bear_momentum_extra = (brp < 0) and (mom <= 100) and (psar_bearish or bearish_ribbon) and ((macdh < 0) or (r < 50))
            
            # --- 4.6 FVG Context ---
            fvg_context = self._detect_fvg(df)
            is_fvg_ok = (fvg_context == "BULLISH_FVG" if is_bull_momentum_extra else (fvg_context == "BEARISH_FVG" if is_bear_momentum_extra else False))
            
            # --- 4.7 HTF Alignment (M15 -> H1 Shortcut using EMA 800) ---
            ema800 = ema(df['close'], 800).iloc[-1]
            is_htf_bullish = current_close > ema800
            is_htf_bearish = current_close < ema800
        elif any(x in symbol for x in ["US30", "USTEC", "NAS100", "DJ30"]):
            # Index Intelligence: FVG + Trend + Volume
            ema200 = ema(df['close'], 200).iloc[-1]
            fvg_context = self._detect_fvg(df)
            
            # Trend Alignment
            is_htf_bullish = current_close > ema200
            is_htf_bearish = current_close < ema200
            
            # WAE Explosion Requirement (Indices have high volume, scaling WAE threshold)
            wae_buy_strong = wae_buy > 0.0
            wae_sell_strong = wae_sell > 0.0
            
            # Confluence: FVG is a massive plus for Indices
            is_bull_momentum_extra = is_htf_bullish and wae_buy_strong
            is_bear_momentum_extra = is_htf_bearish and wae_sell_strong
            
            # FVG or Sweep is highly preferred
            has_fvg = (fvg_context == "BULLISH_FVG") if is_bull_momentum_extra else (fvg_context == "BEARISH_FVG" if is_bear_momentum_extra else False)
        else:
            is_bull_momentum_extra = True
            is_bear_momentum_extra = True
            has_fvg = False
        
        # --- 5. Signal Logic ---
        # Long criteria: SSL Momentum + (Bullish BOS OR Bullish Sweep)
        is_bullish_momentum = (current_close > baseline_val) and qqe_up and wae_buy
        is_bearish_momentum = (current_close < baseline_val) and qqe_down and wae_sell
        
        # Displacement Filter: Candle body or total range must be "strong"
        curr_range = abs(df['high'].iloc[-1] - df['low'].iloc[-1])
        is_displaced = curr_range >= (self.mss_displacement * atr_val)
        
        # Trend Strength Filter (ADX)
        adx_df = adx(df['high'], df['low'], df['close'], 14)
        curr_adx = adx_df[f'ADX_14'].iloc[-1]
        is_trending = curr_adx >= self.adx_threshold

        is_bullish_struct = (sweep == "BULLISH_SWEEP")
        is_bearish_struct = (sweep == "BEARISH_SWEEP")
        
        # Stricter Confluence: Trending + Momentum + Struct + Session
        session_ok = self._is_session_active(symbol, candle_time)
        
        # Final Signal logic: OPUS EDITION REQUIRES SWEEP!
        long_signal = (is_bullish_momentum and is_bullish_struct and is_bull_momentum_extra)
        short_signal = (is_bearish_momentum and is_bearish_struct and is_bear_momentum_extra)
        
        # --- Index Intelligence: Entry Refinement (NY Open & FVG) ---
        if any(x in symbol for x in ["US30", "USTEC"]):
            ny_open_window = False
            if candle_time and hasattr(candle_time, 'hour'):
                ny_open_window = (13 <= candle_time.hour <= 15) # First 3 hours of NY are most volatile
                
            # For indices, we require FVG OR Sweep OR Very Strong ADX to avoid churn.
            # However, during NY Open, if there's a sweep, it's a high conviction 'Judas Swing'
            if long_signal:
                if not (has_fvg or sweep != "NONE" or curr_adx > 25):
                    if ny_open_window and sweep == "BULLISH_SWEEP":
                        pass # Allow Judas Swing
                    else:
                        long_signal = False
            if short_signal:
                if not (has_fvg or sweep != "NONE" or curr_adx > 25):
                    if ny_open_window and sweep == "BEARISH_SWEEP":
                        pass
                    else:
                        short_signal = False
                        
            # Use smaller TP for non-trending sweeps to secure profit
            if long_signal and not is_trending and sweep == "BULLISH_SWEEP":
                self.tp_mult = 1.0
            elif short_signal and not is_trending and sweep == "BEARISH_SWEEP":
                self.tp_mult = 1.0
        # --- BTC Trend Filter (Strict EMA 200 + HTF + FVG) ---
        if "BTCUSD" in symbol:
            # BTC needs HTF Alignment and either FVG or Sweep for high winrate
            # Loosening: Allow if EMA 200 OR EMA 800 is aligned, BUT FVG/Sweep is mandatory
            if long_signal:
                if not (is_htf_bullish or (current_close > ema200)):
                    long_signal = False
                
                # FVG or Sweep is mandatory for sniper accuracy
                if sweep == "NONE" and fvg_context != "BULLISH_FVG":
                    long_signal = False
            
            if short_signal:
                if not (is_htf_bearish or (current_close < ema200)):
                    short_signal = False
                
                if sweep == "NONE" and fvg_context != "BEARISH_FVG":
                    short_signal = False
                    
            # Double check slow trend only if it's NOT a sweep (sweeps are reversals)
            if sweep == "NONE":
                if long_signal and current_close < ema200:
                    long_signal = False
                if short_signal and current_close > ema200:
                    short_signal = False
                    
            # --- Hard Risk Cap for $100 Accounts ---
            # If current equity is low, we cannot afford wide SLs.
            # We enforce a maximum SL distance in USD value for 0.01 lot.
            equity = kwargs.get("equity", 1000.0)
            if equity < 300:
                max_risk_usd = 5.0 # Max $5 risk per 0.01 lot
                # BTC point is 0.01, contract_size is 1.0. 
                # 0.01 lot * 1.0 * points = Profit.
                # So 100 points = $1.0. $5 risk = 500 points.
                max_sl_dist = 500.0
                
                if long_signal:
                    sl_dist = current_close - (last_pl - (0.5 * atr_val) if last_pl != float('-inf') else current_close - (2.5 * atr_val))
                    if sl_dist > max_sl_dist:
                        # Attempt to tighten to max allowed or skip
                        new_sl = current_close - max_sl_dist
                        # If new_sl is above pivot, it's safer. If not, we still enforce it.
                        pass # apply_cap_below
                    
                if short_signal:
                    sl_dist = (last_ph + (1.0 * atr_val) if last_ph != float('inf') else current_close + (2.5 * atr_val)) - current_close
                    if sl_dist > max_sl_dist:
                        pass
        if long_signal:
            sl_price = last_pl - (self.sl_mult * atr_val) if last_pl != float('-inf') else current_close - (2.5 * self.sl_mult * atr_val)
            
            # --- Enforce Hard Cap ---
            max_sl_dist = current_close * 0.02
            if (current_close - sl_price) > max_sl_dist:
                sl_price = current_close - max_sl_dist
            
            sl_dist = abs(current_close - sl_price)
            # OPUS TRADING: Sweeps have high win rate, push for higher RR
            tp_mult_final = max(2.5, self.tp_mult) if sweep == "BULLISH_SWEEP" else self.tp_mult
            tp_price = current_close + (sl_dist * tp_mult_final)
            
            # === MIN RR GATE (reject low-quality setups) ===
            actual_rr = (tp_price - current_close) / sl_dist if sl_dist > 0 else 0
            if actual_rr < getattr(self, 'min_rr', 1.5):
                return Decision(
                    symbol=symbol, action=Action.HOLD, confidence=0.0,
                    reason=f"RR {actual_rr:.2f} < min {self.min_rr}",
                    strategy_name="alpha_v6_smc"
                )
            
            confidence = 0.95 if session_ok and is_trending else 0.85
            
            return Decision(
                symbol=symbol,
                action=Action.BUY,
                strategy_name="alpha_v6_smc",
                confidence=confidence,
                reason=f"SSL + {struct} + {sweep} (ADX={curr_adx:.1f}, RR={actual_rr:.1f})",
                stop_loss=sl_price,
                take_profit=tp_price,
                tags=["sweep", sweep, "session_ok", str(session_ok), "adx", str(curr_adx)]
            )
            
        elif short_signal:
            sl_price = last_ph + (self.sl_mult * atr_val) if last_ph != float('inf') else current_close + (2.5 * self.sl_mult * atr_val)
            
            max_sl_dist = current_close * 0.02
            if (sl_price - current_close) > max_sl_dist:
                sl_price = current_close + max_sl_dist
            
            sl_dist = abs(sl_price - current_close)
            tp_mult_final = max(2.5, self.tp_mult) if sweep == "BEARISH_SWEEP" else self.tp_mult
            tp_price = current_close - (sl_dist * tp_mult_final)
            
            # === MIN RR GATE ===
            actual_rr = (current_close - tp_price) / sl_dist if sl_dist > 0 else 0
            if actual_rr < getattr(self, 'min_rr', 1.5):
                return Decision(
                    symbol=symbol, action=Action.HOLD, confidence=0.0,
                    reason=f"RR {actual_rr:.2f} < min {self.min_rr}",
                    strategy_name="alpha_v6_smc"
                )
            
            confidence = 0.95 if session_ok and is_trending else 0.85
            
            return Decision(
                symbol=symbol,
                action=Action.SELL,
                strategy_name="alpha_v6_smc",
                confidence=confidence,
                reason=f"SSL + {struct} + {sweep} (ADX={curr_adx:.1f}, RR={actual_rr:.1f})",
                stop_loss=sl_price,
                take_profit=tp_price,
                tags=["sweep", sweep, "session_ok", str(session_ok), "adx", str(curr_adx)]
            )
            
        return Decision(
            symbol=symbol,
            action=Action.HOLD,
            confidence=0.0,
            reason=f"No Setup. Struct: {struct}, Sweep: {sweep}",
            strategy_name="alpha_v6_smc"
        )

    def update_brain(self, outcome: dict) -> None:
        pass
