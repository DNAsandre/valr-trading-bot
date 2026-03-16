# Human-In-The-Loop (HITL) VALR Crypto Trading Bot

A Python 3.10+ async-first semi-autonomous cryptocurrency trading bot tailored for the VALR Exchange (primary execution & streaming) and Luno (secondary data). 

## Architecture 🛠️
- **Language**: Python 3.10+ leveraging `asyncio` for non-blocking Websocket streams natively.
- **Exchanges**: Integrates the official `valr-python` library and asynchronous Luno REST fetches using `aiohttp`. 
- **Communications**: `python-telegram-bot` interfaces directly with Telegram API for secure, actionable callback keyboards allowing for safe 1-tap trade executions.
- **Strategy & Insights**: Utilizes `pandas_ta` to assess MACD momentum, Bollinger Band volatility, and RSI positioning to output comprehensive, natural-language Insight payloads before executing any targets.

## 🚀 Deployment Doctrine
**CRITICAL**: This project is hosted live on **Railway**. 

1. **Live Environment**: The Telegram bot is controlled by the Railway deployment, NOT by local execution.
2. **Standard Operating Procedure**: After making any local changes (commands, strategy, logic), you **MUST** deploy them live by running:
   ```bash
   railway up
   ```
   Always verify the live bot's behavior after deployment.


## Security & Risk Constraints 🔐
1. **API Keys Scoping**: When making keys on VALR, grant **ONLY "Trade" and "View" permissions**. **NEVER provide "Withdraw" access.**
2. **Rate Limits Checked**: Internal classes strictly accommodate HTTP 429 Status Limits with exponentially enforced timeout bounds logic, limiting account bans.
3. **Hardcapped Positions**: Native `position_size_zar` calculations never mathematically exceed the global `MAX_POSITION_SIZE_PCT` core parameter (Currently defined at 5% allocated portfolio size per signal).

## Pre-requisites & Local Environment 💻
1. Clone / Change your contextual working Directory `Trader Bot`.
2. Target a valid interpreter running Python v3.10 or higher.
3. Generate VALR API Keys directly on the VALR platform (View/Trade Scopes Only).
4. Register a Telegram Bot using `@BotFather` on Telegram and fetch your specific Account `ID`.

## Installation 🚀
1. Install requirement dependencies securely via pip mapping:
   ```bash
   pip install -r requirements.txt
   ```
2. Replicate the Environmental Config natively referencing the example structure:
   ```bash
   cp .env.example .env
   ```
3. Populate `.env`:
   - Inject your respective `VALR_API_KEY`/`VALR_API_SECRET` mapped definitions safely.
   - Provide your Telegram setup credentials (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`). *(Note: Multi-user scoping supports generalized comma-separated arrays)*
4. Run python to engage the Engine logic loops:
   ```bash
   python main.py
   ```
   
> **Warning**: Ensure you test appropriately targeting safe test-nets or micro-funds before enabling unrestricted orders. The bot operates natively on real exchange funds upon explicit Execution callback prompts.
