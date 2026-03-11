import asyncio
import logging
from config import VALR_PAIR, MAX_POSITION_SIZE_PCT, POLL_INTERVAL
from exchange import ExchangeInterface
from strategy import Strategy
from telegram_bot import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger(__name__)

class HitlTradingBot:
    def __init__(self):
        self.exchange = ExchangeInterface()
        self.strategy = Strategy()
        self.notifier = TelegramNotifier(
            exchange=self.exchange,
            strategy=self.strategy
        )
        self.queue = asyncio.Queue()

    async def execute_signal_autonomously(self, trade_info: dict) -> tuple[bool, float]:
        try:
            logger.info(f"Executing trade: {trade_info}")
            pair = trade_info.get('pair', VALR_PAIR)
            signal = trade_info.get('signal', 'BUY').upper()
            price = float(trade_info['price'])

            base_currency = pair.replace('ZAR', '').replace('USDT', '').replace('USDC', '')
            quote_currency = 'ZAR' if 'ZAR' in pair else ('USDC' if 'USDC' in pair else 'USDT')

            balances = await self.exchange.get_valr_balances()
            amount = 0.0

            if signal == 'BUY':
                quote_balance = 0.0
                for bal in balances:
                    if bal.get('currency') == quote_currency:
                        quote_balance = float(bal.get('available', 0))
                        break

                position_size_quote = quote_balance * MAX_POSITION_SIZE_PCT
                if position_size_quote <= 0:
                    logger.error(f"Insufficient {quote_currency} balance.")
                    return False, 0.0

                amount = position_size_quote / price

            elif signal == 'SELL':
                base_balance = 0.0
                for bal in balances:
                    if bal.get('currency') == base_currency:
                        base_balance = float(bal.get('available', 0))
                        break

                amount = base_balance * MAX_POSITION_SIZE_PCT
                if amount <= 0:
                    logger.error(f"Insufficient {base_currency} balance.")
                    return False, 0.0

            amount = round(amount, 8)

            logger.info(f"Placing {signal} order: {amount} on {pair} at R{price}")

            result = await self.exchange.place_valr_order(
                pair=pair,
                side=signal,
                amount=amount,
                price=price
            )
            logger.info(f"Order result: {result}")
            return True, amount

        except Exception as e:
            logger.error(f"Trade execution error: {e}", exc_info=True)
            return False, 0.0

    async def strategy_consumer(self):
        """Consumes WS price data and runs analysis for the originating pair."""
        logger.info("Strategy consumer started.")
        while True:
            try:
                data = await self.queue.get()
                pair = data.get("pair", VALR_PAIR)
                price = data.get("price", 0)

                # Only analyze if this pair is being watched
                if pair not in self.notifier.watched_pairs:
                    continue

                self.strategy.add_price(pair, price)

                valr_ob = await self.exchange.get_valr_order_book(pair)
                luno_ob = {}  # Luno only has BTC pair

                signal = self.strategy.analyze(pair, valr_ob, luno_ob)
                if signal:
                    logger.info(f"Autonomous Signal for {pair}: {signal['insight']}")
                    
                    # Execute without HITL
                    success, amount = await self.execute_signal_autonomously(signal)
                    
                    # Notify Telegram
                    await self.notifier.notify_execution(signal, success, amount)

            except Exception as e:
                logger.warning(f"Consumer error: {e}. Recovering in 5s...")
                await asyncio.sleep(5)

    async def rest_poller(self):
        """Polls VALR REST API for prices of watched pairs that aren't covered by WebSocket."""
        logger.info("REST price poller started.")
        while True:
            try:
                for pair in list(self.notifier.watched_pairs):
                    try:
                        summary = await self.exchange.get_valr_market_summary(pair)
                        if summary:
                            last_price = float(summary.get('lastTradedPrice', 0))
                            if last_price > 0:
                                self.strategy.add_price(pair, last_price)

                                # Run analysis
                                valr_ob = await self.exchange.get_valr_order_book(pair)
                                signal = self.strategy.analyze(pair, valr_ob, {})
                                if signal:
                                    logger.info(f"REST Autonomous Signal for {pair}: {signal['insight']}")
                                    
                                    # Execute without HITL
                                    success, amount = await self.execute_signal_autonomously(signal)
                                    
                                    # Notify Telegram
                                    await self.notifier.notify_execution(signal, success, amount)
                    except Exception as e:
                        logger.warning(f"REST poll error for {pair}: {e}")

                await asyncio.sleep(POLL_INTERVAL)

            except Exception as e:
                logger.warning(f"REST poller error: {e}. Recovering in 10s...")
                await asyncio.sleep(10)

    async def ws_producer(self):
        """WebSocket stream for real-time trade data."""
        logger.info("Starting VALR WebSocket streamer...")
        await self.exchange.start_ws(self.queue)

    async def run(self):
        try:
            await self.notifier.start_bot()
            logger.info("Telegram bot initialized.")

            consumer_task = asyncio.create_task(self.strategy_consumer())
            producer_task = asyncio.create_task(self.ws_producer())
            poller_task = asyncio.create_task(self.rest_poller())

            await asyncio.gather(consumer_task, producer_task, poller_task)

        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            await self.notifier.stop_bot()
            await self.exchange.close()
            logger.info("Bot stopped.")

if __name__ == "__main__":
    bot = HitlTradingBot()
    asyncio.run(bot.run())
