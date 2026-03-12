from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from app.analysis.indicators import adx, atr, bulls_power, bears_power, ema, macd, rsi
from app.domain.enums import Action
from app.domain.models import Decision, SymbolProfile
from app.strategy.base import BaseStrategy


_UTC = dt.timezone.utc
_NY_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class SessionContext:
    label: str
    is_prime: bool
    is_killzone: bool
    is_silver_bullet: bool
    is_trade_window: bool


class AlphaV7ICTStrategy(BaseStrategy):
    """
    Cross-asset ICT execution model.

    Core logic:
    - recent liquidity sweep
    - displacement away from the sweep
    - fair value gap created by that displacement
    - current bar retests the gap inside premium/discount territory
    - momentum and session filters confirm the setup
    """

    name = "alpha_v7_ict"
    asset_class = "*"
    timeframe = "M5"

    _DEFAULTS = {
        "allowed_timeframes": ["M5", "M15", "H1"],
        "ema_fast": 20,
        "ema_slow": 50,
        "ema_trend": 200,
        "adx_length": 14,
        "min_adx": 18.0,
        "rsi_length": 14,
        "rsi_buy_min": 52.0,
        "rsi_sell_max": 48.0,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
        "range_lookback": 36,
        "sweep_lookback": 24,
        "sweep_confirm_bars": 6,
        "sweep_tolerance_atr": 0.05,
        "displacement_window": 4,
        "min_displacement_atr": 0.48,
        "displacement_body_ratio": 0.50,
        "close_near_extreme_pct": 0.25,
        "fvg_lookback": 10,
        "fvg_min_size_atr": 0.14,
        "retest_buffer_atr": 0.14,
        "retest_lookback_bars": 3,
        "retest_hold_buffer_atr": 0.10,
        "max_reentry_distance_atr": 0.50,
        "stop_buffer_atr": 0.20,
        "premium_discount_buffer": 0.08,
        "min_rr": 1.80,
        "tp_rr": 2.40,
        "confidence_floor": 0.72,
        "require_prime_window": True,
        "crypto_off_window_min_adx_boost": 4.0,
        "flow_body_ratio": 0.62,
    }

    def __init__(self, symbol: Optional[str] = None, **kwargs):
        super().__init__()
        self.symbol = symbol or "UNKNOWN"
        self.p = dict(self._DEFAULTS)
        self._apply_overrides(kwargs)

    @classmethod
    def get_name(cls) -> str:
        return cls.name

    @classmethod
    def get_supported_timeframes(cls) -> list[str]:
        return ["M5", "M15", "H1"]

    def _apply_overrides(self, params: dict) -> None:
        for key, value in params.items():
            if value is not None:
                self.p[key] = value

    def _safe_float(self, value: object, fallback: float = 0.0) -> float:
        try:
            out = float(value)
        except Exception:
            return fallback
        if pd.isna(out):
            return fallback
        return out

    def _asset_class(self, symbol: str) -> str:
        sym = str(symbol or "").upper()
        if any(token in sym for token in ["XAU", "XAG", "GOLD", "SILVER"]):
            return "metals"
        if "BTC" in sym:
            return "crypto"
        if "OIL" in sym:
            return "energy"
        if any(token in sym for token in ["US30", "USTEC", "NAS", "DJ", "SPX", "GER"]):
            return "indices"
        if any(token in sym for token in ["EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "USD"]):
            return "forex"
        return "other"

    def _prepare_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        # Optimization: Skip if already prepared (DatetimeIndex + UTC)
        if hasattr(df.index, "tz") and df.index.tz is not None:
            return df
            
        frame = df.copy()
        if not isinstance(frame.index, pd.DatetimeIndex):
            if "time" in frame.columns:
                series = pd.to_datetime(frame["time"], utc=True, errors="coerce")
                frame.index = series
            else:
                try:
                    first_idx = frame.index[0]
                    unit = "ms" if float(first_idx) > 1e12 else "s"
                    frame.index = pd.to_datetime(frame.index, unit=unit, utc=True, errors="coerce")
                except:
                    pass
        elif frame.index.tz is None:
            frame.index = frame.index.tz_localize(_UTC)
        else:
            frame.index = frame.index.tz_convert(_UTC)

        frame = frame[~frame.index.duplicated(keep="last")]
        frame = frame.sort_index()
        return frame

    def _session_context(self, ts_utc: pd.Timestamp, asset_class: str) -> SessionContext:
        ts = pd.Timestamp(ts_utc)
        if ts.tzinfo is None:
            ts = ts.tz_localize(_UTC)
        else:
            ts = ts.tz_convert(_UTC)
        ny_ts = ts.tz_convert(_NY_TZ)
        minute = (ny_ts.hour * 60) + ny_ts.minute

        in_london = 120 <= minute < 480
        in_ny_am = 480 <= minute < 720
        in_ny_pm = 810 <= minute < 930

        is_killzone = (180 <= minute < 300) or (510 <= minute < 660) or (840 <= minute < 900)
        is_silver_bullet = (180 <= minute < 240) or (600 <= minute < 660) or (840 <= minute < 900)

        if 180 <= minute < 300:
            label = "LONDON_KZ"
        elif 510 <= minute < 660:
            label = "NY_AM_KZ"
        elif 840 <= minute < 900:
            label = "NY_PM_SB"
        elif in_london:
            label = "LONDON"
        elif in_ny_am:
            label = "NY_AM"
        elif in_ny_pm:
            label = "NY_PM"
        else:
            label = "OFF_WINDOW"

        is_prime = in_london or in_ny_am or in_ny_pm
        is_trade_window = True if asset_class == "crypto" else is_prime
        return SessionContext(
            label=label,
            is_prime=is_prime,
            is_killzone=is_killzone,
            is_silver_bullet=is_silver_bullet,
            is_trade_window=is_trade_window,
        )

    def _ny_open(self, frame: pd.DataFrame, ts_utc: pd.Timestamp) -> float:
        ny_index = frame.index.tz_convert(_NY_TZ)
        target_date = pd.Timestamp(ts_utc).tz_convert(_NY_TZ).date()
        same_day = frame.loc[ny_index.date == target_date]
        if same_day.empty:
            return float(frame["open"].iloc[-1])
        return float(same_day["open"].iloc[0])

    def _find_recent_sweep(self, frame: pd.DataFrame, side: str, atr_series: pd.Series) -> dict | None:
        lookback = int(self.p.get("sweep_lookback", 24))
        confirm_bars = int(self.p.get("sweep_confirm_bars", 6))
        start_idx = max(lookback, len(frame) - confirm_bars)

        for idx in range(len(frame) - 1, start_idx - 1, -1):
            hist = frame.iloc[idx - lookback:idx]
            if hist.empty:
                continue
            candle = frame.iloc[idx]
            atr_now = self._safe_float(atr_series.iloc[idx], 0.0)
            tol = atr_now * float(self.p.get("sweep_tolerance_atr", 0.05))
            prior_low = float(hist["low"].min())
            prior_high = float(hist["high"].max())

            if side == "BUY":
                if float(candle["low"]) <= (prior_low - tol) and float(candle["close"]) > prior_low:
                    return {
                        "idx": idx,
                        "side": side,
                        "liquidity_level": prior_low,
                        "extreme": float(candle["low"]),
                        "target": prior_high,
                    }
            else:
                if float(candle["high"]) >= (prior_high + tol) and float(candle["close"]) < prior_high:
                    return {
                        "idx": idx,
                        "side": side,
                        "liquidity_level": prior_high,
                        "extreme": float(candle["high"]),
                        "target": prior_low,
                    }
        return None

    def _find_displacement(self, frame: pd.DataFrame, side: str, sweep_idx: int, atr_series: pd.Series) -> dict | None:
        end_idx = min(len(frame) - 1, sweep_idx + int(self.p.get("displacement_window", 3)))
        body_ratio_min = float(self.p.get("displacement_body_ratio", 0.55))
        close_extreme_pct = float(self.p.get("close_near_extreme_pct", 0.25))
        atr_min = float(self.p.get("min_displacement_atr", 0.55))

        for idx in range(sweep_idx, end_idx + 1):
            candle = frame.iloc[idx]
            high = float(candle["high"])
            low = float(candle["low"])
            open_ = float(candle["open"])
            close = float(candle["close"])
            rng = high - low
            if rng <= 0:
                continue
            body = abs(close - open_)
            atr_now = self._safe_float(atr_series.iloc[idx], 0.0)
            body_ratio = body / rng

            if side == "BUY":
                close_near_extreme = (high - close) <= (rng * close_extreme_pct)
                directional = close > open_
            else:
                close_near_extreme = (close - low) <= (rng * close_extreme_pct)
                directional = close < open_

            if directional and body_ratio >= body_ratio_min and body >= (atr_now * atr_min):
                return {
                    "idx": idx,
                    "body": body,
                    "body_ratio": body_ratio,
                    "atr": atr_now,
                }
        return None

    def _find_recent_fvg(
        self,
        frame: pd.DataFrame,
        side: str,
        from_idx: int,
        atr_series: pd.Series,
    ) -> dict | None:
        end_idx = len(frame) - 2
        if end_idx < from_idx + 1:
            return None

        lookback = int(self.p.get("fvg_lookback", 8))
        min_idx = max(from_idx + 1, end_idx - lookback + 1)
        min_gap_atr = float(self.p.get("fvg_min_size_atr", 0.18))

        for idx in range(end_idx, min_idx - 1, -1):
            atr_now = self._safe_float(atr_series.iloc[idx], 0.0)
            if side == "BUY":
                zone_low = float(frame["high"].iloc[idx - 2])
                zone_high = float(frame["low"].iloc[idx])
                gap = zone_high - zone_low
                if gap >= (atr_now * min_gap_atr):
                    return {"idx": idx, "low": zone_low, "high": zone_high, "size": gap}
            else:
                zone_low = float(frame["high"].iloc[idx])
                zone_high = float(frame["low"].iloc[idx - 2])
                gap = zone_high - zone_low
                if gap >= (atr_now * min_gap_atr):
                    return {"idx": idx, "low": zone_low, "high": zone_high, "size": gap}
        return None

    def _fvg_retest_ok(self, frame: pd.DataFrame, side: str, fvg: dict, atr_now: float) -> bool:
        recent_lookback = max(1, int(self.p.get("retest_lookback_bars", 3)))
        recent = frame.iloc[-recent_lookback:]
        latest = recent.iloc[-1]
        close = float(latest["close"])
        high = float(latest["high"])
        low = float(latest["low"])
        touch_buffer = atr_now * float(self.p.get("retest_buffer_atr", 0.12))
        hold_buffer = atr_now * float(self.p.get("retest_hold_buffer_atr", 0.10))
        chase_limit = atr_now * float(self.p.get("max_reentry_distance_atr", 0.35))

        if side == "BUY":
            touched = bool((recent["low"] <= (float(fvg["high"]) + touch_buffer)).any())
            accepted = close >= (float(fvg["low"]) - hold_buffer)
            not_chased = close <= (float(fvg["high"]) + chase_limit)
            return touched and accepted and not_chased

        touched = bool((recent["high"] >= (float(fvg["low"]) - touch_buffer)).any())
        accepted = close <= (float(fvg["high"]) + hold_buffer)
        not_chased = close >= (float(fvg["low"]) - chase_limit)
        return touched and accepted and not_chased

    def _build_trade(
        self,
        frame: pd.DataFrame,
        side: str,
        symbol: str,
        session_ctx: SessionContext,
        sweep: dict,
        fvg: dict,
        atr_now: float,
        trend_aligned: bool,
        major_aligned: bool,
        pd_ok: bool,
        momentum_ok: bool,
        current_close: float,
    ) -> Decision:
        recent_slice = frame.iloc[sweep["idx"]:]
        stop_buffer = atr_now * float(self.p.get("stop_buffer_atr", 0.20))
        min_rr = float(self.p.get("min_rr", 1.80))
        base_tp_rr = max(min_rr, float(self.p.get("tp_rr", 2.40)))

        if side == "BUY":
            recent_low = float(recent_slice["low"].min())
            stop_loss = min(float(sweep["extreme"]), recent_low, float(fvg["low"])) - stop_buffer
            risk = current_close - stop_loss
            if risk <= 0:
                return self.create_hold(symbol, "ICT invalid buy stop")
            base_tp = current_close + (risk * base_tp_rr)
            structural_target = max(float(sweep["target"]), float(frame["high"].iloc[-int(self.p.get("range_lookback", 36)):].max()))
            take_profit = max(base_tp, structural_target)
            rr = (take_profit - current_close) / risk
            reason = f"ICT BUY: discount sweep + bullish FVG retest ({session_ctx.label})"
            tags = ["ICT", "BUY", "SWEEP", "FVG", session_ctx.label]
        else:
            recent_high = float(recent_slice["high"].max())
            stop_loss = max(float(sweep["extreme"]), recent_high, float(fvg["high"])) + stop_buffer
            risk = stop_loss - current_close
            if risk <= 0:
                return self.create_hold(symbol, "ICT invalid sell stop")
            base_tp = current_close - (risk * base_tp_rr)
            structural_target = min(float(sweep["target"]), float(frame["low"].iloc[-int(self.p.get("range_lookback", 36)):].min()))
            take_profit = min(base_tp, structural_target)
            rr = (current_close - take_profit) / risk
            reason = f"ICT SELL: premium sweep + bearish FVG retest ({session_ctx.label})"
            tags = ["ICT", "SELL", "SWEEP", "FVG", session_ctx.label]

        if rr < min_rr:
            return self.create_hold(symbol, f"ICT RR {rr:.2f} < {min_rr:.2f}")

        confidence = float(self.p.get("confidence_floor", 0.72))
        confidence += 0.07
        confidence += 0.07 if session_ctx.is_killzone else 0.03
        confidence += 0.04 if session_ctx.is_silver_bullet else 0.0
        confidence += 0.04 if pd_ok else 0.0
        confidence += 0.05 if trend_aligned else -0.03
        confidence += 0.03 if major_aligned else -0.02
        confidence += 0.04 if momentum_ok else 0.0
        confidence = max(0.55, min(0.96, confidence))

        return Decision(
            symbol=symbol,
            action=Action.BUY if side == "BUY" else Action.SELL,
            confidence=round(confidence, 3),
            reason=reason,
            stop_loss=round(stop_loss, 5),
            take_profit=round(take_profit, 5),
            risk_reward_ratio=round(rr, 3),
            strategy_name=self.name,
            timeframe=str(self.p.get("timeframe", self.timeframe)),
            tags=tags,
            extra={
                "session": session_ctx.label,
                "trend_aligned": trend_aligned,
                "major_aligned": major_aligned,
                "pd_ok": pd_ok,
                "fvg_low": round(float(fvg["low"]), 5),
                "fvg_high": round(float(fvg["high"]), 5),
                "sweep_index": int(sweep["idx"]),
            },
        )

    def analyze(self, df: pd.DataFrame, profile: SymbolProfile, regime: any = None, **kwargs) -> Decision:
        symbol = profile.symbol
        timeframe = str(kwargs.get("timeframe") or self.p.get("timeframe") or self.timeframe).upper()
        
        # Optimization: Use windowing if dataframe is large (backtest scenario)
        ema_len = int(self.p.get("ema_trend", 200))
        lookback_limit = max(ema_len, 100) + 50
        
        if len(df) > lookback_limit * 2:
            frame_full = self._prepare_frame(df)
            frame = frame_full.tail(lookback_limit)
        else:
            frame = self._prepare_frame(df)

        if len(frame) < ema_len + 10:
            return self.create_hold(symbol, "ICT waiting for warmup")

        allowed_timeframes = {str(tf).upper() for tf in self.p.get("allowed_timeframes", ["M5", "M15", "H1"])}
        if timeframe not in allowed_timeframes:
            return self.create_hold(symbol, f"ICT timeframe {timeframe} disabled")

        asset_class = self._asset_class(symbol)
        last_time = frame.index[-1]
        current_close = float(frame["close"].iloc[-1])

        session_ctx = self._session_context(last_time, asset_class)
        require_prime = bool(self.p.get("require_prime_window", True))
        if require_prime and not session_ctx.is_trade_window:
            return self.create_hold(symbol, f"ICT outside session window ({session_ctx.label})")

        # Indicators: Check if pre-calculated in dataframe columns (Optimization for Grid Search)
        if "atr" in frame.columns:
            atr_series = frame["atr"]
            atr_now = self._safe_float(atr_series.iloc[-1], 0.0)
        else:
            atr_series = atr(frame["high"], frame["low"], frame["close"], int(self.p.get("adx_length", 14)))
            atr_now = self._safe_float(atr_series.iloc[-1], 0.0)
            
        if atr_now <= 0:
            return self.create_hold(symbol, "ICT ATR unavailable")

        if "ema_fast" in frame.columns:
            ema_fast = float(frame["ema_fast"].iloc[-1])
            ema_slow = float(frame["ema_slow"].iloc[-1])
            ema_trend = float(frame["ema_trend"].iloc[-1])
        else:
            ema_fast = float(ema(frame["close"], int(self.p.get("ema_fast", 20))).iloc[-1])
            ema_slow = float(ema(frame["close"], int(self.p.get("ema_slow", 50))).iloc[-1])
            ema_trend = float(ema(frame["close"], ema_len).iloc[-1])

        if "adx" in frame.columns:
            adx_now = self._safe_float(frame["adx"].iloc[-1], 0.0)
            plus_di = self._safe_float(frame["plus_di"].iloc[-1], 0.0)
            minus_di = self._safe_float(frame["minus_di"].iloc[-1], 0.0)
        else:
            adx_len = int(self.p.get("adx_length", 14))
            adx_df = adx(frame["high"], frame["low"], frame["close"], adx_len)
            adx_now = self._safe_float(adx_df[f"ADX_{adx_len}"].iloc[-1], 0.0)
            plus_di = self._safe_float(adx_df[f"DMP_{adx_len}"].iloc[-1], 0.0)
            minus_di = self._safe_float(adx_df[f"DMN_{adx_len}"].iloc[-1], 0.0)

        if "rsi" in frame.columns:
            rsi_now = self._safe_float(frame["rsi"].iloc[-1], 0.0)
        else:
            rsi_len = int(self.p.get("rsi_length", 14))
            rsi_now = self._safe_float(rsi(frame["close"], rsi_len).iloc[-1], 0.0)

        if "macd_hist" in frame.columns:
            macd_hist = self._safe_float(frame["macd_hist"].iloc[-1], 0.0)
        else:
            m_fast = int(self.p.get("macd_fast", 12))
            m_slow = int(self.p.get("macd_slow", 26))
            m_sig = int(self.p.get("macd_signal", 9))
            macd_df = macd(frame["close"], m_fast, m_slow, m_sig)
            macd_hist = self._safe_float(macd_df[f"MACDh_{m_fast}_{m_slow}_{m_sig}"].iloc[-1], 0.0)

        if "bull_power" in frame.columns:
            bull_power = self._safe_float(frame["bull_power"].iloc[-1], 0.0)
            bear_power = self._safe_float(frame["bear_power"].iloc[-1], 0.0)
        else:
            bull_power = self._safe_float(bulls_power(frame["high"], frame["close"], 13).iloc[-1], 0.0)
            bear_power = self._safe_float(bears_power(frame["low"], frame["close"], 13).iloc[-1], 0.0)

        range_lookback = int(self.p.get("range_lookback", 36))
        dealing_range = frame.iloc[-range_lookback:]
        range_high = float(dealing_range["high"].max())
        range_low = float(dealing_range["low"].min())
        range_span = max(0.0, range_high - range_low)
        range_mid = range_low + (range_span * 0.5)
        pd_buffer = range_span * float(self.p.get("premium_discount_buffer", 0.10))
        ny_open = self._ny_open(frame, last_time)

        pd_tolerance = atr_now * 0.12
        is_discount = current_close <= (max(range_mid + pd_buffer, ny_open) + pd_tolerance)
        is_premium = current_close >= (min(range_mid - pd_buffer, ny_open) - pd_tolerance)

        min_adx = float(self.p.get("min_adx", 18.0))
        if asset_class == "crypto" and not session_ctx.is_prime:
            min_adx += float(self.p.get("crypto_off_window_min_adx_boost", 4.0))
        if adx_now < min_adx:
            return self.create_hold(symbol, f"ICT ADX {adx_now:.1f} < {min_adx:.1f}")

        buy_sweep = self._find_recent_sweep(frame, "BUY", atr_series)
        sell_sweep = self._find_recent_sweep(frame, "SELL", atr_series)
        buy_disp = self._find_displacement(frame, "BUY", buy_sweep["idx"], atr_series) if buy_sweep else None
        sell_disp = self._find_displacement(frame, "SELL", sell_sweep["idx"], atr_series) if sell_sweep else None
        buy_fvg = self._find_recent_fvg(frame, "BUY", buy_disp["idx"], atr_series) if buy_disp else None
        sell_fvg = self._find_recent_fvg(frame, "SELL", sell_disp["idx"], atr_series) if sell_disp else None

        buy_retest = buy_fvg and self._fvg_retest_ok(frame, "BUY", buy_fvg, atr_now)
        sell_retest = sell_fvg and self._fvg_retest_ok(frame, "SELL", sell_fvg, atr_now)

        buy_trend = current_close >= ema_slow and ema_fast >= ema_slow
        sell_trend = current_close <= ema_slow and ema_fast <= ema_slow
        buy_major = current_close >= ema_trend
        sell_major = current_close <= ema_trend

        buy_momentum = (
            (rsi_now >= float(self.p.get("rsi_buy_min", 52.0)) or current_close >= ema_fast)
            and (macd_hist > -0.02 or current_close >= ema_fast)
            and bull_power >= 0
            and (plus_di >= minus_di or current_close >= ema_fast)
        )
        sell_momentum = (
            (rsi_now <= float(self.p.get("rsi_sell_max", 48.0)) or current_close <= ema_fast)
            and (macd_hist < 0.02 or current_close <= ema_fast)
            and bear_power <= 0
            and (minus_di >= plus_di or current_close <= ema_fast)
        )

        buy_counter_ok = session_ctx.is_silver_bullet and buy_disp is not None and macd_hist > 0
        sell_counter_ok = session_ctx.is_silver_bullet and sell_disp is not None and macd_hist < 0
        flow_body_ratio = float(self.p.get("flow_body_ratio", 0.62))
        buy_flow_ok = buy_disp is not None and buy_disp["body_ratio"] >= flow_body_ratio
        sell_flow_ok = sell_disp is not None and sell_disp["body_ratio"] >= flow_body_ratio

        if buy_sweep and buy_disp and buy_fvg and buy_retest and (buy_momentum or buy_flow_ok) and is_discount and (buy_major or buy_counter_ok):
            return self._build_trade(
                frame=frame,
                side="BUY",
                symbol=symbol,
                session_ctx=session_ctx,
                sweep=buy_sweep,
                fvg=buy_fvg,
                atr_now=atr_now,
                trend_aligned=buy_trend,
                major_aligned=buy_major,
                pd_ok=is_discount,
                momentum_ok=(buy_momentum or buy_flow_ok),
                current_close=current_close,
            )

        if sell_sweep and sell_disp and sell_fvg and sell_retest and (sell_momentum or sell_flow_ok) and is_premium and (sell_major or sell_counter_ok):
            return self._build_trade(
                frame=frame,
                side="SELL",
                symbol=symbol,
                session_ctx=session_ctx,
                sweep=sell_sweep,
                fvg=sell_fvg,
                atr_now=atr_now,
                trend_aligned=sell_trend,
                major_aligned=sell_major,
                pd_ok=is_premium,
                momentum_ok=(sell_momentum or sell_flow_ok),
                current_close=current_close,
            )

        return self.create_hold(symbol, f"ICT waiting: {session_ctx.label} delivery not ready")
