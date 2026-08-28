import asyncio
import unittest
from unittest.mock import patch

from config import DEFAULT_WATCHED_PAIRS, SUPPORTED_PAIRS
from exchange import ExchangeInterface


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


if __name__ == "__main__":
    unittest.main()
