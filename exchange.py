import asyncio
import logging
from typing import Optional, Dict

from valr_python import Client, WebSocketClient
import aiohttp

from config import VALR_API_KEY, VALR_API_SECRET, LUNO_PAIR

logger = logging.getLogger(__name__)

class ExchangeInterface:
    def __init__(self):
        self.valr_client = Client(api_key=VALR_API_KEY, api_secret=VALR_API_SECRET)
        self.ws_client = None
        self.luno_base_url = "https://api.luno.com/api/1"
        self.session = None

    async def _get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return self.session

    async def _run_valr_sync(self, func, *args, **kwargs):
        retries = 3
        backoff = 1
        for i in range(retries):
            try:
                return await asyncio.to_thread(func, *args, **kwargs)
            except Exception as e:
                if '429' in str(e) or 'Too Many Requests' in str(e):
                    logger.warning(f"VALR HTTP 429. Retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                    backoff *= 2
                else:
                    raise e
        raise Exception("Max retries exceeded for VALR API endpoint.")

    async def get_valr_balances(self):
        """Fetch all VALR balances via REST."""
        return await self._run_valr_sync(self.valr_client.get_balances)

    async def get_valr_market_summary(self, pair: str):
        """Fetch current market summary (last price, bid, ask) for a specific pair."""
        try:
            result = await self._run_valr_sync(self.valr_client.get_market_summary, pair)
            return result
        except Exception as e:
            logger.error(f"Failed to get market summary for {pair}: {e}")
            return None

    async def get_valr_market_summaries(self):
        """Fetch market summaries for ALL pairs at once."""
        try:
            return await self._run_valr_sync(self.valr_client.get_market_summary)
        except Exception as e:
            logger.error(f"Failed to get all market summaries: {e}")
            return []

    async def place_valr_order(self, pair: str, side: str, amount: float, price: float):
        """Place a post-only limit order on VALR for any pair."""
        req = {
            "side": side.upper(),
            "quantity": str(amount),
            "price": str(price),
            "pair": pair,
            "postOnly": True
        }
        return await self._run_valr_sync(self.valr_client.post_limit_order, **req)

    async def get_valr_order_book(self, pair: str):
        return await self._run_valr_sync(self.valr_client.get_order_book_public, pair)

    async def get_luno_order_book(self, pair: str = LUNO_PAIR):
        session = await self._get_session()
        url = f"{self.luno_base_url}/orderbook_top?pair={pair}"
        retries = 3
        backoff = 1
        for i in range(retries):
            async with session.get(url) as response:
                if response.status == 429:
                    logger.warning(f"Luno HTTP 429. Retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                    backoff *= 2
                    continue
                response.raise_for_status()
                return await response.json()
        raise Exception("Max retries exceeded for Luno orderbook fetch.")

    async def start_ws(self, queue: asyncio.Queue):
        loop = asyncio.get_running_loop()

        def on_trade_hook(data):
            try:
                if isinstance(data, dict):
                    price = float(data.get("price", 0))
                    pair = data.get("currencyPairSymbol", "BTCZAR")
                    if price > 0:
                        asyncio.run_coroutine_threadsafe(queue.put({"pair": pair, "price": price}), loop)
            except Exception as e:
                logger.error(f"VALR WS Hook Error: {e}")

        hooks = {
            "NEW_TRADE": on_trade_hook,
            "AGGREGATED_ORDERBOOK_UPDATE": lambda data: None,
            "MARKET_SUMMARY_UPDATE": lambda data: None,
        }
        self.ws_client = WebSocketClient(api_key=VALR_API_KEY, api_secret=VALR_API_SECRET, hooks=hooks)

        retries = 5
        backoff = 2
        for i in range(retries):
            try:
                await self.ws_client.run()
                break
            except Exception as e:
                logger.warning(f"VALR WS Error ({e}). Retrying in {backoff}s...")
                await asyncio.sleep(backoff)
                backoff *= 2

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
