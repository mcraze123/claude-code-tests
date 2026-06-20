import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ROBINHOOD_MCP_URL = "https://agent.robinhood.com/mcp/trading"
ROBINHOOD_MCP_TOKEN = os.environ.get("ROBINHOOD_MCP_TOKEN")

# Claude model — use Opus for best trading intelligence
MODEL = "claude-opus-4-8"

# Risk per trade (fraction of account)
MAX_RISK_PER_TRADE = 0.02
MAX_POSITIONS = 5
MIN_RISK_REWARD = 2.0
MAX_POSITION_VALUE_USD = 500.0

# Screener filters
MIN_PRICE = 0.50
MAX_PRICE = 500.0
MIN_AVG_DAILY_VOLUME = 300_000
MIN_RELATIVE_VOLUME = 1.5

# Minimum score (0–100) to consider trading a setup
MIN_SCORE_TO_TRADE = 65

# Data windows per timeframe
TIMEFRAME_PARAMS = {
    "daily": {"period": "90d", "interval": "1d"},
    "4h":    {"period": "30d", "interval": "1h"},   # resampled from 1h
    "1h":    {"period": "7d",  "interval": "1h"},
    "15m":   {"period": "5d",  "interval": "15m"},
}

# Tickers to always include in screen even if not top-movers
CORE_WATCHLIST = [
    "SPY", "QQQ", "IWM",
    "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "META", "GOOGL", "AMZN",
    "NFLX", "PLTR", "SOFI", "SMCI", "ARM", "AVGO", "MU",
    "MARA", "RIOT", "COIN", "MSTR",
    "HOOD", "SQ", "PYPL",
    "RIVN", "LCID", "NIO",
    "GME", "AMC",
]
