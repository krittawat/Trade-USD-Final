from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Iterable


_CLOSED_ENTRY = 1
_TRADE_DEAL_TYPES = {0, 1}
_CURRENCY_QUANT = Decimal("0.01")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_utc(dt: datetime | None, *, fallback: datetime | None = None) -> datetime:
    if dt is None:
        return fallback or utc_now()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def utc_day_start(reference: datetime | None = None) -> datetime:
    ref = normalize_utc(reference)
    return ref.replace(hour=0, minute=0, second=0, microsecond=0)


def is_closed_trade_deal(deal_obj) -> bool:
    return (
        getattr(deal_obj, "entry", None) == _CLOSED_ENTRY
        and getattr(deal_obj, "type", None) in _TRADE_DEAL_TYPES
    )


def realized_deal_pnl(deal_obj) -> float:
    total = 0.0
    for field_name in ("profit", "commission", "swap", "fee"):
        try:
            total += float(getattr(deal_obj, field_name, 0.0) or 0.0)
        except Exception:
            continue
    return total


def quantize_currency(value: float) -> float:
    try:
        normalized = Decimal(str(float(value or 0.0)))
    except Exception:
        normalized = Decimal("0.0")
    return float(normalized.quantize(_CURRENCY_QUANT, rounding=ROUND_HALF_UP))


def summarize_closed_trade_deals(deals: Iterable[object] | None) -> dict:
    ordered = sorted(
        list(deals or []),
        key=lambda deal: (
            int(getattr(deal, "time_msc", 0) or 0),
            int(getattr(deal, "time", 0) or 0),
            int(getattr(deal, "ticket", 0) or 0),
        ),
    )

    realized_pnl = 0.0
    consecutive_losses = 0
    closed_deals = []
    for deal in ordered:
        if not is_closed_trade_deal(deal):
            continue
        pnl = realized_deal_pnl(deal)
        realized_pnl += pnl
        consecutive_losses = consecutive_losses + 1 if pnl < 0 else 0
        closed_deals.append(deal)

    return {
        "realized_pnl": realized_pnl,
        "consecutive_losses": consecutive_losses,
        "closed_deal_count": len(closed_deals),
        "closed_deals": closed_deals,
    }


def get_closed_trade_summary(mt5_module, start_time: datetime | None, end_time: datetime | None = None) -> dict:
    start = normalize_utc(start_time, fallback=utc_day_start())
    end = normalize_utc(end_time, fallback=utc_now())
    deals = mt5_module.history_deals_get(start, end)
    summary = summarize_closed_trade_deals(deals)
    summary["start_time"] = start
    summary["end_time"] = end
    summary["deals"] = list(deals or [])
    return summary


def remaining_additional_loss_budget(daily_pnl: float, loss_limit_currency: float) -> float:
    limit = abs(quantize_currency(loss_limit_currency))
    if limit <= 0:
        return math.inf
    return max(0.0, quantize_currency(limit + quantize_currency(daily_pnl)))


def combined_realized_and_floating_pnl(realized_pnl: float, floating_pnl: float) -> float:
    return quantize_currency(quantize_currency(realized_pnl) + quantize_currency(floating_pnl))


def is_currency_loss_breached(pnl: float, loss_limit_currency: float) -> bool:
    limit = abs(quantize_currency(loss_limit_currency))
    if limit <= 0:
        return False
    return quantize_currency(pnl) <= -limit


def currency_loss_limit_to_dd_pct(
    loss_limit_currency: float,
    balance: float,
    fallback_pct: float,
) -> float:
    fallback = max(0.0, float(fallback_pct or 0.0))
    bal = max(0.0, float(balance or 0.0))
    limit = abs(float(loss_limit_currency or 0.0))
    if bal <= 0 or limit <= 0:
        return fallback
    return min(fallback, (limit / bal) * 100.0)
