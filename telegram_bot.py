import logging
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters, ContextTypes
from openai import AsyncOpenAI
from config import (TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS, SUPPORTED_PAIRS, DEFAULT_WATCHED_PAIRS,
                     MAX_POSITION_SIZE_PCT, OPENAI_API_KEY, DOUBLE_ZAR_MODE, DOUBLE_ZAR_BUY_PCT)

logger = logging.getLogger(__name__)

class TelegramNotifier:
    def __init__(self, exchange=None, strategy=None):
        self.app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
        self.exchange = exchange
        self.strategy = strategy
        self.watched_pairs = list(DEFAULT_WATCHED_PAIRS)
        self.goals = {}  # {currency: {"target_multiplier": 2.0, "initial_balance": float}}
        self.risk_pct = MAX_POSITION_SIZE_PCT
        self.double_zar_enabled = DOUBLE_ZAR_MODE
        self.double_zar_buy_pct = DOUBLE_ZAR_BUY_PCT
        self.ai_client = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

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
        self.app.add_handler(CommandHandler("profit", self.profit_cmd))
        self.app.add_handler(CommandHandler("sell", self.sell_cmd))
        self.app.add_handler(CommandHandler("risk", self.risk_cmd))
        self.app.add_handler(CommandHandler("scan", self.scan_cmd))
        self.app.add_handler(CommandHandler("restart", self.restart_cmd))
        self.app.add_handler(CommandHandler("doublezar", self.doublezar_cmd))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_ai_chat))

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
            "📋 *VALR Trading Bot — Complete Command List*\n\n"
            "*Account & Assets*\n"
            "💰 /balances — View all current VALR holdings\n"
            "💼 /portfolio — Total portfolio value in ZAR\n"
            "💵 /profit — Detailed Profit/Loss analysis\n\n"
            "*Global Controls*\n"
            "⚖️ /risk `10` — Set trade size % per position (default 5%)\n"
            "🧠 /scan — Wake up AI brain to find the best current trade\n"
            "🔄 /restart — Completely restart the bot processes\n\n"
            "*Manual & Watchlist*\n"
            "👀 /watch `XRPZAR` — Manually add a pair to monitor\n"
            "🚫 /unwatch `XRPZAR` — Stop monitoring a pair\n"
            "📊 /pairs — View currently watched pairs\n"
            "📈 /status — See live RSI/MACD readings per pair\n"
            "⚡ /sell `XRP` — Smart sell (waits for profit or break-even)\n\n"
            "*Accumulation Goals*\n"
            "🎯 /goal `double XRP` — Set an accumulation target\n"
            "🏆 /goals — View active goals & progress\n\n"
            "*💸 Double ZAR Mode*\n"
            "🚀 /doublezar — View Double ZAR status\n"
            "🟢 /doublezar `on` — Enable market-wide scanning\n"
            "🔴 /doublezar `off` — Disable market-wide scanning\n"
            "🔍 /doublezar `scan` — Force an immediate market-wide scan\n\n"
            "💬 *AI Chat Capability*\n"
            "You can type any plain-text message to me! I will analyze the market using OpenAI and help you with strategy decisions, or execute commands based on your conversation.",
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

    async def profit_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        if not self.exchange:
            await update.message.reply_text("⚠️ Exchange not connected.")
            return

        await update.message.reply_text("⏳ Analyzing trading history for profit/loss. This may take a moment...", parse_mode='Markdown')

        try:
            analysis = await self.exchange.get_profit_analysis()
            if not analysis:
                await update.message.reply_text("❌ Failed to calculate profit.")
                return

            lines = ["📊 *Profit Analysis*\n"]
            lines.append(f"• **Current Portfolio Value**: R {analysis['current_portfolio_value']:,.2f}")
            lines.append(f"• **Total Invested (Historic Buys)**: R {analysis['total_invested']:,.2f}")
            lines.append(f"• **Net Realized Profit**: R {analysis['realized_profit']:,.2f}")
            lines.append(f"• **Net Unrealized Profit**: R {analysis['unrealized_profit']:,.2f}\n")
            lines.append("📈 *Asset Breakdown*")

            for asset, data in analysis['assets'].items():
                if data['amount'] > 0 or data['realized_profit'] != 0:
                    lines.append(
                        f"*{asset}*:\n"
                        f"  Holdings: {data['amount']:.4f} (R {data['current_value']:,.2f})\n"
                        f"  Avg Buy Price: R {data['global_avg_buy_price']:,.2f}\n"
                        f"  Realized: R {data['realized_profit']:,.2f} | Unrealized: R {data['unrealized_profit']:,.2f}"
                    )
            
            await update.message.reply_text("\n".join(lines), parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Profit command error: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Profit calculation failed: {e}")

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

    async def scan_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        if not self.strategy or not self.strategy.ai_client:
            await update.message.reply_text("⚠️ AI Brain is not connected. Check API key.")
            return

        await update.message.reply_text("⏳ 🧠 Waking up the AI...\nScanning all markets for the single best trade...", parse_mode='Markdown')

        try:
            balances = await self.exchange.get_valr_balances()
            top_pick = await self.strategy.ai_market_scan(current_balances=balances)
            
            if top_pick:
                if top_pick not in self.watched_pairs:
                    self.watched_pairs.append(top_pick)
                    action_msg = f"I've added `{top_pick}` to the watchlist and will monitor it for a BUY entry."
                else:
                    action_msg = f"`{top_pick}` is already actively monitored."

                await update.message.reply_text(
                    f"🧠 *AI MARKET SCAN COMPLETE*\n\n"
                    f"Top Pick: `{top_pick}`\n\n"
                    f"{action_msg}",
                    parse_mode='Markdown'
                )
            else:
                await update.message.reply_text("📉 *AI Scan Complete*: The AI did not find any highly profitable setups right now. Waiting for better conditions.", parse_mode='Markdown')

        except Exception as e:
            logger.error(f"Scan command error: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Manual AI Scan failed: {e}")

    async def doublezar_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Toggle or trigger the Double ZAR market-wide scanning mode."""
        if not self._is_authorized(update.effective_user.id):
            return

        # No args = show status
        if not context.args:
            status_emoji = "🟢 ENABLED" if self.double_zar_enabled else "🔴 DISABLED"
            await update.message.reply_text(
                f"💸 *Double ZAR Mode*\n\n"
                f"Status: {status_emoji}\n"
                f"Buy Size: {self.double_zar_buy_pct * 100:.0f}% of ZAR balance per trade\n\n"
                f"When enabled, the bot scans ALL ZAR pairs on VALR every 30 min,\n"
                f"uses AI to find the best buy opportunity, and executes automatically.\n\n"
                f"Commands:\n"
                f"• `/doublezar on` — Enable\n"
                f"• `/doublezar off` — Disable\n"
                f"• `/doublezar scan` — Force immediate scan",
                parse_mode='Markdown'
            )
            return

        action = context.args[0].lower()

        if action == "on":
            self.double_zar_enabled = True
            await update.message.reply_text(
                "🟢 *Double ZAR Mode — ACTIVATED!*\n\n"
                "🚀 The bot will now scan the ENTIRE VALR market every 30 minutes,\n"
                "find the best crypto to buy using AI analysis, and execute automatically.\n\n"
                f"💰 Each trade will use {self.double_zar_buy_pct * 100:.0f}% of your available ZAR.",
                parse_mode='Markdown'
            )
        elif action == "off":
            self.double_zar_enabled = False
            await update.message.reply_text(
                "🔴 *Double ZAR Mode — DEACTIVATED*\n\n"
                "Market-wide scanning has been stopped. The bot will continue\n"
                "monitoring only your watched pairs as usual.",
                parse_mode='Markdown'
            )
        elif action == "scan":
            if not self.exchange or not self.strategy:
                await update.message.reply_text("⚠️ Exchange or Strategy not connected.")
                return

            await update.message.reply_text(
                "⏳ 🧠 *Double ZAR Scan Starting...*\n\n"
                "Scanning ALL ZAR pairs on VALR and asking AI to find the best buy...",
                parse_mode='Markdown'
            )

            try:
                # Get all ZAR market data
                summaries = await self.exchange.get_all_zar_market_summaries()
                if not summaries:
                    await update.message.reply_text("❌ Could not fetch market data from VALR.")
                    return

                # Get ZAR balance
                balances = await self.exchange.get_valr_balances()
                zar_balance = 0.0
                for bal in balances:
                    if bal.get('currency') == 'ZAR':
                        zar_balance = float(bal.get('available', 0))
                        break

                # Run AI scan
                result = await self.strategy.ai_double_zar_scan(summaries, zar_balance)

                if result:
                    pair = result['pair']
                    reason = result['reason']
                    confidence = result['confidence']

                    conf_emoji = {"HIGH": "🟢", "MED": "🟡", "LOW": "🔴"}.get(confidence, "⚪")

                    await update.message.reply_text(
                        f"🧠💸 *DOUBLE ZAR SCAN COMPLETE*\n\n"
                        f"🏆 *Best Buy*: `{pair}`\n"
                        f"📊 *Confidence*: {conf_emoji} {confidence}\n"
                        f"💡 *Reason*: {reason}\n\n"
                        f"💰 Available ZAR: R {zar_balance:,.2f}\n"
                        f"📈 Total pairs scanned: {len(summaries)}",
                        parse_mode='Markdown'
                    )

                    # Auto-watch the pair
                    if pair not in self.watched_pairs:
                        self.watched_pairs.append(pair)
                else:
                    await update.message.reply_text(
                        "📉 *Double ZAR Scan Complete*\n\n"
                        "The AI did not find any strong buy opportunities right now.\n"
                        f"Scanned {len(summaries)} ZAR pairs. Will try again soon.",
                        parse_mode='Markdown'
                    )

            except Exception as e:
                logger.error(f"Double ZAR scan error: {e}", exc_info=True)
                await update.message.reply_text(f"❌ Double ZAR scan failed: {e}")
        else:
            await update.message.reply_text(
                "❌ Unknown option. Use:\n"
                "• `/doublezar on`\n"
                "• `/doublezar off`\n"
                "• `/doublezar scan`",
                parse_mode='Markdown'
            )

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

    async def risk_cmd(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
            
        if not context.args:
            await update.message.reply_text(
                f"⚖️ *Current Trade Size*: {self.risk_pct * 100:.1f}%\n\n"
                "Use `/risk 10` to set position size to 10% of available balance per trade.",
                parse_mode='Markdown'
            )
            return

        try:
            new_risk = float(context.args[0])
            if new_risk <= 0 or new_risk > 100:
                await update.message.reply_text("❌ Percentage must be between 0.1 and 100.")
                return
                
            self.risk_pct = new_risk / 100.0
            await update.message.reply_text(
                f"✅ *Trade Size Updated!*\n\n"
                f"New position size: {new_risk:.1f}% of available balance per trade.",
                parse_mode='Markdown'
            )
            logger.info(f"Risk updated to {self.risk_pct} by user {update.effective_user.id}")
        except ValueError:
            await update.message.reply_text("❌ Invalid value. Use a number like `/risk 10`.", parse_mode='Markdown')

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

    async def handle_ai_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorized(update.effective_user.id):
            return
        if not self.ai_client:
            await update.message.reply_text("⚠️ OpenAI API Key is not configured in .env.")
            return

        user_text = update.message.text
        await self.app.bot.send_chat_action(chat_id=update.effective_user.id, action='typing')
        
        context_data = {
            "watched_pairs": self.watched_pairs,
            "risk_pct": self.risk_pct
        }
        if self.strategy:
            status = []
            for p in self.watched_pairs:
                st = self.strategy.get_status(p)
                if st and st.get('ready'):
                    status.append(st)
            context_data["market_status"] = status
            
        system_prompt = (
            "You are the brain of an autonomous crypto trading bot on VALR. "
            "You have access to the user's current crypto portfolio, market status, "
            "and active bot settings.\n"
            f"Here is current context: {json.dumps(context_data)}\n"
            "You can help analyze strategy, discuss the market, or adjust bot parameters. "
            "You have tools available to execute commands if the user asks you to."
        )

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "check_profit",
                    "description": "Shows the user's overall trading profit, total invested amount, and current portfolio value. Call this when the user asks about their profits.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "execute_sell",
                    "description": "Trigger a smart sell for a specific currency (e.g., XRP).",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "currency": {"type": "string", "description": "The currency symbol, e.g. XRP or BTC"}
                        },
                        "required": ["currency"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "set_risk",
                    "description": "Update the bot's position size percentage.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "percentage": {"type": "number", "description": "Percentage (0-100), e.g. 10"}
                        },
                        "required": ["percentage"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "watch_pair",
                    "description": "Add a pair to the bot's watchlist.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pair": {"type": "string", "description": "VALR trading pair, e.g. XRPZAR"}
                        },
                        "required": ["pair"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "force_scan",
                    "description": "Force the AI Market Scanner to wake up, scan all supported pairs, and pick the best trade.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            }
        ]

        try:
            response = await self.ai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text}
                ],
                tools=tools,
                tool_choice="auto"
            )

            msg = response.choices[0].message
            if msg.tool_calls:
                for tool_call in msg.tool_calls:
                    func_name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)
                    
                    if func_name == "check_profit":
                        await update.message.reply_text(f"🤖 Calculating your profit analysis...")
                        await self.profit_cmd(update, context)
                    elif func_name == "execute_sell":
                        curr = args.get('currency')
                        await update.message.reply_text(f"🤖 Executing smart sell for {curr}...")
                        context.args = [curr]
                        await self.sell_cmd(update, context)
                    elif func_name == "set_risk":
                        pct = args.get('percentage')
                        await update.message.reply_text(f"🤖 Updating risk to {pct}%...")
                        context.args = [str(pct)]
                        await self.risk_cmd(update, context)
                    elif func_name == "watch_pair":
                        p = args.get('pair')
                        await update.message.reply_text(f"🤖 Watching pair {p}...")
                        context.args = [p]
                        await self.watch_cmd(update, context)
                    elif func_name == "force_scan":
                        await update.message.reply_text(f"🤖 Triggering a full market scan...")
                        await self.scan_cmd(update, context)
                
                if msg.content:
                    await update.message.reply_text(msg.content, parse_mode='Markdown')
            else:
                await update.message.reply_text(msg.content, parse_mode='Markdown')

        except Exception as e:
            logger.error(f"AI Chat error: {e}", exc_info=True)
            await update.message.reply_text(f"🤖 Brain is currently offline or errored: {e}")

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
