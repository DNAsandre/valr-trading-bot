"""Durable, bot-owned accounting state for live XRP/ZAR execution.

This module deliberately records only fills reconciled from this bot's VALR orders.
It never imports exchange balance data, so manually acquired XRP cannot be sold by
an automated order.
"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo


SAST = ZoneInfo("Africa/Johannesburg")
PAIR = "XRPZAR"
TERMINAL_STATUSES = frozenset({"FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "CLOSED", "COMPLETED"})
_ORDER_FIELDS = frozenset({
    "orderId", "currencyPair", "side", "status", "originalQuantity", "price",
    "totalFilledQuantity", "totalFilledValue", "remainingQuantity", "allowMargin", "type",
})
_TRADE_FIELDS = frozenset({
    "id", "orderId", "currencyPair", "side", "quantity", "price", "fee", "feeCurrency", "tradedAt",
})


class LiveStateError(RuntimeError):
    """Raised when live execution state cannot safely be used."""


class LiveState:
    """Atomic, Decimal-based state for live orders and FIFO bot-owned XRP lots."""

    SCHEMA_VERSION = 1

    def __init__(self) -> None:
        today = self._sast_date(datetime.now(tz=SAST))
        self.lots: list[dict[str, Decimal]] = []
        self.pending_order: dict[str, object] | None = None
        self.sast_date = today
        self.daily_realized_pnl_zar = Decimal("0")
        self.daily_execution_count = 0
        self.cooldown_until: datetime | None = None

    @staticmethod
    def _decimal(
        value: object, field: str, *, positive: bool = False, allow_negative: bool = False
    ) -> Decimal:
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError) as error:
            raise LiveStateError(f"invalid {field}") from error
        if not result.is_finite() or (positive and result <= 0) or (not allow_negative and result < 0):
            raise LiveStateError(f"invalid {field}")
        return result

    @staticmethod
    def _sast_date(value: datetime) -> str:
        if value.tzinfo is None:
            raise LiveStateError("timestamp must include timezone")
        return value.astimezone(SAST).date().isoformat()

    @staticmethod
    def _parse_timestamp(value: object) -> datetime:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as error:
            raise LiveStateError("invalid trade timestamp") from error
        if parsed.tzinfo is None:
            raise LiveStateError("timestamp must include timezone")
        return parsed

    @property
    def settled_xrp(self) -> Decimal:
        return sum((lot["quantity"] for lot in self.lots), Decimal("0"))

    def _roll_risk_day(self, timestamp: datetime) -> None:
        date = self._sast_date(timestamp)
        if date != self.sast_date:
            self.sast_date = date
            self.daily_realized_pnl_zar = Decimal("0")
            self.daily_execution_count = 0
            self.cooldown_until = None

    def set_cooldown_until(self, timestamp: datetime | None) -> None:
        """Set an explicit SAST-aware cooldown; ``None`` clears it."""
        if timestamp is not None and timestamp.tzinfo is None:
            raise LiveStateError("cooldown timestamp must include timezone")
        self.cooldown_until = timestamp.astimezone(SAST) if timestamp else None

    def in_cooldown(self, timestamp: datetime | None = None) -> bool:
        now = timestamp or datetime.now(tz=SAST)
        if now.tzinfo is None:
            raise LiveStateError("timestamp must include timezone")
        self._roll_risk_day(now)
        return self.cooldown_until is not None and now.astimezone(SAST) < self.cooldown_until

    def begin_order(self, *, side: str, requested_quantity: object, price: object) -> None:
        """Reserve the sole execution slot before submitting an order to VALR."""
        normalized_side = str(side).upper()
        quantity = self._decimal(requested_quantity, "requested quantity", positive=True)
        order_price = self._decimal(price, "price", positive=True)
        if normalized_side not in {"BUY", "SELL"}:
            raise LiveStateError("invalid order side")
        if self.pending_order is not None:
            raise LiveStateError("pending order already exists")
        if self.in_cooldown():
            raise LiveStateError("cooldown is active")
        if normalized_side == "SELL" and quantity > self.settled_xrp:
            raise LiveStateError("SELL exceeds settled bot-owned XRP")
        self.pending_order = {
            "order_id": None,
            "side": normalized_side,
            "requested_quantity": quantity,
            "price": order_price,
            "reconciled_filled_quantity": Decimal("0"),
            "reconciled_filled_value": Decimal("0"),
            "seen_trade_ids": set(),
        }

    @staticmethod
    def _validate_order_shape(order: Mapping[str, object]) -> None:
        if not _ORDER_FIELDS.issubset(order):
            raise LiveStateError("incomplete VALR order")
        if order["currencyPair"] != PAIR:
            raise LiveStateError("unexpected currency pair")
        if str(order["side"]).upper() not in {"BUY", "SELL"}:
            raise LiveStateError("invalid VALR order side")

    def accept_order(self, order: Mapping[str, object]) -> None:
        """Attach the VALR order ID to the one previously reserved order."""
        if self.pending_order is None:
            raise LiveStateError("no pending order to accept")
        if self.pending_order["order_id"] is not None:
            raise LiveStateError("pending order already accepted")
        self._validate_order_shape(order)
        if str(order["side"]).upper() != self.pending_order["side"]:
            raise LiveStateError("unexpected VALR order")
        if self._decimal(order["originalQuantity"], "original quantity", positive=True) != self.pending_order["requested_quantity"]:
            raise LiveStateError("unexpected VALR order quantity")
        if self._decimal(order["price"], "order price", positive=True) != self.pending_order["price"]:
            raise LiveStateError("unexpected VALR order price")
        order_id = str(order["orderId"])
        if not order_id:
            raise LiveStateError("invalid order ID")
        self.pending_order["order_id"] = order_id

    @staticmethod
    def _validate_trade_shape(trade: Mapping[str, object], pending: Mapping[str, object]) -> None:
        if not _TRADE_FIELDS.issubset(trade):
            raise LiveStateError("incomplete VALR trade")
        if (
            trade["currencyPair"] != PAIR
            or str(trade["orderId"]) != pending["order_id"]
            or str(trade["side"]).upper() != pending["side"]
            or not str(trade["id"])
        ):
            raise LiveStateError("unexpected VALR trade")
        fee_currency = str(trade["feeCurrency"]).upper()
        if fee_currency not in {"XRP", "ZAR"}:
            raise LiveStateError("unsupported fee currency")

    def _consume_fifo(self, quantity: Decimal) -> Decimal:
        """Remove bot-owned XRP and return the consumed ZAR cost basis."""
        if quantity > self.settled_xrp:
            raise LiveStateError("SELL exceeds settled bot-owned XRP")
        remaining = quantity
        cost = Decimal("0")
        updated: list[dict[str, Decimal]] = []
        for lot in self.lots:
            if remaining == 0:
                updated.append(lot)
                continue
            taken = min(lot["quantity"], remaining)
            taken_cost = lot["cost_zar"] * taken / lot["quantity"]
            cost += taken_cost
            residual_quantity = lot["quantity"] - taken
            if residual_quantity:
                updated.append({"quantity": residual_quantity, "cost_zar": lot["cost_zar"] - taken_cost})
            remaining -= taken
        self.lots = updated
        return cost

    def _apply_trade(self, trade: Mapping[str, object]) -> None:
        assert self.pending_order is not None
        quantity = self._decimal(trade["quantity"], "trade quantity", positive=True)
        price = self._decimal(trade["price"], "trade price", positive=True)
        fee = self._decimal(trade["fee"], "trade fee")
        fee_currency = str(trade["feeCurrency"]).upper()
        side = str(trade["side"]).upper()
        timestamp = self._parse_timestamp(trade["tradedAt"])
        self._roll_risk_day(timestamp)
        gross_value = quantity * price
        if side == "BUY":
            owned_quantity = quantity - fee if fee_currency == "XRP" else quantity
            if owned_quantity <= 0:
                raise LiveStateError("BUY fee consumes all acquired XRP")
            cost = gross_value + fee if fee_currency == "ZAR" else gross_value
            self.lots.append({"quantity": owned_quantity, "cost_zar": cost})
        else:
            quantity_to_consume = quantity + fee if fee_currency == "XRP" else quantity
            cost = self._consume_fifo(quantity_to_consume)
            proceeds = gross_value - fee if fee_currency == "ZAR" else gross_value
            self.daily_realized_pnl_zar += proceeds - cost
        self.daily_execution_count += 1

    def reconcile_order(self, order: Mapping[str, object], trades: Iterable[Mapping[str, object]]) -> None:
        """Atomically apply unseen fills; retain the prior ledger on any mismatch."""
        candidate = deepcopy(self)
        candidate._reconcile_order(order, trades)
        self.__dict__.update(candidate.__dict__)

    def _reconcile_order(self, order: Mapping[str, object], trades: Iterable[Mapping[str, object]]) -> None:
        """Apply unseen fills exactly once; leave the order pending until terminal."""
        if self.pending_order is None or self.pending_order["order_id"] is None:
            raise LiveStateError("no accepted pending order")
        self._validate_order_shape(order)
        pending = self.pending_order
        if str(order["orderId"]) != pending["order_id"] or str(order["side"]).upper() != pending["side"]:
            raise LiveStateError("unexpected VALR order")
        original = self._decimal(order["originalQuantity"], "original quantity", positive=True)
        price = self._decimal(order["price"], "order price", positive=True)
        if original != pending["requested_quantity"] or price != pending["price"]:
            raise LiveStateError("changed pending order")
        filled_quantity = self._decimal(order["totalFilledQuantity"], "total filled quantity")
        filled_value = self._decimal(order["totalFilledValue"], "total filled value")
        if filled_quantity > original:
            raise LiveStateError("filled quantity exceeds requested quantity")
        prior_quantity = pending["reconciled_filled_quantity"]
        prior_value = pending["reconciled_filled_value"]
        assert isinstance(prior_quantity, Decimal) and isinstance(prior_value, Decimal)
        if filled_quantity < prior_quantity or filled_value < prior_value:
            raise LiveStateError("VALR cumulative fill moved backwards")
        unseen = []
        seen_ids = pending["seen_trade_ids"]
        assert isinstance(seen_ids, set)
        for trade in trades:
            self._validate_trade_shape(trade, pending)
            if str(trade["id"]) not in seen_ids:
                unseen.append(trade)
        incremental_quantity = sum((self._decimal(t["quantity"], "trade quantity", positive=True) for t in unseen), Decimal("0"))
        incremental_value = sum((self._decimal(t["quantity"], "trade quantity", positive=True) * self._decimal(t["price"], "trade price", positive=True) for t in unseen), Decimal("0"))
        if incremental_quantity != filled_quantity - prior_quantity or incremental_value != filled_value - prior_value:
            raise LiveStateError("trade history does not match cumulative fill")
        # Validate all fields and availability before mutating the ledger.
        for trade in unseen:
            fee = self._decimal(trade["fee"], "trade fee")
            if str(trade["side"]).upper() == "SELL" and str(trade["feeCurrency"]).upper() == "XRP":
                if self._decimal(trade["quantity"], "trade quantity", positive=True) + fee > self.settled_xrp:
                    raise LiveStateError("SELL exceeds settled bot-owned XRP")
            self._apply_trade(trade)
            seen_ids.add(str(trade["id"]))
        pending["reconciled_filled_quantity"] = filled_quantity
        pending["reconciled_filled_value"] = filled_value
        if str(order["status"]).upper() in TERMINAL_STATUSES:
            self.pending_order = None

    @staticmethod
    def _encoded_decimal(value: Decimal) -> str:
        return format(value, "f")

    def to_dict(self) -> dict[str, object]:
        """Return the complete non-secret JSON persistence representation."""
        pending: dict[str, object] | None = None
        if self.pending_order is not None:
            pending = {
                "order_id": self.pending_order["order_id"],
                "side": self.pending_order["side"],
                "requested_quantity": self._encoded_decimal(self.pending_order["requested_quantity"]),
                "price": self._encoded_decimal(self.pending_order["price"]),
                "reconciled_filled_quantity": self._encoded_decimal(self.pending_order["reconciled_filled_quantity"]),
                "reconciled_filled_value": self._encoded_decimal(self.pending_order["reconciled_filled_value"]),
                "seen_trade_ids": sorted(self.pending_order["seen_trade_ids"]),
            }
        return {
            "schema_version": self.SCHEMA_VERSION,
            "lots": [
                {"quantity": self._encoded_decimal(lot["quantity"]), "cost_zar": self._encoded_decimal(lot["cost_zar"])}
                for lot in self.lots
            ],
            "pending_order": pending,
            "risk": {
                "sast_date": self.sast_date,
                "daily_realized_pnl_zar": self._encoded_decimal(self.daily_realized_pnl_zar),
                "daily_execution_count": self.daily_execution_count,
                "cooldown_until": self.cooldown_until.isoformat() if self.cooldown_until else None,
            },
        }

    def save(self, path: str | Path) -> None:
        """Atomically replace JSON state without ever serializing credentials."""
        destination = Path(path)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    os.fchmod(handle.fileno(), 0o600)
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, destination)
            except Exception:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
                raise
        except (OSError, TypeError, ValueError) as error:
            raise LiveStateError("could not save live state") from error

    @classmethod
    def load(cls, path: str | Path) -> "LiveState":
        source = Path(path)
        try:
            with source.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            raise LiveStateError("could not load live state") from error
        try:
            if not isinstance(data, dict) or set(data) != {"schema_version", "lots", "pending_order", "risk"}:
                raise ValueError("invalid state document")
            if data["schema_version"] != cls.SCHEMA_VERSION or not isinstance(data["lots"], list) or not isinstance(data["risk"], dict):
                raise ValueError("unsupported state document")
            risk = data["risk"]
            if set(risk) != {"sast_date", "daily_realized_pnl_zar", "daily_execution_count", "cooldown_until"}:
                raise ValueError("invalid risk state")
            restored = cls.__new__(cls)
            restored.lots = []
            for lot in data["lots"]:
                if not isinstance(lot, dict) or set(lot) != {"quantity", "cost_zar"}:
                    raise ValueError("invalid lot")
                restored.lots.append({
                    "quantity": cls._decimal(lot["quantity"], "lot quantity", positive=True),
                    "cost_zar": cls._decimal(lot["cost_zar"], "lot cost"),
                })
            if not isinstance(risk["sast_date"], str):
                raise ValueError("invalid SAST date")
            datetime.fromisoformat(risk["sast_date"])
            restored.sast_date = risk["sast_date"]
            restored.daily_realized_pnl_zar = cls._decimal(
                risk["daily_realized_pnl_zar"], "daily realized P&L", allow_negative=True
            )
            if not isinstance(risk["daily_execution_count"], int) or risk["daily_execution_count"] < 0:
                raise ValueError("invalid execution count")
            restored.daily_execution_count = risk["daily_execution_count"]
            cooldown = risk["cooldown_until"]
            restored.cooldown_until = cls._parse_timestamp(cooldown) if cooldown is not None else None
            pending = data["pending_order"]
            if pending is None:
                restored.pending_order = None
            else:
                if not isinstance(pending, dict) or set(pending) != {
                    "order_id", "side", "requested_quantity", "price", "reconciled_filled_quantity", "reconciled_filled_value", "seen_trade_ids"
                }:
                    raise ValueError("invalid pending order")
                if pending["order_id"] is not None and not isinstance(pending["order_id"], str):
                    raise ValueError("invalid pending order ID")
                if pending["side"] not in {"BUY", "SELL"} or not isinstance(pending["seen_trade_ids"], list) or not all(isinstance(item, str) and item for item in pending["seen_trade_ids"]):
                    raise ValueError("invalid pending order")
                restored.pending_order = {
                    "order_id": pending["order_id"],
                    "side": pending["side"],
                    "requested_quantity": cls._decimal(pending["requested_quantity"], "requested quantity", positive=True),
                    "price": cls._decimal(pending["price"], "price", positive=True),
                    "reconciled_filled_quantity": cls._decimal(pending["reconciled_filled_quantity"], "reconciled quantity"),
                    "reconciled_filled_value": cls._decimal(pending["reconciled_filled_value"], "reconciled value"),
                    "seen_trade_ids": set(pending["seen_trade_ids"]),
                }
            return restored
        except (KeyError, TypeError, ValueError, InvalidOperation, LiveStateError) as error:
            raise LiveStateError("corrupt live state") from error
