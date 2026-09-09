import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from live_state import LiveState, LiveStateError


class LiveStateTests(unittest.TestCase):
    @staticmethod
    def order(*, order_id="buy-1", side="BUY", quantity="2", price="20", status="Open", filled_quantity="0", filled_value="0"):
        return {
            "orderId": order_id,
            "currencyPair": "XRPZAR",
            "side": side,
            "status": status,
            "originalQuantity": quantity,
            "price": price,
            "totalFilledQuantity": filled_quantity,
            "totalFilledValue": filled_value,
            "remainingQuantity": str(Decimal(quantity) - Decimal(filled_quantity)),
            "allowMargin": False,
            "type": "LIMIT",
        }

    @staticmethod
    def trade(*, trade_id="trade-1", order_id="buy-1", side="BUY", quantity="2", price="10", fee="1", fee_currency="ZAR"):
        return {
            "id": trade_id,
            "orderId": order_id,
            "currencyPair": "XRPZAR",
            "side": side,
            "quantity": quantity,
            "price": price,
            "fee": fee,
            "feeCurrency": fee_currency,
            "tradedAt": "2026-09-09T08:00:00Z",
        }

    def test_manual_xrp_balance_cannot_authorize_automated_sell(self):
        state = LiveState()

        with self.assertRaisesRegex(LiveStateError, "settled bot-owned XRP"):
            state.begin_order(side="SELL", requested_quantity="1", price="20")

    def test_pending_order_blocks_a_new_order(self):
        state = LiveState()
        state.begin_order(side="BUY", requested_quantity="2", price="20")
        state.accept_order(self.order())

        with self.assertRaisesRegex(LiveStateError, "pending order"):
            state.begin_order(side="BUY", requested_quantity="1", price="21")

    def test_partial_buy_fill_is_incremental_and_idempotent(self):
        state = LiveState()
        state.begin_order(side="BUY", requested_quantity="2", price="20")
        state.accept_order(self.order())
        partial = self.order(filled_quantity="1", filled_value="10")
        trade = self.trade(quantity="1", price="10")

        state.reconcile_order(partial, [trade])
        state.reconcile_order(partial, [trade])

        self.assertEqual(state.settled_xrp, Decimal("1"))
        self.assertEqual(state.lots, [{"quantity": Decimal("1"), "cost_zar": Decimal("11")}])
        self.assertEqual(state.pending_order["reconciled_filled_quantity"], Decimal("1"))
        self.assertEqual(state.pending_order["reconciled_filled_value"], Decimal("10"))
        self.assertEqual(state.daily_execution_count, 1)

    def test_sell_uses_fifo_cost_and_zar_fees_for_realized_pnl(self):
        state = LiveState()
        state.begin_order(side="BUY", requested_quantity="5", price="10")
        state.accept_order(self.order(quantity="5", price="10"))
        state.reconcile_order(
            self.order(quantity="5", price="10", status="Filled", filled_quantity="5", filled_value="50"),
            [self.trade(quantity="5", price="10", fee="5")],
        )
        state.begin_order(side="BUY", requested_quantity="5", price="20")
        state.accept_order(self.order(order_id="buy-2", quantity="5", price="20"))
        state.reconcile_order(
            self.order(order_id="buy-2", quantity="5", price="20", status="Filled", filled_quantity="5", filled_value="100"),
            [self.trade(trade_id="trade-2", order_id="buy-2", quantity="5", price="20", fee="0")],
        )
        state.begin_order(side="SELL", requested_quantity="7", price="30")
        state.accept_order(self.order(order_id="sell-1", side="SELL", quantity="7", price="30"))

        state.reconcile_order(
            self.order(order_id="sell-1", side="SELL", quantity="7", price="30", status="Filled", filled_quantity="7", filled_value="210"),
            [self.trade(trade_id="trade-3", order_id="sell-1", side="SELL", quantity="7", price="30", fee="7")],
        )

        self.assertEqual(state.settled_xrp, Decimal("3"))
        self.assertEqual(state.lots, [{"quantity": Decimal("3"), "cost_zar": Decimal("60")}])
        self.assertEqual(state.daily_realized_pnl_zar, Decimal("108"))
        self.assertIsNone(state.pending_order)

    def test_xrp_fees_reduce_owned_inventory_and_fifo_sell_cost(self):
        state = LiveState()
        state.begin_order(side="BUY", requested_quantity="5", price="10")
        state.accept_order(self.order(quantity="5", price="10"))
        state.reconcile_order(
            self.order(quantity="5", price="10", status="Filled", filled_quantity="5", filled_value="50"),
            [self.trade(quantity="5", price="10", fee="1", fee_currency="XRP")],
        )
        state.begin_order(side="SELL", requested_quantity="3", price="20")
        state.accept_order(self.order(order_id="sell-1", side="SELL", quantity="3", price="20"))

        state.reconcile_order(
            self.order(order_id="sell-1", side="SELL", quantity="3", price="20", status="Filled", filled_quantity="3", filled_value="60"),
            [self.trade(trade_id="trade-2", order_id="sell-1", side="SELL", quantity="3", price="20", fee="1", fee_currency="XRP")],
        )

        self.assertEqual(state.settled_xrp, Decimal("0"))
        self.assertEqual(state.daily_realized_pnl_zar, Decimal("10"))

    def test_persistence_round_trip_is_json_and_contains_no_secrets(self):
        state = LiveState()
        state.begin_order(side="BUY", requested_quantity="2", price="20")
        state.accept_order(self.order())
        state.reconcile_order(
            self.order(filled_quantity="1", filled_value="10"),
            [self.trade(quantity="1", price="10")],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live-state.json"
            state.save(path)
            saved = json.loads(path.read_text(encoding="utf-8"))
            restored = LiveState.load(path)

        self.assertEqual(restored.to_dict(), state.to_dict())
        self.assertNotIn("api_key", json.dumps(saved).lower())
        self.assertNotIn("secret", json.dumps(saved).lower())

    def test_persistence_preserves_a_daily_realized_loss(self):
        state = LiveState()
        state.daily_realized_pnl_zar = Decimal("-1.25")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live-state.json"
            state.save(path)
            restored = LiveState.load(path)

        self.assertEqual(restored.daily_realized_pnl_zar, Decimal("-1.25"))

    def test_load_fails_closed_for_missing_or_corrupt_state(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.json"
            with self.assertRaises(LiveStateError):
                LiveState.load(missing)
            corrupt = Path(directory) / "corrupt.json"
            corrupt.write_text("{not json", encoding="utf-8")
            with self.assertRaises(LiveStateError):
                LiveState.load(corrupt)

    def test_reconciliation_failure_leaves_ledger_unchanged(self):
        state = LiveState()
        state.lots = [{"quantity": Decimal("5"), "cost_zar": Decimal("50")}]
        state.begin_order(side="SELL", requested_quantity="5", price="20")
        state.accept_order(self.order(order_id="sell-1", side="SELL", quantity="5", price="20"))
        before = state.to_dict()
        first = self.trade(trade_id="first", order_id="sell-1", side="SELL", quantity="3", price="20", fee="0")
        second = self.trade(trade_id="second", order_id="sell-1", side="SELL", quantity="2", price="20", fee="1", fee_currency="XRP")

        with self.assertRaises(LiveStateError):
            state.reconcile_order(
                self.order(order_id="sell-1", side="SELL", quantity="5", price="20", status="Filled", filled_quantity="5", filled_value="100"),
                [first, second],
            )

        self.assertEqual(state.to_dict(), before)


if __name__ == "__main__":
    unittest.main()
