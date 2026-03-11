import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS, SUPPORTED_PAIRS, DEFAULT_WATCHED_PAIRS

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self, exchange=None, strategy=None):
        self.app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
        self.exchange = exchange
        self.strategy = strategy
        self.watched_pairs = list(DEFAULT_WATCHED_PAIRS)
        self.goals = {}  # {currency: {"target_multiplier": 2.0, "initial_balance": float}}

        # Register all command handlers
        self.app.add_handler(CommandHandler("start", self.start))
        self.app.add_handler(CommandHandler("help", self.help_cmd))
        self.app.add_handler(CommandHandler("balances", self.balances_cmd))
        self.app.add_handler(CommandHandler("watch", self.watch_cmd))
        self.app.add_handler(CommandHandler("unwatch", self.unwatch_cmd))
        self.app.add_handler(CommandHandler("pairs", self.pairs_cmd))
        self.app.add_handler(CommandHandler("status", self.status_cmd))
        self.app.add_handler(CommandHandler("goal", self.goal_cmd))
        self.app.add_handler(CommandHandler("goals", self.goals_cmd))
        self.app.add_handler(CommandHandler("portfolio", self.portfolio_cmd))
        self.app.add_handler(CommandHandler("sell", self.sell_cmd))
        self.app.add_handler(CommandHandler("restart", self.restart_cmd))

    def _is_authorized(self, user_id: int) -> bool:
        return user_id in TELEGRAM_ALLOWED_USERS

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in TELEGRAM_ALLOWED_USERS:
            TELEGRAM_ALLOWED_USERS.append(user_id)
        await update.message.reply_text(
            "🤖 *VALR Autonomous Crypto Bot — Active*\n\n"
            "Monitoring and executing trades automatically based on advanced strategies.\n"
            "Type /help to see all available commands.",
            parse_mode='Markdown'
        )

    async def help_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        await update.message.reply_text(
            "📋 *Available Commands*\n\n"
            "💰 /balances — Show all VALR holdings\n"
            "💼 /portfolio — Show total portfolio value in ZAR\n"
            "⚡ /sell `XRP` — Smart sell at avg buy price or better\n"
            "👀 /watch `XRPZAR` — Add a pair to monitor\n"
            "🚫 /unwatch `XRPZAR` — Stop monitoring a pair\n"
            "📊 /pairs — List currently watched pairs\n"
            "📈 /status — Live indicator readings per pair\n"
            "🎯 /goal `double XRP` — Set an accumulation target\n"
            "🏆 /goals — View active goals & progress\n"
            "🔄 /restart — Restart the bot\n"
            "❓ /help — This message",
            parse_mode='Markdown'
        )

    async def balances_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        if not self.exchange:
            await update.message.reply_text("⚠️ Exchange not connected.")
            return

        await update.message.reply_text("⏳ Fetching balances from VALR...")

        try:
            balances = await self.exchange.get_valr_balances()
            lines = ["💰 *Your VALR Balances*\n"]
            for bal in balances:
                available = float(bal.get('available', 0))
                total = float(bal.get('total', 0))
                currency = bal.get('currency', '?')
                if total > 0:
                    lines.append(f"• *{currency}*: {available:,.8f} available ({total:,.8f} total)")

            if len(lines) == 1:
                lines.append("No holdings found.")

            await update.message.reply_text("\n".join(lines), parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Balances error: {e}")
            await update.message.reply_text(f"❌ Failed to fetch balances: {e}")

    async def portfolio_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        if not self.exchange:
            await update.message.reply_text("⚠️ Exchange not connected.")
            return

        await update.message.reply_text("⏳ Calculating portfolio value...")

        try:
            total_zar = await self.exchange.get_portfolio_value_zar()
            await update.message.reply_text(
                f"💼 *Total Portfolio Value*\n\n"
                f"**R {total_zar:,.2f}**",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Portfolio error: {e}")
            await update.message.reply_text(f"❌ Failed to calculate portfolio: {e}")

    async def sell_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        if not self.exchange:
            await update.message.reply_text("⚠️ Exchange not connected.")
            return

        if not context.args:
            await update.message.reply_text("Usage: /sell `XRP`", parse_mode='Markdown')
            return

        currency = context.args[0].upper()
        if currency == "ZAR":
            await update.message.reply_text("❌ Cannot sell ZAR for ZAR.")
            return

        pair = f"{currency}ZAR"
        
        await update.message.reply_text(f"⏳ Analyzing {currency} for sale...")

        try:
            balances = await self.exchange.get_valr_balances()
            balance = 0.0
            for bal in balances:
                if bal.get('currency') == currency:
                    balance = float(bal.get('available', 0))
                    break
            
            if balance <= 0:
                await update.message.reply_text(f"❌ You have no {currency} available to sell.")
                return

            avg_buy_price = await self.exchange.get_average_buy_price(pair, balance)
            
            summary = await self.exchange.get_valr_market_summary(pair)
            if not summary:
                await update.message.reply_text(f"❌ Failed to get market price for {pair}.")
                return
            current_price = float(summary.get('lastTradedPrice', 0))

            if avg_buy_price == 0:
                sell_price = current_price
                post_only = False
                msg = f"⚠️ No buy history found for {currency}. Selling at current market price (R {current_price:.2f})."
            elif current_price >= avg_buy_price:
                sell_price = current_price
                post_only = False
                msg = f"✅ Current price (R {current_price:.2f}) is higher than your average buy price (R {avg_buy_price:.2f}). Selling at market price!"
            else:
                sell_price = avg_buy_price * 1.005 # Sell at Buy Price + 0.5% buffer
                post_only = True
                msg = f"📉 Current price (R {current_price:.2f}) is lower than buy price (R {avg_buy_price:.2f}). Placing a target Limit Order at R {sell_price:.2f}."

            amount = round(balance, 8)
            result = await self.exchange.place_valr_order(
                pair=pair, side="SELL", amount=amount, price=sell_price, post_only=post_only
            )
            
            await update.message.reply_text(
                f"{msg}\n\n"
                f"🚨 *SMART SELL EXECUTED* 🚨\n"
                f"*Pair*: {pair}\n"
                f"*Amount*: {amount} {currency}\n"
                f"*Avg Buy Price*: R {avg_buy_price:.2f}\n"
                f"*Sell Price*: R {sell_price:.2f}\n"
                f"*Status*: {'Filled' if not post_only else 'Pending Limit Order'}",
                parse_mode='Markdown'
            )

        except Exception as e:
            logger.error(f"Sell command error: {e}", exc_info=True)
            if 'Post-only order would execute immediately' in str(e):
                await update.message.reply_text(f"❌ Sell failed: Limit order crossed the spread. Price changed rapidly.")
            else:
                await update.message.reply_text(f"❌ Sell execution failed: {e}")

    async def restart_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        await update.message.reply_text("🔄 Restarting bot... Please wait a few seconds.", parse_mode='Markdown')
        import os
        import sys
        logger.info("Restarting bot via telegram command...")
        # Give telegram time to send the message before we kill the process
        import asyncio
        loop = asyncio.get_running_loop()
        loop.call_later(1.0, lambda: os.execl(sys.executable, sys.executable, *sys.argv))

    async def watch_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        if not context.args:
            await update.message.reply_text(
                "Usage: /watch `XRPZAR`\n\nAvailable pairs:\n" +
                ", ".join(f"`{p}`" for p in SUPPORTED_PAIRS),
                parse_mode='Markdown'
            )
            return

        pair = context.args[0].upper()
        if pair not in SUPPORTED_PAIRS:
            await update.message.reply_text(
                f"❌ `{pair}` is not supported.\n\nAvailable:\n" +
                ", ".join(f"`{p}`" for p in SUPPORTED_PAIRS),
                parse_mode='Markdown'
            )
            return

        if pair in self.watched_pairs:
            await update.message.reply_text(f"Already watching `{pair}`.", parse_mode='Markdown')
            return

        self.watched_pairs.append(pair)
        display = pair[:-3] + "/" + pair[-3:]
        await update.message.reply_text(f"👀 Now watching *{display}*! Strategy analysis will begin once price data accumulates.", parse_mode='Markdown')

    async def unwatch_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        if not context.args:
            await update.message.reply_text("Usage: /unwatch `XRPZAR`", parse_mode='Markdown')
            return

        pair = context.args[0].upper()
        if pair not in self.watched_pairs:
            await update.message.reply_text(f"`{pair}` is not being watched.", parse_mode='Markdown')
            return

        self.watched_pairs.remove(pair)
        display = pair[:-3] + "/" + pair[-3:]
        await update.message.reply_text(f"🚫 Stopped watching *{display}*.", parse_mode='Markdown')

    async def pairs_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        if not self.watched_pairs:
            await update.message.reply_text("No pairs being watched. Use /watch to add one.")
            return

        lines = ["📊 *Watched Pairs*\n"]
        for p in self.watched_pairs:
            display = p[:-3] + "/" + p[-3:]
            lines.append(f"• {display}")
        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

    async def status_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        if not self.strategy:
            await update.message.reply_text("⚠️ Strategy engine not connected.")
            return

        lines = ["📈 *Strategy Status*\n"]
        for pair in self.watched_pairs:
            display = pair[:-3] + "/" + pair[-3:]
            status = self.strategy.get_status(pair)
            if not status or not status.get("ready"):
                pts = status.get("data_points", 0) if status else 0
                lines.append(f"• *{display}*: Collecting data ({pts}/35 points)")
            else:
                rsi = status['rsi']
                price = status['price']
                macd = status['macd_hist']
                # RSI zone
                if rsi <= 30:
                    rsi_label = "🟢 Oversold"
                elif rsi >= 70:
                    rsi_label = "🔴 Overbought"
                else:
                    rsi_label = "⚪ Neutral"
                # MACD direction
                macd_label = "📈 Bullish" if macd > 0 else "📉 Bearish"
                lines.append(
                    f"• *{display}* — R {price:,.2f}\n"
                    f"  RSI: {rsi:.1f} ({rsi_label})\n"
                    f"  MACD: {macd_label}\n"
                    f"  BB: {status['bb_lower']:,.2f} — {status['bb_upper']:,.2f}"
                )

        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

    async def goal_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        if not context.args or len(context.args) < 2:
            await update.message.reply_text(
                "Usage: /goal `double XRP`\n\n"
                "Sets a target to double your current XRP holdings.\n"
                "The bot will prioritize XRP/ZAR signals toward this goal.",
                parse_mode='Markdown'
            )
            return

        action = context.args[0].lower()
        currency = context.args[1].upper()

        if action == "double":
            multiplier = 2.0
        elif action == "triple":
            multiplier = 3.0
        else:
            try:
                multiplier = float(action.replace("x", ""))
            except ValueError:
                await update.message.reply_text("❌ Use: /goal `double XRP`, /goal `triple BTC`, or /goal `1.5x ETH`", parse_mode='Markdown')
                return

        # Fetch current balance for the currency
        if not self.exchange:
            await update.message.reply_text("⚠️ Exchange not connected.")
            return

        try:
            balances = await self.exchange.get_valr_balances()
            current_balance = 0.0
            for bal in balances:
                if bal.get('currency') == currency:
                    current_balance = float(bal.get('available', 0))
                    break

            if current_balance <= 0:
                await update.message.reply_text(f"❌ You have no {currency} balance to set a goal for.")
                return

            target_balance = current_balance * multiplier
            self.goals[currency] = {
                "target_multiplier": multiplier,
                "initial_balance": current_balance,
                "target_balance": target_balance,
            }

            # Auto-watch the pair if not already watched
            pair = f"{currency}ZAR"
            if pair in SUPPORTED_PAIRS and pair not in self.watched_pairs:
                self.watched_pairs.append(pair)

            await update.message.reply_text(
                f"🎯 *Goal Set: {multiplier}x {currency}*\n\n"
                f"Current: {current_balance:,.8f} {currency}\n"
                f"Target: {target_balance:,.8f} {currency}\n"
                f"Progress: {'█' * 1}{'░' * 9} 0%\n\n"
                f"Auto-watching {currency}/ZAR for trade opportunities.",
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Goal setup error: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def goals_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        if not self.goals:
            await update.message.reply_text("No active goals. Use /goal `double XRP` to set one.", parse_mode='Markdown')
            return

        lines = ["🏆 *Active Goals*\n"]
        try:
            balances = await self.exchange.get_valr_balances()
            bal_map = {b['currency']: float(b.get('available', 0)) for b in balances}
        except Exception:
            bal_map = {}

        for currency, goal in self.goals.items():
            current = bal_map.get(currency, goal['initial_balance'])
            target = goal['target_balance']
            initial = goal['initial_balance']
            progress = min((current - initial) / (target - initial) * 100, 100) if target > initial else 0
            filled = int(progress / 10)
            bar = '█' * filled + '░' * (10 - filled)

            lines.append(
                f"• *{goal['target_multiplier']}x {currency}*\n"
                f"  {current:,.8f} / {target:,.8f}\n"
                f"  {bar} {progress:.1f}%"
            )

        await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

    async def notify_execution(self, trade_info: dict, success: bool, amount: float):
        """Notify user of an autonomous trade execution."""
        display_pair = trade_info.get('display_pair', trade_info.get('pair', 'BTC/ZAR'))
        
        if success:
            message = (
                f"🚨 *TRADE EXECUTED* 🚨\n\n"
                f"*Action*: {trade_info['signal']} {amount:,.8f} {display_pair}\n"
                f"*Price*: R {trade_info['price']:.2f}\n"
                f"*Take-Profit*: R {trade_info['take_profit']:.2f}\n"
                f"*Stop-Loss*: R {trade_info['stop_loss']:.2f}\n\n"
                f"🧠 _{trade_info['insight']}_"
            )
        else:
            message = (
                f"⚠️ *TRADE FAILED TO EXECUTE* ⚠️\n\n"
                f"*Action*: {trade_info['signal']} {display_pair}\n"
                f"Price: R {trade_info['price']:.2f}\n\n"
                f"Bot attempted to trade based on:\n"
                f"_{trade_info['insight']}_\n\n"
                f"Check bot logs for reasons (e.g., insufficient funds)."
            )

        for user_id in TELEGRAM_ALLOWED_USERS:
            try:
                await self.app.bot.send_message(
                    chat_id=user_id, text=message, parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Failed sending execution notice to {user_id}: {e}")

    async def start_bot(self):
        import asyncio
        from telegram.error import Conflict
        
        await self.app.initialize()
        await self.app.start()
        
        retries = 12
        for i in range(retries):
            try:
                await self.app.updater.start_polling()
                logger.info("Telegram polling started successfully.")
                break
            except Conflict:
                logger.warning(f"Telegram Conflict (old instance overlap). Retrying in 10s... ({i+1}/{retries})")
                await asyncio.sleep(10)
        else:
            raise Exception("Failed to start Telegram polling due to persistent Conflict.")

    async def stop_bot(self):
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()
