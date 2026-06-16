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

# ── Risk management — LIVE MODE (never loosened — DO NOT change these) ──────
# These values protect real funds. Paper mode uses PAPER_* overrides below.
MAX_POSITION_PCT     = 0.10   # 10% per trade in live mode
STOP_LOSS_PCT        = 0.15   # 15% stop loss in live mode
DAILY_DRAWDOWN_LIMIT = 0.08   # 8% daily drawdown in live mode
MIN_LIQUIDITY        = 500.0

# ── Live mode entry thresholds (conservative — DO NOT change) ────────────────
NEAR_RESOLUTION_WINDOW_MIN    = 10
NEAR_RESOLUTION_MAX_PRICE     = 0.95
NEAR_RESOLUTION_MIN_EDGE      = 0.005
NEAR_RESOLUTION_MIN_LIQUIDITY = 10.0
ARBITRAGE_MAX_SUM             = 0.98
ARBITRAGE_MIN_EDGE            = 0.001
REPRICING_MIN_DEVIATION       = 0.02
CROSS_TIMEFRAME_MIN_DIVERGENCE = 0.03
COPY_TRADING_MIN_WIN_RATE     = 0.55
COPY_TRADING_MIN_TRADES       = 20

# ── PAPER MODE — Aggressive thresholds (only used when TRADING_MODE=paper) ──
# Safe to change freely: no real funds involved.
PAPER_MAX_POSITION_PCT            = 0.25   # 25% per paper trade
PAPER_DAILY_DRAWDOWN_LIMIT        = 0.50   # halt only at 50% paper loss
PAPER_MIN_BALANCE                 = 0.50   # trade until nearly nothing
PAPER_LOOP_INTERVAL               = 10     # scan every 10s (was 30)
PAPER_NEAR_RESOLUTION_WINDOW_MIN  = 30     # 30-min window (was 10)
PAPER_NEAR_RESOLUTION_MAX_PRICE   = 0.85   # buy anything below 85%
PAPER_NEAR_RESOLUTION_MIN_EDGE    = 0.003  # 0.3% EV minimum
PAPER_ARBITRAGE_MAX_SUM           = 0.99   # tighter spreads accepted
PAPER_REPRICING_MIN_DEVIATION     = 0.01   # 1% (was 2%)
PAPER_CROSS_TIMEFRAME_MIN_DIV     = 0.015  # 1.5% (was 3%)
PAPER_MOMENTUM_MIN_MOVE           = 0.015  # 1.5% price move triggers momentum
PAPER_MOMENTUM_LOOKBACK_SCANS     = 3      # must move same direction for N scans
PAPER_OVERREACTION_JUMP_PCT       = 0.08   # 8% jump in 60s triggers fade
PAPER_OVERREACTION_HOLD_SCANS     = 6      # hold the fade for up to N scans

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
