import asyncio
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from config import (
    VALR_PAIR,
    POLL_INTERVAL,
    DOUBLE_ZAR_SCAN_INTERVAL,
    TELEGRAM_ALLOWED_USERS,
    MAX_DAILY_LOSS_ZAR,
    TRADE_COOLDOWN_SECONDS,
    MAX_TRADES_PER_DAY,
    PAPER_FEE_PCT,
    DAILY_REPORT_HOUR_SAST,
    PAPER_STATE_PATH,
)
from exchange import ExchangeInterface
from paper import PaperPortfolio
from reporting import format_paper_daily_report
from risk import TradingRiskGuard
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
        self.risk_guard = TradingRiskGuard(
            max_daily_loss_zar=MAX_DAILY_LOSS_ZAR,
            cooldown_seconds=TRADE_COOLDOWN_SECONDS,
            max_trades_per_day=MAX_TRADES_PER_DAY,
        )
        self.paper_portfolio: PaperPortfolio | None = None
        self.paper_state_path = Path(PAPER_STATE_PATH)
        self.queue = asyncio.Queue()

    async def execute_signal_autonomously(self, trade_info: dict) -> tuple[bool, float]:
        now = datetime.now(ZoneInfo("Africa/Johannesburg"))
        decision = self.risk_guard.can_execute(now)
        if not decision.allowed:
            logger.warning(
                "Blocked autonomous %s signal for %s: %s",
                trade_info.get("signal"), trade_info.get("pair", VALR_PAIR), decision.reason,
            )
            return False, 0.0
        try:
            logger.info(f"Executing trade: {trade_info}")
            pair = trade_info.get('pair', VALR_PAIR)
            signal = trade_info.get('signal', 'BUY').upper()
            price = float(trade_info['price'])

            base_currency = pair.replace('ZAR', '').replace('USDT', '').replace('USDC', '')
            quote_currency = 'ZAR' if 'ZAR' in pair else ('USDC' if 'USDC' in pair else 'USDT')

            balances = await self.exchange.get_valr_balances()
            paper_mode = self.exchange.execution_mode == "paper"
            if paper_mode and self.paper_portfolio is None:
                state_path = getattr(self, "paper_state_path", None)
                if state_path and Path(state_path).exists():
                    self.paper_portfolio = PaperPortfolio.load(state_path)
                    logger.info("Loaded persisted paper XRP/ZAR portfolio state.")
                else:
                    seed_zar = next(
                        (float(bal.get("available", 0)) for bal in balances if bal.get("currency") == "ZAR"),
                        0.0,
                    )
                    seed_xrp = next(
                        (float(bal.get("available", 0)) for bal in balances if bal.get("currency") == "XRP"),
                        0.0,
                    )
                    self.paper_portfolio = PaperPortfolio(
                        initial_zar=seed_zar,
                        initial_xrp=seed_xrp,
                        initial_xrp_price=price,
                        fee_pct=PAPER_FEE_PCT,
                    )
                    logger.info("Seeded paper XRP/ZAR portfolio from a read-only VALR balance snapshot.")
            amount = 0.0

            if signal == 'BUY':
                quote_balance = 0.0
                base_held = 0.0
                for bal in balances:
                    if bal.get('currency') == quote_currency:
                        quote_balance = float(bal.get('available', 0))
                    elif bal.get('currency') == base_currency:
                        base_held = float(bal.get('available', 0))

                if paper_mode:
                    quote_balance = self.paper_portfolio.zar_balance
                    base_held = self.paper_portfolio.xrp_balance

                # Do not stack a second XRP position.
                current_value_held = base_held * price
                if current_value_held > 100:
                    logger.info(f"Ignored BUY signal for {pair}: Already hold R{current_value_held:.2f} worth.")
                    return False, 0.0

                position_size_quote = quote_balance * self.notifier.risk_pct
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
                if paper_mode:
                    base_balance = self.paper_portfolio.xrp_balance

                # Exit the single simulated/live XRP position.
                amount = base_balance
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
            realized_pnl_zar = 0.0
            if paper_mode:
                if signal == "BUY":
                    paper_fill = self.paper_portfolio.buy(quantity=amount, price=price)
                else:
                    paper_fill = self.paper_portfolio.sell(quantity=amount, price=price)
                realized_pnl_zar = paper_fill.realized_pnl_zar
                logger.info(
                    "Paper portfolio updated: realized P&L R%.2f, ZAR R%.2f, XRP %.8f",
                    realized_pnl_zar,
                    self.paper_portfolio.zar_balance,
                    self.paper_portfolio.xrp_balance,
                )
                state_path = getattr(self, "paper_state_path", None)
                if state_path:
                    self.paper_portfolio.save(state_path)
            self.risk_guard.record_execution(now, realized_pnl_zar=realized_pnl_zar)
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

                candle_closed = self.strategy.add_price(pair, price)
                if not candle_closed:
                    continue

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

    async def ai_market_scan_loop(self):
        """Periodically wakes up and asks the AI for the best market trade."""
        logger.info("AI Market Scan loop started.")
        while True:
            try:
                # Run the scan every 1 hour (3600 seconds)
                await asyncio.sleep(3600)
                
                logger.info("Waking up AI Market Scanner...")
                balances = await self.exchange.get_valr_balances()
                top_pick = await self.strategy.ai_market_scan(current_balances=balances)
                
                if top_pick:
                    # Automatically watch the new top pick
                    if top_pick not in self.notifier.watched_pairs:
                        logger.info(f"AI Selected {top_pick} as the best trade. Watching it now.")
                        self.notifier.watched_pairs.append(top_pick)
                        
                        # Notify the user
                        for user_id in TELEGRAM_ALLOWED_USERS:
                            try:
                                await self.notifier.app.bot.send_message(
                                    chat_id=user_id,
                                    text=f"🧠 *AI MARKET SCAN COMPLETE*\n\n"
                                         f"I've scanned the market and identified `{top_pick}` as the best opportunity right now.\n"
                                         f"I have added it to the watchlist and will monitor it for entry.",
                                    parse_mode='Markdown'
                                )
                            except Exception:
                                pass
            except Exception as e:
                logger.warning(f"AI Market Scan loop encountered an error: {e}")

    async def double_zar_loop(self):
        """Periodically scans ALL ZAR pairs on VALR and buys the best opportunity."""
        logger.info("Double ZAR loop started.")
        while True:
            try:
                await asyncio.sleep(DOUBLE_ZAR_SCAN_INTERVAL)

                if not self.notifier.double_zar_enabled:
                    continue

                logger.info("Double ZAR: Waking up for market-wide scan...")

                # Fetch all ZAR market summaries
                summaries = await self.exchange.get_all_zar_market_summaries()
                if not summaries:
                    logger.warning("Double ZAR: No market data returned.")
                    continue

                # Get ZAR balance
                balances = await self.exchange.get_valr_balances()
                zar_balance = 0.0
                for bal in balances:
                    if bal.get('currency') == 'ZAR':
                        zar_balance = float(bal.get('available', 0))
                        break

                if zar_balance < 10:
                    logger.info(f"Double ZAR: ZAR balance too low (R{zar_balance:.2f}). Skipping.")
                    continue

                # AI picks the best buy
                result = await self.strategy.ai_double_zar_scan(summaries, zar_balance)
                if not result:
                    logger.info("Double ZAR: AI found no good setups.")
                    continue

                pair = result['pair']
                reason = result['reason']
                confidence = result['confidence']

                # Auto-watch
                if pair not in self.notifier.watched_pairs:
                    self.notifier.watched_pairs.append(pair)

                # Get current price for the pair
                summary = await self.exchange.get_valr_market_summary(pair)
                if not summary:
                    logger.warning(f"Double ZAR: Could not get price for {pair}.")
                    continue
                # Use bidPrice to ensure Maker status on BUY limits
                bid_price_str = summary.get('bidPrice')
                current_price = float(bid_price_str) if bid_price_str else float(summary.get('lastTradedPrice', 0))
                if current_price <= 0:
                    continue

                # Calculate buy amount
                buy_zar = zar_balance * self.notifier.double_zar_buy_pct
                buy_amount = round(buy_zar / current_price, 8)

                if buy_amount <= 0:
                    continue

                # Execute the buy
                logger.info(f"Double ZAR: Buying {buy_amount} of {pair} at R{current_price:.2f} (reason: {reason})")
                try:
                    await self.exchange.place_valr_order(
                        pair=pair, side="BUY", amount=buy_amount, price=current_price, post_only=True
                    )
                    trade_success = True
                except Exception as e:
                    logger.error(f"Double ZAR: Order failed: {e}")
                    trade_success = False

                conf_emoji = {"HIGH": "🟢", "MED": "🟡", "LOW": "🔴"}.get(confidence, "⚪")
                display_pair = pair[:-3] + "/" + pair[-3:]

                # Notify user via Telegram
                for user_id in TELEGRAM_ALLOWED_USERS:
                    try:
                        if trade_success:
                            await self.notifier.app.bot.send_message(
                                chat_id=user_id,
                                text=f"🧠💸 *DOUBLE ZAR — TRADE EXECUTED!*\n\n"
                                     f"*Pair*: {display_pair}\n"
                                     f"*Action*: BUY {buy_amount:,.8f}\n"
                                     f"*Price*: R {current_price:,.2f}\n"
                                     f"*Spent*: R {buy_zar:,.2f}\n"
                                     f"*Confidence*: {conf_emoji} {confidence}\n"
                                     f"*Reason*: {reason}\n\n"
                                     f"📈 Pairs scanned: {len(summaries)}",
                                parse_mode='Markdown'
                            )
                        else:
                            await self.notifier.app.bot.send_message(
                                chat_id=user_id,
                                text=f"⚠️ *DOUBLE ZAR — TRADE FAILED*\n\n"
                                     f"*Pair*: {display_pair}\n"
                                     f"*Reason*: {reason}\n"
                                     f"Check logs for details.",
                                parse_mode='Markdown'
                            )
                    except Exception:
                        pass

            except Exception as e:
                logger.warning(f"Double ZAR loop error: {e}")
                await asyncio.sleep(30)

    async def paper_daily_report_loop(self):
        """Send one marked-to-market paper report per SAST day after the configured hour."""
        logger.info("Paper daily report loop started.")
        reported_date = None
        while True:
            try:
                now = datetime.now(ZoneInfo("Africa/Johannesburg"))
                if (
                    self.exchange.execution_mode == "paper"
                    and self.paper_portfolio is not None
                    and now.hour >= DAILY_REPORT_HOUR_SAST
                    and reported_date != now.date()
                ):
                    summary = await self.exchange.get_valr_market_summary(VALR_PAIR)
                    mark_price = float((summary or {}).get("bidPrice") or (summary or {}).get("lastTradedPrice") or 0)
                    if mark_price > 0:
                        report = self.paper_portfolio.daily_report(mark_price=mark_price)
                        await self.notifier.notify_paper_report(format_paper_daily_report(report))
                        reported_date = now.date()
                    else:
                        logger.warning("Skipping paper report: no XRP/ZAR mark price available.")
            except Exception as e:
                logger.warning("Paper daily report loop error: %s", e)
            await asyncio.sleep(60)

    async def rest_poller(self):
        """Polls VALR REST API for prices of watched pairs that aren't covered by WebSocket."""
        logger.info("REST price poller started.")
        while True:
            try:
                for pair in list(self.notifier.watched_pairs):
                    try:
                        summary = await self.exchange.get_valr_market_summary(pair)
                        if not summary:
                            continue
                        last_price = float(summary.get('lastTradedPrice', 0))
                        if last_price <= 0:
                            continue
                        candle_closed = self.strategy.add_price(pair, last_price)
                        if not candle_closed:
                            continue

                        # Run analysis only for a completed candle.
                        valr_ob = await self.exchange.get_valr_order_book(pair)
                        signal = self.strategy.analyze(pair, valr_ob, {})
                        if signal:
                            logger.info(f"REST Autonomous Signal for {pair}: {signal['insight']}")
                            success, amount = await self.execute_signal_autonomously(signal)
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
            paper_report_task = asyncio.create_task(self.paper_daily_report_loop())

            await asyncio.gather(consumer_task, producer_task, poller_task, paper_report_task)

        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            await self.notifier.stop_bot()
            await self.exchange.close()
            logger.info("Bot stopped.")

if __name__ == "__main__":
    bot = HitlTradingBot()
    asyncio.run(bot.run())
