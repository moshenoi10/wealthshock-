import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

POLYMARKET_HOST = os.getenv("POLYMARKET_HOST", "https://clob.polymarket.com")
GAMMA_HOST = "https://gamma-api.polymarket.com"
CHAIN_ID = int(os.getenv("CHAIN_ID", "137"))
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")

TRADING_MODE = os.getenv("TRADING_MODE", "paper")
INITIAL_BALANCE = float(os.getenv("INITIAL_BALANCE", "18.0"))
MIN_BALANCE = 10.0
MAX_TOTAL_BUDGET = 18.0

# ── Risk management (DO NOT change — user instruction) ──────────────────────
MAX_POSITION_PCT = 0.10
STOP_LOSS_PCT = 0.15
DAILY_DRAWDOWN_LIMIT = 0.08
MIN_LIQUIDITY = 500.0

# ── Entry thresholds (loosened 2026-06-14) ───────────────────────────────────
# Strategy 1 — Near Resolution
NEAR_RESOLUTION_WINDOW_MIN = 5
NEAR_RESOLUTION_MAX_PRICE = 0.98
NEAR_RESOLUTION_MIN_EDGE = 0.005        # was 0.015

# Strategy 2 & 3 — Pure / Directional Arbitrage
ARBITRAGE_MAX_SUM = 0.995               # was 0.98
ARBITRAGE_MIN_EDGE = 0.001              # secondary noise guard

# Strategy 4 — Repricing / Fair Value
REPRICING_MIN_DEVIATION = 0.01         # was 0.02

# Strategy 5 — Cross Timeframe
CROSS_TIMEFRAME_MIN_DIVERGENCE = 0.015 # was 0.03

# Strategy 6 — Copy Trading
COPY_TRADING_MIN_WIN_RATE = 0.44       # was 0.55  (×0.8)
COPY_TRADING_MIN_TRADES = 16           # was 20    (×0.8)

# ── Hedge ────────────────────────────────────────────────────────────────────
HEDGE_PCT = 0.15
FULL_HEDGE_TRIGGER_PCT = 0.08

# ── Loop / orders ─────────────────────────────────────────────────────────────
LOOP_INTERVAL = 30
ORDER_CANCEL_DELAY = 10

# ── Server ───────────────────────────────────────────────────────────────────
PORT = int(os.getenv("PORT", "8080"))

# ── Data files ───────────────────────────────────────────────────────────────
DATA_DIR = Path("data")
TRADES_FILE = DATA_DIR / "trades.json"
WALLET_SCORES_FILE = DATA_DIR / "wallet_scores.json"
REJECTED_FILE = DATA_DIR / "rejected_opportunities.json"
