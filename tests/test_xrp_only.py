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
