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

# Risk Management
MAX_POSITION_SIZE_PCT = 0.20
AUTONOMOUS_MODE = True
TRAILING_STOP_LOSS_PCT = 0.01
MAX_OPEN_TRADES = 3

# Trading Pairs
VALR_PAIR = "XRPZAR"
LUNO_PAIR = "XBTZAR"

# Supported pairs on VALR for multi-pair monitoring
SUPPORTED_PAIRS = [
    "BTCZAR", "ETHZAR", "XRPZAR", "SOLZAR", "ADAZAR",
    "DOTZAR", "LINKZAR", "AVAXZAR", "MATICCZAR", "DOGEZAR",
    "USDCZAR", "USDTZAR",
]

# Default pairs to watch on startup
DEFAULT_WATCHED_PAIRS = ["XRPZAR"]

# REST polling interval for non-WebSocket pairs (seconds)
POLL_INTERVAL = 30

# Double ZAR Mode — scans the ENTIRE VALR market for the best buy opportunities
DOUBLE_ZAR_MODE = False  # Master toggle (enable via /doublezar on)
DOUBLE_ZAR_SCAN_INTERVAL = 1800  # Seconds between scans (30 min)
DOUBLE_ZAR_BUY_PCT = 0.10  # Percentage of ZAR balance to spend per trade (10%)
