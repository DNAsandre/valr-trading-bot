"""Pure, fee-aware XRP/ZAR backtesting helpers. No credentials or order endpoints."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Callable, Sequence


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def run_backtest(
    candles: Sequence[dict],
    *,
    signal_provider: Callable[[int, Sequence[dict]], str | None],
    starting_zar: float = 1_000.0,
    position_size_pct: float = 0.02,
    fee_pct: float = 0.002,
    cooldown_seconds: int = 900,
    max_trades_per_day: int = 3,
    max_daily_loss_zar: float = 50.0,
) -> dict[str, float | int]:
    """Simulate closed-candle XRP/ZAR signals with v2 position/risk limits."""
    if starting_zar <= 0 or not 0 < position_size_pct <= 1 or not 0 <= fee_pct < 1:
        raise ValueError("invalid backtest capital, position size, or fee")
    zar = float(starting_zar)
    xrp = 0.0
    open_cost = 0.0
    realized = 0.0
    fills = 0
    last_trade_at: datetime | None = None
    day_fills: dict[str, int] = defaultdict(int)
    day_realized: dict[str, float] = defaultdict(float)

    for index, candle in enumerate(candles):
        price = float(candle["close"])
        if price <= 0:
            continue
        at = _parse_time(candle["startTime"])
        day = at.date().isoformat()
        if day_fills[day] >= max_trades_per_day or day_realized[day] <= -max_daily_loss_zar:
            continue
        if last_trade_at and (at - last_trade_at).total_seconds() < cooldown_seconds:
            continue
        signal = (signal_provider(index, candles) or "").upper()
        if signal == "BUY" and xrp == 0.0:
            notional = zar * position_size_pct
            quantity = notional / price
            fee = notional * fee_pct
            total_cost = notional + fee
            if total_cost > zar:
                continue
            zar -= total_cost
            xrp = quantity
            open_cost = total_cost
            fills += 1
            day_fills[day] += 1
            last_trade_at = at
        elif signal == "SELL" and xrp > 0.0:
            gross = xrp * price
            proceeds = gross * (1 - fee_pct)
            pnl = proceeds - open_cost
            zar += proceeds
            xrp = 0.0
            open_cost = 0.0
            realized += pnl
            day_realized[day] += pnl
            fills += 1
            day_fills[day] += 1
            last_trade_at = at

    last_price = float(candles[-1]["close"]) if candles else 0.0
    ending_equity = zar + (xrp * last_price)
    return {
        "fills": fills,
        "realized_pnl_zar": realized,
        "ending_equity_zar": ending_equity,
        "unrealized_pnl_zar": ending_equity - starting_zar - realized,
        "open_xrp": xrp,
    }
