"""
Unification Strategy (God Mode) - ENHANCED
Synthesizes signals from V-Final, Omega Strike, Institutional Scalp,
Asian Range Breakout, Kill Zone Strategy, Pattern Recognition, and Antigravity.
"""
import pandas as pd
from typing import Dict, Any, Tuple

try:
    import app.analysis.indicators as ind
except ImportError:
    from app.utils.safe_ta import SafeTA as ta
from app.strategy.base_strategy import SignalType, StrategyDecision
from app.strategy.vfinal_strategy import v_final_strategy
from app.strategy.omega_strike_v2 import omega_strike_v2
from app.strategy.institutional_scalp import institutional_scalp
from app.strategy.asian_range_breakout import asian_range_breakout
from app.strategy.kill_zone_strategy import kill_zone_strategy
from app.ai.pattern_recognition import pattern_recognizer
from app.ai.market_intelligence import market_intel # NEW: Internet Knowledge
from app.services.order_flow_analyzer import order_flow_analyzer # NEW: Order Flow
from app.ai.evolution_engine import evolution_engine # NEW: SOS Engine
from app.services.market_analyzer import MarketAnalyzer, MarketRegime # NEW: Regime Analyzer
from app.strategy.ghost_protocol import ghost_protocol # NEW: Institutional Ghost Protocol
from app.strategy.antigravity import antigravity_strategy # NEW: Momentum Filter
from app.core.config_service import config_service

class UnificationStrategy:
    def __init__(self):
        self.name = "UNIFICATION"
        # Initial mapping, will be overriden by EvolutionEngine
        self.weights = {
            "V_FINAL": config_service.get("WEIGHT_V_FINAL", "STRATEGY_UNIFICATION", 1.5), 
            "INSTITUTIONAL": config_service.get("WEIGHT_INSTITUTIONAL", "STRATEGY_UNIFICATION", 1.5), 
            "OMEGA_STRIKE": config_service.get("WEIGHT_OMEGA_STRIKE", "STRATEGY_UNIFICATION", 2.0),
            "ASIAN_BREAKOUT": config_service.get("WEIGHT_ASIAN_BREAKOUT", "STRATEGY_UNIFICATION", 1.5), 
            "KILL_ZONE": config_service.get("WEIGHT_KILL_ZONE", "STRATEGY_UNIFICATION", 1.5), 
            "PATTERN": config_service.get("WEIGHT_PATTERN", "STRATEGY_UNIFICATION", 1.5),
            "SUPERTREND": config_service.get("WEIGHT_SUPERTREND", "STRATEGY_UNIFICATION", 1.5), 
            "RSI_DIV": config_service.get("WEIGHT_RSI_DIV", "STRATEGY_UNIFICATION", 2.0), 
            "BB_SQUEEZE": config_service.get("WEIGHT_BB_SQUEEZE", "STRATEGY_UNIFICATION", 1.5), 
            "ORDER_FLOW": config_service.get("WEIGHT_ORDER_FLOW", "STRATEGY_UNIFICATION", 2.0),
            "ANTIGRAVITY": config_service.get("WEIGHT_ANTIGRAVITY", "STRATEGY_UNIFICATION", 2.5), # HIGH WEIGHT for Momentum
        }
        self.threshold_strong = config_service.get("THRESHOLD_STRONG", "STRATEGY_UNIFICATION", 4.5)
        self.threshold_scalp = config_service.get("THRESHOLD_SCALP", "STRATEGY_UNIFICATION", 1.5)
        self.current_regime = MarketRegime.UNCERTAIN

    def _sync_dynamic_params(self, regime: MarketRegime, symbol: str):
        """SOS: Pull optimized weights and thresholds from Veteran Memory"""
        regime_str = regime.value if hasattr(regime, "value") else str(regime)
        if "TRENDING" in regime_str: key = "TRENDING"
        else: key = "SIDEWAYS"
        
        dyn_params = evolution_engine.get_params(key, symbol="GLOBAL")
        if dyn_params:
            # Dynamically update weights or thresholds (e.g. higher TP/RR for Trend)
            self.dynamic_tp_rr = dyn_params.get("take_profit_rr", 1.5)
            self.dynamic_sl_atr = dyn_params.get("stop_loss_atr", 1.5)
            # Dynamic Strictness: Allow AI to lower the bar if needed
            self.threshold_scalp = dyn_params.get("threshold_scalp", 1.5)
            # We could also evolve weights here in Phase 50+

    def analyze(self, df: pd.DataFrame, direction_mode: str = "AUTO", symbol: str = "XAUUSD", regime: MarketRegime = MarketRegime.UNCERTAIN) -> StrategyDecision:
        """
        Run all sub-strategies and vote. (FULL DYNAMIC MODE)
        """
        self.current_regime = regime
        self._sync_dynamic_params(regime, symbol)
        # 1. Get Signals independently from ALL 7 strategies
        s_vfinal = v_final_strategy.analyze(df, direction_mode="AUTO", symbol=symbol)
        s_inst = institutional_scalp.analyze(df, symbol=symbol)
        s_omega = omega_strike_v2.analyze(df, direction="AUTO")
        s_asian = asian_range_breakout.analyze(df, direction_mode="AUTO", symbol=symbol)
        s_killzone = kill_zone_strategy.analyze(df, direction_mode="AUTO", symbol=symbol)
        s_pattern = pattern_recognizer.analyze(df, symbol=symbol)
        s_orderflow = order_flow_analyzer.analyze(df) # NEW: Phase 37
        s_ghost = ghost_protocol.analyze(df) # NEW: Ghost Protocol (Hidden Order Blocks)
        s_antigravity = antigravity_strategy.analyze(df, direction="AUTO") # NEW: Momentum Check
        
        # NEW: SuperTrend Calculation (AI Research)
        # Using default length=7, factor=3
        # sti = df.ta.supertrend(length=7, multiplier=3.0)
        sti = ta.supertrend(df['high'], df['low'], df['close'], length=7, multiplier=3.0)
        s_supertrend = "WAIT"
        if sti is not None and not sti.empty:
             # Try to find the direction column dynamically
             # Usually starts with SUPERTd_
             dir_col = next((c for c in sti.columns if c.startswith('SUPERTd_')), None)
             if dir_col:
                 curr_dir = sti.iloc[-1][dir_col]
                 if curr_dir == 1: s_supertrend = "BUY"
                 elif curr_dir == -1: s_supertrend = "SELL"
        
        # NEW: RSI Divergence & BB Squeeze Calculation
        # 1. RSI Divergence
        rsi_div = "NONE"
        if 'rsi' in df.columns:
            curr_close = df['close'].iloc[-1]
            prev_close = df['close'].iloc[-2]
            curr_rsi = df['rsi'].iloc[-1]
            prev_rsi = df['rsi'].iloc[-2]
            # Simple 2-candle divergence check (Micro-Div)
            if curr_close < prev_close and curr_rsi > prev_rsi: rsi_div = "BULLISH"
            elif curr_close > prev_close and curr_rsi < prev_rsi: rsi_div = "BEARISH"
        
        # 2. BB Squeeze (Bollinger Bands vs Keltner Channels - Simplified)
        # Using BandWidth
        # bb = ind.bbands(df['close'], 20, 2.0)
        bb = ta.bbands(df['close'], length=20, std=2.0)
        bb_squeeze = False
        if bb is not None:
             # BBW = (Upper - Lower) / Middle
             try:
                 cols = bb.columns
                 upper_col = next((c for c in cols if c.startswith('BBU_')), None)
                 lower_col = next((c for c in cols if c.startswith('BBL_')), None)
                 middle_col = next((c for c in cols if c.startswith('BBM_')), None)
                 
                 if upper_col and lower_col and middle_col:
                     upper = bb[upper_col]
                     lower = bb[lower_col]
                     middle = bb[middle_col]
                     width = (upper - lower) / middle
                     current_width = width.iloc[-1]
                     # Relative Squeeze: Width < Average Width * 0.8
                     avg_width = width.rolling(20).mean().iloc[-1]
                     if current_width < avg_width * 0.8: # Squeeze is ON
                         bb_squeeze = True
             except Exception as e:
                 pass # Skip if BB calc issue (Silent for performance, but safe)

        # 2. Vote Calculation
        score = 0.0
        reasons = []
        confidences = []

        # Helper to normalize signal to string
        def get_sig_str(signal):
            return str(signal.value) if hasattr(signal, "value") else str(signal)

        # Process V_FINAL
        sig_vfinal = get_sig_str(s_vfinal.signal)
        if sig_vfinal == "BUY":
            score += self.weights["V_FINAL"]
            reasons.append(f"V-Final(BUY):{s_vfinal.reasons[0] if s_vfinal.reasons else 'Trend'}")
            confidences.append(s_vfinal.confidence)
        elif sig_vfinal == "SELL":
            score -= self.weights["V_FINAL"]
            reasons.append(f"V-Final(SELL):{s_vfinal.reasons[0] if s_vfinal.reasons else 'Trend'}")
            confidences.append(s_vfinal.confidence)

        # Process INSTITUTIONAL
        sig_inst = get_sig_str(s_inst.signal)
        if sig_inst == "BUY":
            score += self.weights["INSTITUTIONAL"]
            reasons.append(f"Inst(BUY):{s_inst.reasons[0] if s_inst.reasons else 'SMC'}")
            confidences.append(s_inst.confidence)
        elif sig_inst == "SELL":
            score -= self.weights["INSTITUTIONAL"]
            reasons.append(f"Inst(SELL):{s_inst.reasons[0] if s_inst.reasons else 'SMC'}")
            confidences.append(s_inst.confidence)

        # Process OMEGA STRIKE
        sig_omega = get_sig_str(s_omega.signal)
        if sig_omega == "BUY":
            score += self.weights["OMEGA_STRIKE"]
            reasons.append(f"Omega(BUY):{s_omega.reasons[0] if s_omega.reasons else 'Scalp'}")
            confidences.append(s_omega.confidence)
        elif sig_omega == "SELL":
            score -= self.weights["OMEGA_STRIKE"]
            reasons.append(f"Omega(SELL):{s_omega.reasons[0] if s_omega.reasons else 'Scalp'}")
            confidences.append(s_omega.confidence)

        # Process PATTERN RECOGNITION (Standard)
        if isinstance(s_pattern, dict):
            sig_pattern = s_pattern.get("signal", "WAIT")
            if sig_pattern == "BUY":
                score += self.weights["PATTERN"]
                reasons.append(f"Pat(BUY):{s_pattern.get('reason','Pattern')}")
            elif sig_pattern == "SELL":
                score -= self.weights["PATTERN"]
                reasons.append(f"Pat(SELL):{s_pattern.get('reason','Pattern')}")

        # Process ASIAN BREAKOUT (NEW)
        sig_asian = get_sig_str(s_asian.signal)
        if sig_asian == "BUY":
            score += self.weights["ASIAN_BREAKOUT"]
            reasons.append(f"Asian(BUY)")
            confidences.append(s_asian.confidence)
        elif sig_asian == "SELL":
            score -= self.weights["ASIAN_BREAKOUT"]
            reasons.append(f"Asian(SELL)")
            confidences.append(s_asian.confidence)

        # Process KILL ZONE (NEW)
        sig_killzone = get_sig_str(s_killzone.signal)
        if sig_killzone == "BUY":
            score += self.weights["KILL_ZONE"]
            reasons.append(f"KZ(BUY)")
            confidences.append(s_killzone.confidence)
        elif sig_killzone == "SELL":
            score -= self.weights["KILL_ZONE"]
            reasons.append(f"KZ(SELL)")
            confidences.append(s_killzone.confidence)

        # Process SUPERTREND (NEW)
        if s_supertrend == "BUY":
            score += self.weights["SUPERTREND"]
            reasons.append(f"SuperTrend(UP)")
        elif s_supertrend == "SELL":
            score -= self.weights["SUPERTREND"]
            reasons.append(f"SuperTrend(DOWN)")

        # Process RSI DIVERGENCE (NEW)
        if rsi_div == "BULLISH":
            score += self.weights["RSI_DIV"]
            reasons.append(f"div(BULL)")
        elif rsi_div == "BEARISH":
            score -= self.weights["RSI_DIV"]
            reasons.append(f"div(BEAR)")

        # Process BB SQUEEZE (NEW)
        if bb_squeeze:
            reasons.append(f"SQUEEZE")
            if score > 0: score += self.weights["BB_SQUEEZE"]
            elif score < 0: score -= self.weights["BB_SQUEEZE"]

        # Process ORDER FLOW (NEW)
        if s_orderflow["bias"] == "BULLISH":
            score += self.weights["ORDER_FLOW"] * s_orderflow["power"]
            reasons.append(f"OF(BUY):P{s_orderflow['power']}")
        elif s_orderflow["bias"] == "BEARISH":
            score -= self.weights["ORDER_FLOW"] * s_orderflow["power"]
            reasons.append(f"OF(SELL):P{s_orderflow['power']}")

        # Process ANTIGRAVITY (NEW: Momentum Edge)
        sig_anti = str(s_antigravity.signal.value) if hasattr(s_antigravity.signal, "value") else str(s_antigravity.signal)
        if sig_anti == "BUY":
            score += self.weights["ANTIGRAVITY"]
            reasons.append(f"MAG(BUY):{s_antigravity.reasons[0] if s_antigravity.reasons else 'Mom'}")
            confidences.append(s_antigravity.confidence)
        elif sig_anti == "SELL":
            score -= self.weights["ANTIGRAVITY"]
            reasons.append(f"MAG(SELL):{s_antigravity.reasons[0] if s_antigravity.reasons else 'Mom'}")
            confidences.append(s_antigravity.confidence)

        # Process GHOST PROTOCOL (NEW: World Class Efficiency)
        # High Weight (2.5) because Institutional levels are critical
        if s_ghost["signal"] == "BUY":
            score += 2.5
            reasons.append(f"GHOST(BUY):{s_ghost['pattern']}")
            confidences.append(s_ghost["confidence"])
            
            # GOLDEN FORMULA: GHOST + PATTERN COMBO
            if isinstance(s_pattern, dict) and s_pattern.get("signal") == "BUY":
                p_reason = s_pattern.get("reason", "")
                if "HEAD" in p_reason or "DOUBLE" in p_reason or "HAMMER" in p_reason or "ENGULFING" in p_reason:
                    score += 3.0 # SUPER BOOST
                    reasons.insert(0, "GOLDEN FORMULA(BUY)") # Priority Reason
                    confidences.append(95) # Max Confidence

        elif s_ghost["signal"] == "SELL":
            score -= 2.5
            reasons.append(f"GHOST(SELL):{s_ghost['pattern']}")
            confidences.append(s_ghost["confidence"])
            
            # GOLDEN FORMULA: GHOST + PATTERN COMBO
            if isinstance(s_pattern, dict) and s_pattern.get("signal") == "SELL":
                p_reason = s_pattern.get("reason", "")
                if "HEAD" in p_reason or "DOUBLE" in p_reason or "SHOOTING" in p_reason or "ENGULFING" in p_reason:
                    score -= 3.0 # SUPER BOOST
                    reasons.insert(0, "GOLDEN FORMULA(SELL)")
                    confidences.append(95)

        # 3. Decision Logic
        final_signal = SignalType.WAIT
        final_conf = 0.0
        risk_mult = 1.0
        
        avg_conf = sum(confidences) / len(confidences) if confidences else 0

        # NEW: Consult Internet Intelligence
        macro_mult = market_intel.analyze_macro_context()
        if macro_mult != 1.0:
            reasons.append(f"Macro({macro_mult}x)")

        # BUY Logic
        if score >= self.threshold_scalp: # Min threshold to trade
            if direction_mode in ["AUTO", "BUY", "BOTH"]:
                final_signal = SignalType.BUY
                final_conf = max(confidences) if confidences else 60
                
                # Risk Scaling
                if score >= self.threshold_strong:
                    risk_mult = 2.5 
                    reasons.insert(0, "GOD-BUY") # Short for MT5
                else:
                    risk_mult = 1.0 
                    reasons.insert(0, "SCALP-BUY")

        # SELL Logic
        elif score <= -self.threshold_scalp:
            if direction_mode in ["AUTO", "SELL", "BOTH"]:
                final_signal = SignalType.SELL
                final_conf = max(confidences) if confidences else 60
                
                # Risk Scaling
                if score <= -self.threshold_strong:
                    risk_mult = 2.5 
                    reasons.insert(0, "GOD-SELL")
                else:
                    risk_mult = 1.0 
                    reasons.insert(0, "SCALP-SELL")
        
        else:
            reasons = ["Scanning... (Score Balanced)"]

        # 3. Dynamic Stop Loss & Take Profit (AI Optimized)
        best_sl = None
        best_tp = None
        entry_price = df['close'].iloc[-1]
        signal = final_signal # Use the determined signal for SL/TP calculation

        # Prioritize Institutional SL (Tightest)
        if s_inst.signal in [SignalType.BUY, SignalType.SELL, "BUY", "SELL"] and s_inst.stop_loss:
            best_sl = s_inst.stop_loss
            best_tp = s_inst.take_profit
            entry_price = s_inst.entry_price if s_inst.entry_price else entry_price
        
        # Fallback to V_FINAL or Omega
        elif s_vfinal.signal in [SignalType.BUY, SignalType.SELL, "BUY", "SELL"] and s_vfinal.sl:
             best_sl = s_vfinal.sl
             best_tp = s_vfinal.tp
        elif s_omega.signal in [SignalType.BUY, SignalType.SELL, "BUY", "SELL"] and s_omega.stop_loss:
             best_sl = s_omega.stop_loss
             best_tp = s_omega.take_profit
        
        # Fallback to Antigravity
        elif s_antigravity.signal in [SignalType.BUY, SignalType.SELL, "BUY", "SELL"] and s_antigravity.stop_loss:
             best_sl = s_antigravity.stop_loss
             best_tp = s_antigravity.take_profit
             
        # Ultimate Fallback: ATR Calculation if all else fails but we have a signal
        if final_signal in [SignalType.BUY, SignalType.SELL]:
            if not best_sl:
                 atr = df['ta_atr_14'].iloc[-1] if 'ta_atr_14' in df.columns else (df['high'] - df['low']).mean()
                 sl_mult = getattr(self, "dynamic_sl_atr", 1.5)
                 tp_mult = getattr(self, "dynamic_tp_rr", 1.5) * sl_mult
                 
                 if final_signal == SignalType.BUY:
                      best_sl = entry_price - (atr * sl_mult)
                      best_tp = entry_price + (atr * tp_mult)
                 else:
                      best_sl = entry_price + (atr * sl_mult)
                      best_tp = entry_price - (atr * tp_mult)

        # 4. Construct Decision
        decision = StrategyDecision(
            signal=final_signal,
            confidence=final_conf,
            reason=" | ".join(reasons[:3]), # Top 3 reasons
            sl=best_sl,
            tp=best_tp,
            entry_price=entry_price,
            risk_multiplier=risk_mult * macro_mult # Apply Macro Multiplier
        )
        
        return decision

unification_strategy = UnificationStrategy()
