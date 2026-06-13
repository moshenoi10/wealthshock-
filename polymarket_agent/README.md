# Polymarket Trading Agent

Autonomous Polymarket trading agent — runs 24/7 on Railway with zero human intervention.
Starts in **paper trading mode** by default. Switch to live when ready.

## Quick Start

```bash
cd polymarket_agent
pip install -r requirements.txt
python main.py
```

Open **http://localhost:8080** for the live dashboard.

## Configuration

Edit `.env` (already created with placeholders):

```
PRIVATE_KEY=your_polymarket_wallet_private_key
OPENROUTER_API_KEY=your_openrouter_key   # optional, not used yet
CHAIN_ID=137                              # Polygon mainnet
TRADING_MODE=paper                        # change to "live" when ready
INITIAL_BALANCE=18.0                      # starting paper balance
```

The agent derives Polymarket API credentials automatically from `PRIVATE_KEY` on startup using the POLY_1271 (EIP-1271 / deposit wallet) signature scheme.

## Switching to Live Trading

Once paper trading results satisfy you, POST to the running agent:

```bash
curl -X POST http://localhost:8080/golive
```

Or set `TRADING_MODE=live` in `.env` before starting.

**Hard limits that are always enforced:**
- Max position: 10% of balance
- Stop loss: 15% per trade
- Daily drawdown pause: 8%
- Balance floor: $10 (pauses all trading if hit)
- Orders auto-cancel after 10 seconds if unfilled
- Limit orders only — no market orders

## Deploy to Railway

```bash
npm install -g @railway/cli
railway login
cd polymarket_agent
railway init
railway up
```

Set environment variables in the Railway dashboard (same as `.env`).
The health check endpoint is `GET /health` → returns `200 OK`.

## Strategies

| # | Name | Logic |
|---|------|-------|
| 1 | **Near Resolution** | Markets closing in <5 min with winning side below 0.98 |
| 2 | **Pure Arbitrage** | YES+NO sum < 0.98 → buy both for guaranteed profit |
| 3 | **Directional Arb** | Like pure arb, but 70/30 tilt toward momentum side |
| 4 | **Repricing** | BTC/ETH/SOL lag CoinGecko by >2% → buy undervalued side |
| 5 | **Cross Timeframe** | Same asset, different expiry diverge >3% → buy lagging |
| 6 | **Copy Trading** | Mirror top-10 wallets by win rate; drop after 3 losses |

All 6 run **in parallel** every 30 seconds. The highest-EV opportunity that passes risk checks is executed.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (Railway uses this) |
| GET | `/` | Live dashboard |
| GET | `/api/status` | JSON: balance, PnL, loop count, mode |
| GET | `/api/trades` | Last 50 trades (JSON) |
| GET | `/api/positions` | Open positions (JSON) |
| POST | `/golive` | Switch from paper to live trading |

## Files

```
polymarket_agent/
├── main.py                 Entry point + FastAPI server (port 8080)
├── config.py               All configuration constants
├── agent/
│   ├── core.py             Main 30-second loop, position tracking
│   ├── auth.py             POLY_1271 credential derivation
│   ├── market_data.py      Polymarket CLOB API fetcher
│   └── executor.py         Order placement (paper + live)
├── strategies/
│   ├── base.py             Opportunity dataclass
│   ├── near_resolution.py  Strategy 1
│   ├── pure_arbitrage.py   Strategy 2
│   ├── directional_arb.py  Strategy 3
│   ├── repricing.py        Strategy 4 (CoinGecko)
│   ├── cross_timeframe.py  Strategy 5
│   └── copy_trading.py     Strategy 6 (poly_data + leaderboard)
├── risk/manager.py         Risk filter: size, drawdown, EV guards
├── hedge/system.py         15% auto-hedge + full-hedge escalation
├── logger/trade_logger.py  Append-only trades.json + console
├── dashboard/static/       Single-page HTML dashboard
├── data/
│   ├── trades.json         All trade records
│   └── wallet_scores.json  Copy-trading wallet rankings
├── poly_data/              Cloned on-chain data pipeline (copy trading)
├── .env                    Environment variables
├── railway.json            Railway deployment config
└── requirements.txt        Python dependencies
```

## Paper Trading Console Output

```
============================================================
  Polymarket Trading Agent
  Mode    : PAPER TRADING
  Balance : $18.00 USDC
  Max pos : $1.80 per trade
  Budget  : $18.00 hard cap
============================================================

[AGENT] #0001 | 247 markets | balance=$18.00 | positions=0 | mode=PAPER
[AGENT] Found 14 raw opportunities
[AGENT] Best: near_resolution | EV=0.0320 | Will BTC be above $100k by...
[PAPER] #0001 | near_resolution        | Will BTC be above $100k by...   | BUY @ 0.9680 | $1.80 | EV=0.032
[TRADE] near_resolution       | BUY 1.80 @ 0.9680 | Will BTC be above $100k by...
```
