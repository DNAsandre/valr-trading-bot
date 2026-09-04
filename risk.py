"""Stateful, exchange-independent execution limits for the trading loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str | None = None


class TradingRiskGuard:
    def __init__(
        self,
        *,
        max_daily_loss_zar: float,
        cooldown_seconds: int,
        max_trades_per_day: int,
    ) -> None:
        if max_daily_loss_zar <= 0:
            raise ValueError("max_daily_loss_zar must be positive")
        if cooldown_seconds < 0:
            raise ValueError("cooldown_seconds cannot be negative")
        if max_trades_per_day <= 0:
            raise ValueError("max_trades_per_day must be positive")
        self.max_daily_loss_zar = max_daily_loss_zar
        self.cooldown_seconds = cooldown_seconds
        self.max_trades_per_day = max_trades_per_day
        self._day: date | None = None
        self._daily_realized_pnl_zar = 0.0
        self._daily_trade_count = 0
        self._last_execution_at: datetime | None = None

    def _reset_if_new_day(self, now: datetime) -> None:
        if self._day != now.date():
            self._day = now.date()
            self._daily_realized_pnl_zar = 0.0
            self._daily_trade_count = 0
            self._last_execution_at = None

    def can_execute(self, now: datetime) -> RiskDecision:
        self._reset_if_new_day(now)
        if self._daily_realized_pnl_zar <= -self.max_daily_loss_zar:
            return RiskDecision(False, "daily_loss_limit")
        if self._daily_trade_count >= self.max_trades_per_day:
            return RiskDecision(False, "daily_trade_limit")
        if self._last_execution_at is not None:
            seconds_since_execution = (now - self._last_execution_at).total_seconds()
            if seconds_since_execution < self.cooldown_seconds:
                return RiskDecision(False, "trade_cooldown")
        return RiskDecision(True)

    def record_execution(self, now: datetime, *, realized_pnl_zar: float) -> None:
        self._reset_if_new_day(now)
        self._daily_trade_count += 1
        self._daily_realized_pnl_zar += realized_pnl_zar
        self._last_execution_at = now
