from .liquidity_hunter import signal_liquidity_hunter
from .trend_killer import signal_trend_killer
import pandas as pd


def select_and_generate_signal(df: pd.DataFrame, context: dict, events: list) -> dict:
    """
    Strategy selector.
    Rules:
    - Compression -> No trade
    - Extreme Expansion (>2.5x baseline) -> No trade
    - Strong/Weak Trend -> Trend Killer first, then Liquidity Hunter fallback
    - Distribution/Accumulation/Sideways -> Liquidity Hunter first, then Trend Killer fallback
    """
    regime = context.get("regime_result", {})
    regime_name = regime.get("regime", "UNKNOWN")

    if regime_name == "Volatility Compression":
        return None

    if "atr" in df.columns and "atr_baseline" in df.columns:
        if regime_name == "Volatility Expansion" and df["atr"].iloc[-1] > df["atr_baseline"].iloc[-1] * 2.5:
            return None

    if "Trend" in regime_name or "Expansion" in regime_name:
        sig = signal_trend_killer(df, context)
        if sig:
            return sig
        if events:
            sig = signal_liquidity_hunter(df, events, context)
            if sig:
                return sig

    if any(r in regime_name for r in ["Sideways", "Distribution", "Accumulation"]):
        if events:
            sig = signal_liquidity_hunter(df, events, context)
            if sig:
                return sig
        sig = signal_trend_killer(df, context)
        if sig:
            return sig

    return None
