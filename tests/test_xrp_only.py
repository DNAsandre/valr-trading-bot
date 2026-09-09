import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from config import DEFAULT_WATCHED_PAIRS, SUPPORTED_PAIRS
from exchange import ExchangeInterface
from telegram_bot import TelegramNotifier


class XrpZarOnlyTests(unittest.TestCase):
    def test_only_xrp_zar_is_supported_and_watched_by_default(self):
        self.assertEqual(SUPPORTED_PAIRS, ["XRPZAR"])
        self.assertEqual(DEFAULT_WATCHED_PAIRS, ["XRPZAR"])

    def test_exchange_rejects_non_xrp_zar_order_before_calling_valr(self):
        exchange = ExchangeInterface()

        def order_must_not_be_called(**kwargs):
            raise AssertionError("VALR order method must not be reached for a blocked pair")

        async def place_blocked_order():
            with patch.object(exchange.valr_client, "post_limit_order", order_must_not_be_called):
                with self.assertRaisesRegex(ValueError, "XRPZAR"):
                    await exchange.place_valr_order(
                        pair="BTCZAR", side="BUY", amount=0.001, price=1.0
                    )

        asyncio.run(place_blocked_order())

    def test_live_orders_require_autonomous_xrpzar_source(self):
        exchange = ExchangeInterface(execution_mode="live")

        def order_must_not_be_called(**kwargs):
            raise AssertionError("Manual or Telegram order must not reach VALR in live mode")

        async def place_manual_live_order():
            with patch.object(exchange.valr_client, "post_limit_order", order_must_not_be_called):
                with self.assertRaisesRegex(PermissionError, "autonomous"):
                    await exchange.place_valr_order(
                        pair="XRPZAR", side="BUY", amount=1.0, price=20.0
                    )

        asyncio.run(place_manual_live_order())

    def test_invalid_live_order_inputs_do_not_reach_valr(self):
        exchange = ExchangeInterface(execution_mode="live")

        def order_must_not_be_called(**kwargs):
            raise AssertionError("Invalid order input must not reach VALR")

        async def reject_invalid_orders():
            cases = (
                {"side": "HOLD", "amount": 1.0, "price": 20.0},
                {"side": "BUY", "amount": 0.0, "price": 20.0},
                {"side": "BUY", "amount": 1.0, "price": float("nan")},
            )
            with patch.object(exchange.valr_client, "post_limit_order", order_must_not_be_called):
                for case in cases:
                    with self.assertRaises(ValueError):
                        await exchange.place_valr_order(
                            pair="XRPZAR",
                            execution_source="autonomous_xrpzar",
                            **case,
                        )

        asyncio.run(reject_invalid_orders())

    def test_exchange_exposes_read_only_live_reconciliation_queries(self):
        exchange = ExchangeInterface()

        async def read_reconciliation_data():
            expected_open = [{"orderId": "open-1"}]
            expected_order = {"orderId": "order-1"}
            expected_trades = [{"orderId": "order-1"}]
            with (
                patch.object(exchange.valr_client, "get_all_open_orders", return_value=expected_open) as open_orders,
                patch.object(exchange.valr_client, "get_order_status", return_value=expected_order) as order_status,
                patch.object(exchange.valr_client, "get_trade_history", return_value=expected_trades) as history,
            ):
                self.assertEqual(await exchange.get_valr_open_orders(), expected_open)
                self.assertEqual(await exchange.get_valr_order_status("XRPZAR", "order-1"), expected_order)
                self.assertEqual(await exchange.get_xrp_zar_trade_history(), expected_trades)
            open_orders.assert_called_once_with()
            order_status.assert_called_once_with("XRPZAR", order_id="order-1")
            history.assert_called_once_with("XRPZAR")

        asyncio.run(read_reconciliation_data())

    def test_websocket_subscribes_to_xrp_zar_trades_only(self):
        class FakeWebSocketClient:
            init_kwargs: dict[str, Any] = {}

            def __init__(self, **kwargs):
                type(self).init_kwargs = kwargs

            async def run(self):
                return None

        exchange = ExchangeInterface()

        async def start_websocket_without_network():
            with patch("exchange.WebSocketClient", FakeWebSocketClient):
                await exchange.start_ws(asyncio.Queue())

        asyncio.run(start_websocket_without_network())
        self.assertEqual(FakeWebSocketClient.init_kwargs["currency_pairs"], ["XRPZAR"])
        self.assertEqual(
            FakeWebSocketClient.init_kwargs["trade_subscriptions"], ["NEW_TRADE"]
        )

    def test_unauthorized_start_cannot_add_user_to_trade_allowlist(self):
        unauthorized_user_id = 999_999_999
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=unauthorized_user_id),
            message=SimpleNamespace(reply_text=AsyncMock()),
        )

        async def start_as_unauthorized_user():
            allowed_users = [123_456_789]
            with patch("telegram_bot.TELEGRAM_ALLOWED_USERS", allowed_users):
                notifier = TelegramNotifier()
                await notifier.start(cast(Any, update), cast(Any, None))
                self.assertNotIn(unauthorized_user_id, allowed_users)
                update.message.reply_text.assert_not_awaited()

        asyncio.run(start_as_unauthorized_user())


if __name__ == "__main__":
    unittest.main()
