"""
Maker Spread Simulator — paper trading only.

Distinct from market_maker_sim (bundle arb). This strategy provides
two-sided liquidity on BTC markets by quoting a bid/ask spread:

  mid = (YES + NO) / 2
  bid = mid - HALF_SPREAD   (we buy YES here)
  ask = mid + HALF_SPREAD   (we sell YES here)

Simulation logic:
  - Open a "book" for each top-5 liquid BTC market
  - Every scan (30s), compare current mid to our quoted levels
  - If price moves DOWN through our bid: bid filled → we're long YES
  - If price moves UP through our ask: ask filled → we're short YES (long NO)
  - When both sides of a book fill (round trip): earn 2 × HALF_SPREAD per unit
  - Single-leg open for > MAX_LEG_AGE_S: close at current market price

Liquidity rewards: modeled as REWARD_PER_SCAN per active book (0.001/scan)
to simulate Polymarket LP incentives (conservative estimate).

Virtual balance: $500 | Max $50/market × 5 markets | HALF_SPREAD = 1.5%
"""
import time
import json as _json
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

INITIAL_BALANCE = 500.0
MAX_TRADE_USD   = 50.0         # per market side (total book = 2×)
TOP_N_MARKETS   = 5
HALF_SPREAD     = 0.015        # 1.5% each side → 3% total spread earned
MAX_LEG_AGE_S   = 300          # 5 min before we close a single-leg
REWARD_PER_SCAN = 0.001        # simulated LP reward per book per scan
CACHE_TTL       = 20

_balance:      float = INITIAL_BALANCE
_total_pnl:    float = 0.0
_wins:         int   = 0
_total_trades: int   = 0
_books:        Dict[str, dict] = {}   # keyed by condition_id
_sim_trades:   deque = deque(maxlen=200)
_scan_log:     deque = deque(maxlen=50)
_scan_count:   int   = 0
_last_scan_ts: Optional[str] = None
_cache_ts:     float = 0.0
_mkt_cache:    List[dict] = []
_lp_rewards:   float = 0.0


# ─── Market fetch ──────────────────────────────────────────────────────────────

async def _fetch_top_btc_markets() -> List[dict]:
    global _cache_ts, _mkt_cache
    if time.time() - _cache_ts < CACHE_TTL:
        return _mkt_cache

    results: List[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://gamma-api.polymarket.com/markets",
                params={
                    "active": "true", "closed": "false",
                    "limit": 300, "order": "volume", "ascending": "false",
                },
            )
            if r.status_code != 200:
                return _mkt_cache
            batch = r.json()
            if not isinstance(batch, list):
                batch = batch.get("data", [])

            for raw in batch:
                if "BTC" not in raw.get("question", "").upper():
                    continue
                liq = float(raw.get("liquidity") or 0)
                if liq < 500:
                    continue

                outcomes = _json.loads(raw.get("outcomes")      or "[]")
                prices   = _json.loads(raw.get("outcomePrices") or "[]")
                yes_p = no_p = None
                for i, o in enumerate(outcomes):
                    p = float(prices[i]) if i < len(prices) else 0.0
                    if o.upper() == "YES": yes_p = p
                    elif o.upper() == "NO":  no_p  = p
                if yes_p is None or no_p is None:
                    continue
                # Skip near-resolved or near-zero (no meaningful spread to quote)
                if yes_p > 0.95 or yes_p < 0.05:
                    continue

                results.append({
                    "condition_id": raw.get("conditionId", ""),
                    "question":     raw.get("question", ""),
                    "yes_price":    yes_p,
                    "no_price":     no_p,
                    "liquidity":    liq,
                    "volume":       float(raw.get("volume") or 0),
                })
                if len(results) >= TOP_N_MARKETS:
                    break
    except Exception as e:
        print(f"[MAKER-SPREAD] Fetch error: {e}")

    if results:
        _mkt_cache = results
        _cache_ts  = time.time()
    return _mkt_cache


# ─── Book management ───────────────────────────────────────────────────────────

def _open_book(mkt: dict, ts: str):
    """Open a two-sided quote book for a market."""
    global _balance
    mid  = (mkt["yes_price"] + mkt["no_price"]) / 2
    size = min(MAX_TRADE_USD, _balance / max(len(_books) + 1, 1))
    if size < 5:
        return

    _balance = round(_balance - size * 2, 4)   # reserve both sides
    _books[mkt["condition_id"]] = {
        "question":     mkt["question"][:70],
        "mid_quoted":   round(mid, 4),
        "bid":          round(mid - HALF_SPREAD, 4),
        "ask":          round(mid + HALF_SPREAD, 4),
        "size":         round(size, 4),
        "bid_filled":   False,
        "ask_filled":   False,
        "bid_fill_ts":  None,
        "ask_fill_ts":  None,
        "open_ts":      time.time(),
        "scans":        0,
    }
    print(
        f"[MAKER-SPREAD] BOOK '{mkt['question'][:40]}' "
        f"bid={mid - HALF_SPREAD:.3f} ask={mid + HALF_SPREAD:.3f} size=${size:.2f}"
    )


def _update_book(cid: str, current_yes: float, ts: str):
    """Check fills and settle if complete or too old."""
    global _balance, _total_pnl, _wins, _total_trades, _lp_rewards

    book = _books.get(cid)
    if not book:
        return False   # already closed

    book["scans"] += 1

    # Simulate fill: if market YES dipped to/below our bid → bid filled
    if not book["bid_filled"] and current_yes <= book["bid"]:
        book["bid_filled"]  = True
        book["bid_fill_ts"] = ts
        print(f"[MAKER-SPREAD] BID FILLED {book['question'][:40]} @{current_yes:.3f}")

    # If market YES rose to/above our ask → ask filled
    if not book["ask_filled"] and current_yes >= book["ask"]:
        book["ask_filled"]  = True
        book["ask_fill_ts"] = ts
        print(f"[MAKER-SPREAD] ASK FILLED {book['question'][:40]} @{current_yes:.3f}")

    # LP reward per scan regardless of fills
    reward = REWARD_PER_SCAN * book["size"]
    _lp_rewards = round(_lp_rewards + reward, 6)
    _balance    = round(_balance + reward, 6)
    _total_pnl  = round(_total_pnl + reward, 6)

    age   = time.time() - book["open_ts"]
    close = False
    won   = False
    reason = ""

    if book["bid_filled"] and book["ask_filled"]:
        # Full round trip: we earned the spread on both sides
        spread_earned = round(HALF_SPREAD * 2 * book["size"], 4)
        _balance    = round(_balance + book["size"] * 2 + spread_earned, 4)
        _total_pnl  = round(_total_pnl + spread_earned, 4)
        _total_trades += 1
        _wins += 1
        close = True
        won   = True
        reason = "round_trip"
        _sim_trades.appendleft({
            "ts": ts, "question": book["question"][:70],
            "type": "round_trip", "bid": book["bid"], "ask": book["ask"],
            "size": book["size"], "spread_earned": spread_earned,
            "lp_reward": round(reward * book["scans"], 6),
            "held_s": round(age), "pnl": round(spread_earned, 4), "won": True,
        })

    elif age > MAX_LEG_AGE_S:
        # Single-leg timeout: close at current mid
        mid_now = current_yes
        if book["bid_filled"] and not book["ask_filled"]:
            # Long YES at bid_price, exit at current mid
            pnl = round((mid_now - book["bid"]) * book["size"], 4)
            _balance = round(_balance + book["size"] + pnl, 4)
        elif book["ask_filled"] and not book["bid_filled"]:
            # Short YES at ask_price, exit at current mid
            pnl = round((book["ask"] - mid_now) * book["size"], 4)
            _balance = round(_balance + book["size"] + pnl, 4)
        else:
            # Neither filled, cancel → refund reserved capital
            pnl = 0.0
            _balance = round(_balance + book["size"] * 2, 4)
        _total_pnl  = round(_total_pnl + pnl, 4)
        _total_trades += 1
        if pnl > 0:
            _wins += 1
        close  = True
        reason = f"timeout_{'bid' if book['bid_filled'] else 'ask' if book['ask_filled'] else 'none'}"
        _sim_trades.appendleft({
            "ts": ts, "question": book["question"][:70],
            "type": reason, "bid": book["bid"], "ask": book["ask"],
            "size": book["size"], "spread_earned": pnl,
            "lp_reward": round(reward * book["scans"], 6),
            "held_s": round(age), "pnl": round(pnl, 4), "won": pnl > 0,
        })

    if close:
        _books.pop(cid, None)

    return close


# ─── Main scan ─────────────────────────────────────────────────────────────────

async def scan() -> dict:
    global _balance, _scan_count, _last_scan_ts

    _scan_count += 1
    ts = datetime.now(timezone.utc).isoformat()
    _last_scan_ts = ts

    markets = await _fetch_top_btc_markets()

    # Update existing books
    mkt_idx = {m["condition_id"]: m for m in markets}
    for cid in list(_books.keys()):
        cur_yes = mkt_idx[cid]["yes_price"] if cid in mkt_idx else _books[cid]["mid_quoted"]
        _update_book(cid, cur_yes, ts)

    # Open new books for markets not already tracked
    for mkt in markets:
        cid = mkt["condition_id"]
        if cid not in _books and len(_books) < TOP_N_MARKETS:
            _open_book(mkt, ts)

    open_books = [
        {
            "question":   b["question"][:60],
            "bid":        b["bid"],
            "ask":        b["ask"],
            "size":       b["size"],
            "bid_filled": b["bid_filled"],
            "ask_filled": b["ask_filled"],
            "scans":      b["scans"],
            "held_min":   round((time.time() - b["open_ts"]) / 60, 1),
        }
        for b in _books.values()
    ]

    _scan_log.appendleft({
        "ts": ts, "scan": _scan_count,
        "markets_tracked": len(markets),
        "books_open": len(_books),
        "lp_rewards_total": round(_lp_rewards, 4),
    })

    print(
        f"[MAKER-SPREAD] #{_scan_count} books={len(_books)}/{TOP_N_MARKETS} "
        f"bal=${_balance:.2f} lp_rewards=${_lp_rewards:.4f}"
    )
    return get_status()


def get_status() -> dict:
    tot = _total_trades
    return {
        "balance":        round(_balance, 2),
        "total_pnl":      round(_total_pnl, 4),
        "lp_rewards":     round(_lp_rewards, 4),
        "trades":         tot,
        "wins":           _wins,
        "win_rate":       round(_wins / tot, 4) if tot else 0.0,
        "open_books":     [
            {
                "question":   b["question"][:60],
                "bid":        b["bid"],
                "ask":        b["ask"],
                "spread_pct": round(HALF_SPREAD * 2 * 100, 1),
                "size":       b["size"],
                "bid_filled": b["bid_filled"],
                "ask_filled": b["ask_filled"],
                "held_min":   round((time.time() - b["open_ts"]) / 60, 1),
            }
            for b in _books.values()
        ],
        "sim_trades":     list(_sim_trades)[:20],
        "scan_log":       list(_scan_log)[:10],
        "scan_count":     _scan_count,
        "last_scan":      _last_scan_ts,
        "initial_balance": INITIAL_BALANCE,
        "half_spread":    HALF_SPREAD,
        "top_n":          TOP_N_MARKETS,
    }
