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

    async def get_all_zar_market_summaries(self) -> list:
        """Fetch market summaries for ALL ZAR-denominated pairs on VALR.
        Returns sorted list with price, change %, volume, high, low."""
        try:
            all_summaries = await self.get_valr_market_summaries()
            zar_pairs = []
            for s in all_summaries:
                pair = s.get('currencyPair', '')
                if not pair.endswith('ZAR'):
                    continue
                # Skip stablecoin/ZAR pairs (not useful for growth trading)
                base = pair.replace('ZAR', '')
                if base in ('USDC', 'USDT', 'BUSD', 'DAI', 'TUSD'):
                    continue
                try:
                    last_price = float(s.get('lastTradedPrice', 0))
                    change_pct = float(s.get('changeFromPrevious', 0))
                    base_volume = float(s.get('baseVolume', 0))
                    high_price = float(s.get('highPrice', 0))
                    low_price = float(s.get('lowPrice', 0))
                    if last_price <= 0:
                        continue
                    zar_pairs.append({
                        'pair': pair,
                        'base': base,
                        'lastPrice': last_price,
                        'changePct': change_pct,
                        'volume': base_volume,
                        'high': high_price,
                        'low': low_price,
                    })
                except (ValueError, TypeError):
                    continue
            # Sort by change % descending (biggest gainers first)
            zar_pairs.sort(key=lambda x: x['changePct'], reverse=True)
            return zar_pairs
        except Exception as e:
            logger.error(f"Failed to get all ZAR market summaries: {e}")
            return []

    async def get_portfolio_value_zar(self) -> float:
        """Calculates total portfolio value in ZAR."""
        try:
            balances = await self.get_valr_balances()
            summaries = await self.get_valr_market_summaries()

            # Create a lookup for last traded prices for pairs ending in ZAR
            price_map = {}
            for summary in summaries:
                pair = summary.get('currencyPair')
                if pair and pair.endswith('ZAR'):
                    price_map[pair] = float(summary.get('lastTradedPrice', 0))

            total_zar = 0.0
            for bal in balances:
                currency = bal.get('currency')
                total_amt = float(bal.get('total', 0))

                if total_amt <= 0:
                    continue

                if currency == 'ZAR':
                    total_zar += total_amt
                else:
                    pair = f"{currency}ZAR"
                    if pair in price_map:
                        total_zar += total_amt * price_map[pair]

            return total_zar
        except Exception as e:
            logger.error(f"Failed to calculate portfolio value: {e}")
            return 0.0

    async def get_average_buy_price(self, pair: str, current_balance: float = 0.0) -> float:
        """Calculate weighted average buy price of the current holdings."""
        try:
            history = await self._run_valr_sync(self.valr_client.get_trade_history, pair)
            if not history:
                return 0.0
            
            buys = [t for t in history if t.get('side') == 'buy']
            
            if current_balance <= 0.0:
                buys = buys[:5]
                total_qty = sum(float(b['quantity']) for b in buys)
                if total_qty == 0: return 0.0
                total_val = sum(float(b['price']) * float(b['quantity']) for b in buys)
                return total_val / total_qty
                
            total_cost = 0.0
            accumulated_qty = 0.0
            
            for b in buys:
                qty = float(b['quantity'])
                price = float(b['price'])
                
                remaining_needed = current_balance - accumulated_qty
                if remaining_needed <= 0:
                    break
                    
                take_qty = min(qty, remaining_needed)
                accumulated_qty += take_qty
                total_cost += take_qty * price
                
            if accumulated_qty > 0:
                return total_cost / accumulated_qty
            return 0.0

        except Exception as e:
            logger.error(f"Failed to get avg buy price for {pair}: {e}")
            return 0.0

    async def get_profit_analysis(self) -> dict:
        """Calculate overall realized and unrealized profit for all traded pairs."""
        try:
            balances = await self.get_valr_balances()
            summaries = await self.get_valr_market_summaries()
            
            price_map = {}
            for summary in summaries:
                pair = summary.get('currencyPair')
                if pair and pair.endswith('ZAR'):
                    price_map[pair] = float(summary.get('lastTradedPrice', 0))

            analysis = {
                "total_invested": 0.0,
                "realized_profit": 0.0,
                "unrealized_profit": 0.0,
                "current_portfolio_value": 0.0,
                "assets": {}
            }

            for bal in balances:
                currency = bal.get('currency')
                if currency == 'ZAR':
                    analysis["current_portfolio_value"] += float(bal.get('total', 0))
                    continue
                    
                pair = f"{currency}ZAR"
                if pair not in price_map:
                    continue
                    
                total_amt = float(bal.get('total', 0))
                current_price = price_map[pair]
                current_value = total_amt * current_price
                
                analysis["current_portfolio_value"] += current_value

                try:
                    history = await self._run_valr_sync(self.valr_client.get_trade_history, pair, skip=0, limit=100)
                    # NOTE: valr-python get_trade_history signature is get_trade_history(currency_pair, skip=0, limit=100)
                    # If we need full history, we would paginate. For now, limited to 100 recent trades.
                except Exception:
                    continue
                    
                if not history:
                    continue

                total_buy_cost = 0.0
                total_buy_qty = 0.0
                total_sell_rev = 0.0
                total_sell_qty = 0.0
                
                for t in history:
                    qty = float(t['quantity'])
                    price = float(t['price'])
                    if t.get('side') == 'buy':
                        total_buy_cost += (qty * price)
                        total_buy_qty += qty
                    elif t.get('side') == 'sell':
                        total_sell_rev += (qty * price)
                        total_sell_qty += qty
                
                global_avg_buy_price = (total_buy_cost / total_buy_qty) if total_buy_qty > 0 else 0
                realized = total_sell_rev - (total_sell_qty * global_avg_buy_price)
                unrealized = current_value - (total_amt * global_avg_buy_price)
                
                if total_buy_qty > 0 or total_sell_qty > 0:
                    analysis["total_invested"] += total_buy_cost
                    analysis["realized_profit"] += realized
                    analysis["unrealized_profit"] += unrealized
                    
                    analysis["assets"][currency] = {
                        "amount": total_amt,
                        "current_value": current_value,
                        "global_avg_buy_price": global_avg_buy_price,
                        "realized_profit": realized,
                        "unrealized_profit": unrealized
                    }
            
            return analysis
        except Exception as e:
            logger.error(f"Failed to calculate profit analysis: {e}")
            return None

    async def place_valr_order(self, pair: str, side: str, amount: float, price: float, post_only: bool = True):
        """Place a limit order on VALR for any pair."""
        req = {
            "side": side.upper(),
            "quantity": str(amount),
            "price": str(price),
            "pair": pair,
            "post_only": post_only
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
