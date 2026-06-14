"""
BTC Lag Arb — independent paper trading simulator.

Detects lag between the real BTC/USD price (Binance) and Polymarket binary
markets that resolve on BTC price levels.

When real BTC is clearly above/below a strike price but the Polymarket YES/NO
price hasn't repriced yet, we buy the correct side and hold until the market
catches up (or 15 minutes max).

Virtual balance: $1,000  (fully independent of all other strategies)
"""
import asyncio
import re
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

INITIAL_BALANCE   = 1_000.0
MAX_TRADE_USD     = 100.0    # 10% cap per trade
MIN_LAG_PCT       = 0.02     # 2% minimum lag to enter
EXIT_TARGET       = 0.90     # exit long when price reaches this
STOP_LOSS         = 0.30     # stop loss
MAX_HOLD_SECONDS  = 900      # 15 min max hold
MARKET_CACHE_TTL  = 300      # refresh market list every 5 min
BTC_PRICE_TTL     = 8        # don't re-fetch within 8s

_balance:      float = INITIAL_BALANCE
_total_pnl:    float = 0.0
_wins:         int   = 0
_total_trades: int   = 0
_positions:    Dict[str, dict] = {}
_sim_trades:   deque = deque(maxlen=200)
_scan_log:     deque = deque(maxlen=50)
_btc_price:    Optional[float] = None
_btc_price_ts: float = 0.0
_markets_cache: List[dict] = []
_markets_ts:   float = 0.0
_opportunities: List[dict] = []
_last_scan_ts: Optional[str] = None
_scan_count:   int = 0

# ─── Regex to extract dollar strike prices ───────────────────────────────────
_DOLLAR_RE = re.compile(
    r'\$\s*([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*([kK]?)'
    r'|([0-9]{2,3})\s*[kK]\b',
    re.IGNORECASE,
)


def _parse_strike(question: str) -> Optional[float]:
    """Return USD strike price from a question string, or None."""
    for m in _DOLLAR_RE.finditer(question):
        g1, suffix, g3 = m.group(1), m.group(2), m.group(3)
        raw = (g1 or g3 or "").replace(",", "")
        if not raw:
            continue
        try:
            val = float(raw)
            if suffix.upper() == "K" or g3:
                val *= 1_000
            if 10_000 <= val <= 200_000:
                return val
        except ValueError:
            pass
    return None


def _parse_direction(question: str) -> Optional[str]:
    ql = question.lower()
    if any(w in ql for w in ["above", "over", "higher", "exceed", "≥", ">="]):
        return "above"
    if any(w in ql for w in ["below", "under", "lower", "less than", "≤", "<="]):
        return "below"
    return None


# ─── Data fetchers ────────────────────────────────────────────────────────────

async def _fetch_btc_price() -> Optional[float]:
    global _btc_price, _btc_price_ts
    if time.time() - _btc_price_ts < BTC_PRICE_TTL:
        return _btc_price
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"},
            )
            if r.status_code == 200:
                p = float(r.json()["price"])
                _btc_price = p
                _btc_price_ts = time.time()
                return p
            print(f"[BTC-LAG] Binance HTTP {r.status_code}")
    except Exception as e:
        print(f"[BTC-LAG] Binance error: {e}")
    return _btc_price


async def _fetch_btc_markets() -> List[dict]:
    global _markets_cache, _markets_ts
    if time.time() - _markets_ts < MARKET_CACHE_TTL:
        return _markets_cache

    import json as _json
    results = []
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            # Try both tag_slug and free-text search
            for params in [
                {"active": "true", "closed": "false", "limit": 100, "tag_slug": "crypto"},
                {"active": "true", "closed": "false", "limit": 100, "keyword": "bitcoin"},
            ]:
                resp = await c.get("https://gamma-api.polymarket.com/markets", params=params)
                if resp.status_code != 200:
                    continue
                batch = resp.json()
                if not isinstance(batch, list):
                    batch = batch.get("markets", [])

                for raw in batch:
                    q = raw.get("question", "")
                    if not any(kw in q.upper() for kw in ["BTC", "BITCOIN"]):
                        continue
                    strike = _parse_strike(q)
                    if not strike:
                        continue
                    direction = _parse_direction(q)
                    if not direction:
                        continue
                    if raw.get("id") in {m["id"] for m in results}:
                        continue

                    outcomes  = _json.loads(raw.get("outcomes")      or "[]")
                    prices    = _json.loads(raw.get("outcomePrices") or "[]")
                    token_ids = _json.loads(raw.get("clobTokenIds")  or "[]")

                    if len(outcomes) < 2:
                        continue

                    yes_price = yes_token = no_price = no_token = None
                    for i, o in enumerate(outcomes):
                        p   = float(prices[i])    if i < len(prices)    else 0.0
                        tid = token_ids[i]         if i < len(token_ids) else ""
                        if o.upper() == "YES":
                            yes_price, yes_token = p, tid
                        elif o.upper() == "NO":
                            no_price, no_token = p, tid

                    if yes_price is None or not yes_token:
                        continue

                    results.append({
                        "id":          raw.get("id", ""),
                        "question":    q,
                        "strike":      strike,
                        "direction":   direction,
                        "yes_price":   yes_price,
                        "no_price":    no_price if no_price is not None else round(1.0 - yes_price, 4),
                        "yes_token":   yes_token,
                        "no_token":    no_token or "",
                        "liquidity":   float(raw.get("liquidity") or 0),
                        "end_date":    raw.get("endDate", ""),
                    })

    except Exception as e:
        print(f"[BTC-LAG] Market fetch error: {e}")

    if results:
        _markets_cache = results
        _markets_ts = time.time()
        print(f"[BTC-LAG] Cached {len(results)} BTC strike markets")
    return _markets_cache


# ─── Lag calculation ─────────────────────────────────────────────────────────

def _compute_lag(real_btc: float, market: dict) -> Optional[dict]:
    """
    Return a dict with trade signal if actionable lag found, else None.

    Expected YES probability is modelled as:
      sigmoid-like: 0.5 + dist_pct * 4, clamped [0.07, 0.93]
    where dist_pct = (real_btc - strike) / strike for "above" markets.
    """
    strike    = market["strike"]
    direction = market["direction"]
    yes_price = market["yes_price"]
    no_price  = market["no_price"]

    dist_pct = (real_btc - strike) / strike  # + = BTC above strike

    if direction == "above":
        expected = max(0.07, min(0.93, 0.50 + dist_pct * 4.0))
        lag = expected - yes_price
        if lag >= MIN_LAG_PCT:
            return {
                "side":     "YES",
                "token":    market["yes_token"],
                "price":    yes_price,
                "expected": round(expected, 4),
                "lag":      round(lag, 4),
                "dist_pct": round(dist_pct, 4),
                "action":   (
                    f"BTC ${real_btc:,.0f} is {dist_pct*100:+.1f}% vs "
                    f"${strike:,.0f} strike — YES should be ~{expected:.2f}, is {yes_price:.3f}"
                ),
            }
        lag = yes_price - (1.0 - expected)  # overpriced YES → buy NO
        if lag >= MIN_LAG_PCT and dist_pct < -MIN_LAG_PCT:
            no_exp = 1.0 - expected
            return {
                "side":     "NO",
                "token":    market["no_token"],
                "price":    no_price,
                "expected": round(no_exp, 4),
                "lag":      round(yes_price - expected, 4),
                "dist_pct": round(dist_pct, 4),
                "action":   (
                    f"BTC ${real_btc:,.0f} is {dist_pct*100:+.1f}% vs "
                    f"${strike:,.0f} strike — NO should be ~{no_exp:.2f}"
                ),
            }

    elif direction == "below":
        # "BTC below $X" → YES if BTC < strike
        dist_pct_inv = -dist_pct
        expected = max(0.07, min(0.93, 0.50 + dist_pct_inv * 4.0))
        lag = expected - yes_price
        if lag >= MIN_LAG_PCT:
            return {
                "side":     "YES",
                "token":    market["yes_token"],
                "price":    yes_price,
                "expected": round(expected, 4),
                "lag":      round(lag, 4),
                "dist_pct": round(dist_pct, 4),
                "action":   (
                    f"BTC ${real_btc:,.0f} is {dist_pct_inv*100:.1f}% BELOW "
                    f"${strike:,.0f} — YES should be ~{expected:.2f}, is {yes_price:.3f}"
                ),
            }

    return None


# ─── Position management ─────────────────────────────────────────────────────

def _check_exits(btc_price: float, market_idx: Dict[str, dict], ts: str):
    global _balance, _total_pnl, _wins, _total_trades

    to_close = []
    for tid, pos in _positions.items():
        mkt = market_idx.get(tid)
        if mkt:
            cur = mkt["yes_price"] if pos["side"] == "YES" else mkt["no_price"]
        else:
            cur = pos["entry_price"]

        held_s = time.time() - pos["entry_time"]
        reason = None
        if cur >= EXIT_TARGET:
            reason = f"target {cur:.3f}"
        elif cur <= STOP_LOSS:
            reason = f"stop-loss {cur:.3f}"
        elif held_s >= MAX_HOLD_SECONDS:
            reason = f"timeout {int(held_s/60)}min"

        if reason:
            pnl = round(pos["size"] * (cur - pos["entry_price"]), 4)
            _balance    = round(_balance    + pnl, 4)
            _total_pnl  = round(_total_pnl  + pnl, 4)
            _total_trades += 1
            if pnl > 0:
                _wins += 1
            _sim_trades.appendleft({
                "ts":           ts,
                "question":     pos["question"][:70],
                "side":         pos["side"],
                "entry_price":  pos["entry_price"],
                "exit_price":   round(cur, 4),
                "size":         pos["size"],
                "lag_pct":      pos["entry_lag_pct"],
                "real_btc_in":  pos["btc_at_entry"],
                "real_btc_out": round(btc_price, 2) if btc_price else None,
                "hold_min":     round(held_s / 60, 1),
                "pnl":          pnl,
                "exit_reason":  reason,
            })
            to_close.append(tid)
            print(f"[BTC-LAG] CLOSE {pos['side']} @ {cur:.3f} "
                  f"(entry {pos['entry_price']:.3f}) pnl=${pnl:+.4f} [{reason}]")

    for tid in to_close:
        _positions.pop(tid, None)


# ─── Main scan (called every 10 seconds) ─────────────────────────────────────

async def scan() -> dict:
    global _balance, _scan_count, _last_scan_ts, _opportunities

    _scan_count += 1
    ts = datetime.now(timezone.utc).isoformat()
    _last_scan_ts = ts

    btc_price, markets = await asyncio.gather(
        _fetch_btc_price(),
        _fetch_btc_markets(),
        return_exceptions=True,
    )
    if isinstance(btc_price, Exception):
        btc_price = _btc_price
    if isinstance(markets, Exception):
        markets = _markets_cache

    lags_found = []

    if btc_price and markets:
        # Build token → market index for fast exit lookups
        idx: Dict[str, dict] = {}
        for m in markets:
            idx[m["yes_token"]] = m
            if m.get("no_token"):
                idx[m["no_token"]] = m

        # Check exits first
        _check_exits(btc_price, idx, ts)

        # Scan for new lags
        for market in markets:
            if market["liquidity"] < 50:
                continue
            signal = _compute_lag(btc_price, market)
            if not signal:
                continue

            lags_found.append({
                "question":  market["question"][:80],
                "strike":    market["strike"],
                "direction": market["direction"],
                "real_btc":  round(btc_price, 2),
                "yes_price": market["yes_price"],
                "no_price":  market["no_price"],
                "side":      signal["side"],
                "lag_pct":   round(signal["lag"] * 100, 2),
                "expected":  signal["expected"],
                "action":    signal["action"],
                "liquidity": market["liquidity"],
            })

            # Open position
            tid = signal["token"]
            if tid and tid not in _positions and _balance >= 20:
                size = min(_balance * 0.10, MAX_TRADE_USD)
                cost = size * signal["price"]
                _balance = round(_balance - cost, 4)
                _positions[tid] = {
                    "question":      market["question"][:80],
                    "side":          signal["side"],
                    "entry_price":   signal["price"],
                    "size":          size,
                    "entry_time":    time.time(),
                    "entry_lag_pct": round(signal["lag"] * 100, 2),
                    "btc_at_entry":  round(btc_price, 2),
                    "strike":        market["strike"],
                }
                print(f"[BTC-LAG] OPEN {signal['side']} "
                      f"'{market['question'][:45]}…' "
                      f"@ {signal['price']:.3f} size=${size:.2f} "
                      f"lag={signal['lag']*100:.1f}%")

    lags_found.sort(key=lambda x: x["lag_pct"], reverse=True)
    _opportunities = lags_found[:8]

    _scan_log.appendleft({
        "ts":              ts,
        "scan":            _scan_count,
        "btc_price":       round(btc_price, 2) if btc_price else None,
        "markets_checked": len(markets) if isinstance(markets, list) else 0,
        "lags_found":      len(lags_found),
        "positions":       len(_positions),
    })

    if btc_price:
        print(f"[BTC-LAG] #{_scan_count} BTC=${btc_price:,.2f} "
              f"mkts={len(markets) if isinstance(markets,list) else 0} "
              f"lags={len(lags_found)} pos={len(_positions)}")

    return get_status()


def get_status() -> dict:
    total    = len(_sim_trades)
    win_rate = round(_wins / total, 4) if total else 0.0

    open_pos = [
        {
            "question":     pos["question"][:65],
            "side":         pos["side"],
            "entry_price":  pos["entry_price"],
            "size":         pos["size"],
            "lag_pct":      pos["entry_lag_pct"],
            "btc_entry":    pos["btc_at_entry"],
            "strike":       pos["strike"],
            "held_min":     round((time.time() - pos["entry_time"]) / 60, 1),
        }
        for pos in _positions.values()
    ]

    return {
        "balance":         round(_balance, 2),
        "total_pnl":       round(_total_pnl, 4),
        "trades":          total,
        "wins":            _wins,
        "win_rate":        win_rate,
        "btc_price":       round(_btc_price, 2) if _btc_price else None,
        "open_positions":  open_pos,
        "opportunities":   _opportunities,
        "sim_trades":      list(_sim_trades)[:20],
        "scan_count":      _scan_count,
        "last_scan":       _last_scan_ts,
        "scan_log":        list(_scan_log)[:10],
        "markets_tracked": len(_markets_cache),
        "initial_balance": INITIAL_BALANCE,
    }
