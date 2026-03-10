import logging
import pandas as pd
import pandas_ta as ta

from backend.trader.brain.deep_brain import get_brain

logger = logging.getLogger("ai_brain_strategy")

def detect_regime(df: pd.DataFrame) -> dict:
    """Analyze dataframe to determine market regime using ADX and SMA"""
    if len(df) < 70:
        return {"regime": "UNCERTAIN"}

    if 'ADX_14' not in df.columns:
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx is not None:
             df = pd.concat([df, adx], axis=1)

    adx_val = df['ADX_14'].iloc[-1] if 'ADX_14' in df.columns else 20.0
    sma14 = ta.sma(df['close'], length=14).iloc[-1]
    sma70 = ta.sma(df['close'], length=70).iloc[-1]
    close = df['close'].iloc[-1]

    sma_spread = abs(sma14 - sma70) / sma70
    is_trend_strong = adx_val > 25.0 and sma_spread > 0.005

    if is_trend_strong:
        if sma14 > sma70 and close > sma70:
            return {"regime": "TREND_BULL"}
        elif sma14 < sma70 and close < sma70:
            return {"regime": "TREND_BEAR"}

    if adx_val < 20.0 or sma_spread < 0.0025:
         return {"regime": "RANGE"}
        
    return {"regime": "UNCERTAIN"}


def signal_ai_brain(df: pd.DataFrame, context: dict) -> dict:
    """
    Antigravity Alpha V5 — Institutional Intelligence
    Combines Deep AI + PD Zones + Session Sweeps + Divergence
    """
    if len(df) < 100:
        return None

    symbol = context.get('symbol', 'UNKNOWN')
    brain = get_brain(symbol)

    # 1. AI Deep Brain Prediction
    ai_pred, ai_prob = brain.predict_next_move(df)
    
    # 2. Market Regime Classifier
    regime_info = detect_regime(df)
    regime = regime_info["regime"]
    
    # 3. Institutional Signatures (Alpha V5)
    latest = df.iloc[-1]
    htf_trend = context.get('htf_ema_align', 'UNCERTAIN')
    pd_zone = latest.get('pd_zone', 'EQUILIBRIUM')
    fvg_bull = latest.get('fvg_bull', 0) > 0
    fvg_bear = latest.get('fvg_bear', 0) > 0
    near_ob_bull = latest.get('ob_bull', False)
    near_ob_bear = latest.get('ob_bear', False)
    sweep_high = latest.get('sweep_high', False)
    sweep_low = latest.get('sweep_low', False)
    div_bull = latest.get('div_bull', False)
    div_bear = latest.get('div_bear', False)
    
    # 4. Alpha V5 Confluence Logic
    # Core Requirement: AI Prob > 0.65
    if ai_prob < 0.65:
        return None

    close = float(latest['close'])
    atr = float(ta.atr(df['high'], df['low'], df['close'], length=14).iloc[-1])
    
    # BULLISH SIGNAL (BUY)
    if ai_pred == "BUY" and (regime != "TREND_BEAR") and (htf_trend != "BEARISH"):
        # Alpha V5 Filter: Buy only in Discount or Equilibrium (Avoid buying Premium)
        if pd_zone == "PREMIUM":
            return None
            
        # Confluence Weighting
        confluence_score = ai_prob
        confluence_score += 0.05 if fvg_bull else 0
        confluence_score += 0.05 if near_ob_bull else 0
        confluence_score += 0.10 if sweep_low else 0
        confluence_score += 0.05 if div_bull else 0
        confluence_score = min(1.0, confluence_score)
        
        rationale = [f"AI Conf: {ai_prob*100:.1f}%", f"Zone: {pd_zone}", f"HTF: {htf_trend}"]
        if sweep_low: rationale.append("Session Liquidity Sweep Found")
        if div_bull: rationale.append("RSI Bullish Divergence")
        if near_ob_bull: rationale.append("Institutional Order Block")
        
        return {
            "symbol": symbol, "side": "BUY", "entry_type": "MARKET",
            "entry_price": close, "sl": round(close - (atr * 0.9), 5),
            "tp1": round(close + (atr * 1.2), 5), 
            "tp2": round(close + (atr * 2.5), 5), 
            "tp3": round(close + (atr * 4.0), 5),
            "rationale": rationale, "confidence": confluence_score,
            "model": "ALPHA_V5_AI",
        }
        
    # BEARISH SIGNAL (SELL)
    elif ai_pred == "SELL" and (regime != "TREND_BULL") and (htf_trend != "BULLISH"):
        # Alpha V5 Filter: Sell only in Premium or Equilibrium (Avoid selling Discount)
        if pd_zone == "DISCOUNT":
            return None
            
        confluence_score = ai_prob
        confluence_score += 0.05 if fvg_bear else 0
        confluence_score += 0.05 if near_ob_bear else 0
        confluence_score += 0.10 if sweep_high else 0
        confluence_score += 0.05 if div_bear else 0
        confluence_score = min(1.0, confluence_score)
        
        rationale = [f"AI Conf: {ai_prob*100:.1f}%", f"Zone: {pd_zone}", f"HTF: {htf_trend}"]
        if sweep_high: rationale.append("Session Liquidity Sweep Found")
        if div_bear: rationale.append("RSI Bearish Divergence")
        if near_ob_bear: rationale.append("Institutional Order Block")
        
        return {
            "symbol": symbol, "side": "SELL", "entry_type": "MARKET",
            "entry_price": close, "sl": round(close + (atr * 0.9), 5),
            "tp1": round(close - (atr * 1.2), 5), 
            "tp2": round(close - (atr * 2.5), 5), 
            "tp3": round(close - (atr * 4.0), 5),
            "rationale": rationale, "confidence": confluence_score,
            "model": "ALPHA_V5_AI",
        }

    return None

    return None
