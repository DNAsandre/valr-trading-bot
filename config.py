import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# VALR Config
VALR_API_KEY = os.getenv("VALR_API_KEY", "")
VALR_API_SECRET = os.getenv("VALR_API_SECRET", "")

# Luno Config (Optional/Public)
LUNO_API_KEY = os.getenv("LUNO_API_KEY", "")
LUNO_API_SECRET = os.getenv("LUNO_API_SECRET", "")

# AI Config
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Telegram Config
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_allowed_users_env = os.getenv("TELEGRAM_ALLOWED_USERS", "")
TELEGRAM_ALLOWED_USERS = [
    int(u.strip()) for u in _allowed_users_env.split(",") if u.strip().isdigit()
]

# Execution safety. V2 is paper-only until the operator explicitly changes this
# value and approves a separate live launch.
TRADING_MODE = os.getenv("TRADING_MODE", "paper").strip().lower()
if TRADING_MODE not in {"paper", "live"}:
    raise ValueError("TRADING_MODE must be either 'paper' or 'live'.")

# Risk Management
MAX_POSITION_SIZE_PCT = 0.02
AUTONOMOUS_MODE = True
TRAILING_STOP_LOSS_PCT = 0.01
MAX_OPEN_TRADES = 1
MAX_DAILY_LOSS_ZAR = float(os.getenv("MAX_DAILY_LOSS_ZAR", "50"))
TRADE_COOLDOWN_SECONDS = int(os.getenv("TRADE_COOLDOWN_SECONDS", "900"))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "3"))
PAPER_FEE_PCT = float(os.getenv("PAPER_FEE_PCT", "0.002"))
DAILY_REPORT_HOUR_SAST = int(os.getenv("DAILY_REPORT_HOUR_SAST", "18"))
PAPER_STATE_PATH = os.getenv(
    "PAPER_STATE_PATH",
    "/Users/Shared/Hermes Agent Workspace/valr-trading-bot-paper-state.json",
)

# Trading Pairs
# This bot is intentionally locked to XRP/ZAR.  Keep execution and the
# Telegram watchlist constrained to this pair unless the user explicitly
# changes the strategy scope.
VALR_PAIR = "XRPZAR"
SUPPORTED_PAIRS = [VALR_PAIR]
LUNO_PAIR = "XBTZAR"

# Default pairs to watch on startup
DEFAULT_WATCHED_PAIRS = [VALR_PAIR]

# REST polling interval for non-WebSocket pairs (seconds)
POLL_INTERVAL = 30

# Double ZAR Mode — scans the ENTIRE VALR market for the best buy opportunities
DOUBLE_ZAR_MODE = False  # Master toggle (enable via /doublezar on)
DOUBLE_ZAR_SCAN_INTERVAL = 1800  # Seconds between scans (30 min)
DOUBLE_ZAR_BUY_PCT = 0.10  # Percentage of ZAR balance to spend per trade (10%)
