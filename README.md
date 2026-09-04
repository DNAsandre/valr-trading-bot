# Human-In-The-Loop (HITL) VALR Crypto Trading Bot

A Python 3.10+ async-first semi-autonomous cryptocurrency trading bot tailored for the VALR Exchange (primary execution & streaming) and Luno (secondary data). 

## Architecture 🛠️
- **Language**: Python 3.10+ leveraging `asyncio` for non-blocking Websocket streams natively.
- **Exchanges**: Integrates the official `valr-python` library and asynchronous Luno REST fetches using `aiohttp`. 
- **Communications**: `python-telegram-bot` interfaces directly with Telegram API for secure, actionable callback keyboards allowing for safe 1-tap trade executions.
- **Strategy & Insights**: Utilizes `pandas_ta` to assess MACD momentum, Bollinger Band volatility, and RSI positioning to output comprehensive, natural-language Insight payloads before executing any targets.

## Local v2 operation — XRP/ZAR paper trading first

This bot runs locally as a macOS LaunchAgent; Railway is no longer required.

### Safety defaults

- **Only `XRPZAR` can be watched, subscribed, or submitted for execution.**
- **`TRADING_MODE=paper` is the default.** In this mode order attempts are simulated locally; they never reach VALR’s order endpoint.
- A simulated portfolio is seeded once from a read-only balance snapshot, stored locally without credentials, and reported daily at **18:00 SAST**.
- The v2 risk limits default to a **2%** buy size, **one position**, **three executions/day**, **15-minute cooldown**, and **R50 realised daily-loss limit**.
- Signals are evaluated from **closed 5-minute candles**, not every price tick.

### Before any future live consideration

1. Keep VALR keys restricted to **View + Trade only** — never withdrawal permission.
2. Run and evaluate paper mode for a meaningful sample, including fees and daily reports.
3. Verify the Telegram numeric allowlist is paired to your current chat.
4. Make a separate, explicit decision before changing `TRADING_MODE` to `live`.

The bot makes no profitability promise. All live trading remains real-money risk.

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
