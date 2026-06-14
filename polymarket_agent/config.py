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

# ── Entry thresholds (reverted to conservative on 2026-06-14) ───────────────
# Lesson learned: loosened thresholds ($18→$10.61, 0% win rate) — quality > quantity
#
# Strategy 1 — Near Resolution
NEAR_RESOLUTION_WINDOW_MIN = 5
NEAR_RESOLUTION_MAX_PRICE = 0.98
NEAR_RESOLUTION_MIN_EDGE = 0.015

# Strategy 2 & 3 — Pure / Directional Arbitrage
ARBITRAGE_MAX_SUM = 0.98
ARBITRAGE_MIN_EDGE = 0.001              # secondary noise guard (keep)

# Strategy 4 — Repricing / Fair Value
REPRICING_MIN_DEVIATION = 0.02

# Strategy 5 — Cross Timeframe
CROSS_TIMEFRAME_MIN_DIVERGENCE = 0.03

# Strategy 6 — Copy Trading
COPY_TRADING_MIN_WIN_RATE = 0.55
COPY_TRADING_MIN_TRADES = 20

# ── Strategy 7 — Exploit: New Market Mispricing ──────────────────────────────
EXPLOIT_NM_MAX_AGE_HOURS  = 1.0    # only markets younger than this
EXPLOIT_NM_MAX_VOLUME     = 500.0  # low volume = still price-discovering
EXPLOIT_NM_MIN_LIQUIDITY  = 100.0  # must have some depth so fill is possible
EXPLOIT_NM_MIN_PRICE      = 0.22   # don't touch extreme mispricings
EXPLOIT_NM_MAX_PRICE      = 0.46   # buy side below apparent fair 0.50
EXPLOIT_NM_MIN_MISPRICING = 0.04   # at least 4% below prior (was 3% — conservative)
EXPLOIT_NM_CONVERGENCE_FACTOR = 0.50  # expect 50% of gap to close → EV multiplier
EXPLOIT_NM_SIZE_PCT       = 0.05   # 5% of balance per trade
EXPLOIT_NM_MAX_TRADE_USD  = float(os.getenv("EXPLOIT_NM_MAX_USD", "50"))  # $50 cap for $1K test

# ── Strategy 8 — Exploit: Liquidity Trap ─────────────────────────────────────
EXPLOIT_LT_MAX_LIQUIDITY  = 5_000.0  # thin markets only
EXPLOIT_LT_MIN_LIQUIDITY  = 80.0     # not completely dead
EXPLOIT_LT_MIN_SPREAD     = 0.020    # YES+NO must be ≤ 0.980 (2%+ gap)
EXPLOIT_LT_SIZE_PCT       = 0.05     # 5% of balance per trade
EXPLOIT_LT_MAX_TRADE_USD  = float(os.getenv("EXPLOIT_LT_MAX_USD", "50"))  # $50 cap for $1K test

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
