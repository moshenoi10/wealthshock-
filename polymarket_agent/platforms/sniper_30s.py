"""
30-60s Resolution Sniper — paper trading only.

Monitors BTC binary markets closing within 2 minutes.
Uses 30-second Binance BTC momentum to pick direction.

If momentum > +0.5% (bullish)  → buy YES when YES ≥ 0.92
If momentum < -0.5% (bearish)  → buy NO  when NO  ≥ 0.92

Resolution: re-fetches the closed market from Gamma API; if YES
resolves to 1.0 we win, else 0.0. Falls back to entry-price heuristic
after 5 minutes if the market hasn't confirmed resolution.

Virtual balance: $500 | Max $50/position | Max 3 concurrent
"""
import time
import json as _json
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import httpx

INITIAL_BALANCE   = 500.0
MAX_TRADE_USD     = 50.0
MAX_POSITIONS     = 3
MOMENTUM_THRESH   = 0.005    # 0.5%
MIN_ENTRY_PRICE   = 0.92     # at least 92% confidence on target side
CLOSE_WINDOW_S    = 120      # only target markets closing ≤ 2 min away
FORCE_CLOSE_EXTRA = 300      # force-close 5 min after scheduled end
CACHE_TTL         = 8        # seconds between market list refreshes

_balance:      float = INITIAL_BALANCE
_total_pnl:    float = 0.0
_wins:         int   = 0
_total_trades: int   = 0
_positions:    Dict[str, dict] = {}
_sim_trades:   deque = deque(maxlen=200)
_scan_log:     deque = deque(maxlen=50)
_scan_count:   int   = 0
_last_scan_ts: Optional[str] = None

_btc_history:  deque = deque(maxlen=180)   # (ts, price) – 3 min of 1s ticks
_btc_price:    float = 0.0
_cache_ts:     float = 0.0
_mkt_cache:    List[dict] = []


# ─── Binance spot price ────────────────────────────────────────────────────────

async def _fetch_btc() -> float:
    global _btc_price
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"},
            )
            p = float(r.json()["price"])
            _btc_price = p
            return p
    except Exception:
        return _btc_price


# ─── Polymarket near-resolution market fetch ───────────────────────────────────

async def _fetch_sniper_markets() -> List[dict]:
    global _cache_ts, _mkt_cache
    if time.time() - _cache_ts < CACHE_TTL:
        return _mkt_cache

    now    = datetime.now(timezone.utc)
    cutoff = now + timedelta(seconds=CLOSE_WINDOW_S)
    results: List[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(
                "https://gamma-api.polymarket.com/markets",
                params={
                    "active": "true", "closed": "false",
                    "limit": 200, "order": "endDate", "ascending": "true",
                },
            )
            if r.status_code != 200:
                return _mkt_cache
            batch = r.json()
            if not isinstance(batch, list):
                batch = batch.get("data", [])

            for raw in batch:
                end_str = (raw.get("endDate") or "").replace("Z", "+00:00")
                if not end_str:
                    continue
                try:
                    end_dt = datetime.fromisoformat(end_str)
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                if end_dt <= now or end_dt > cutoff:
                    continue
                if "BTC" not in raw.get("question", "").upper():
                    continue

                outcomes  = _json.loads(raw.get("outcomes")      or "[]")
                prices    = _json.loads(raw.get("outcomePrices") or "[]")
                yes_p = no_p = None
                for i, o in enumerate(outcomes):
                    p = float(prices[i]) if i < len(prices) else 0.0
                    if o.upper() == "YES": yes_p = p
                    elif o.upper() == "NO":  no_p  = p
                if yes_p is None or no_p is None:
                    continue

                results.append({
                    "condition_id": raw.get("conditionId", ""),
                    "question":     raw.get("question", ""),
                    "yes_price":    yes_p,
                    "no_price":     no_p,
                    "end_dt":       end_dt,
                    "secs_left":    round((end_dt - now).total_seconds()),
                    "liquidity":    float(raw.get("liquidity") or 0),
                })
    except Exception as e:
        print(f"[SNIPER] Fetch error: {e}")

    if results:
        _mkt_cache = results
        _cache_ts  = time.time()
    return _mkt_cache


# ─── Resolution query ──────────────────────────────────────────────────────────

async def _query_resolution(condition_id: str) -> Optional[bool]:
    """Return True if YES resolved to 1.0, False if NO won, None if unknown."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(
                f"https://gamma-api.polymarket.com/markets/{condition_id}"
            )
            if r.status_code != 200:
                return None
            d = r.json()
            if not d.get("closed"):
                return None
            outcomes = _json.loads(d.get("outcomes")      or "[]")
            prices   = _json.loads(d.get("outcomePrices") or "[]")
            for i, o in enumerate(outcomes):
                if o.upper() == "YES" and i < len(prices):
                    return float(prices[i]) >= 0.99
    except Exception:
        pass
    return None


# ─── Momentum calc ─────────────────────────────────────────────────────────────

def _get_momentum() -> Optional[float]:
    now = time.time()
    hist = [(ts, p) for ts, p in _btc_history if now - ts <= 35]
    if len(hist) < 10:
        return None
    oldest = min(hist, key=lambda x: x[0])
    newest = max(hist, key=lambda x: x[0])
    if oldest[1] == 0:
        return None
    return (newest[1] - oldest[1]) / oldest[1]


# ─── Position resolution ───────────────────────────────────────────────────────

async def _resolve_positions(ts: str):
    global _balance, _total_pnl, _wins, _total_trades
    now = datetime.now(timezone.utc)
    to_close: List[str] = []

    for cid, pos in list(_positions.items()):
        past_close = now >= pos["end_dt"]
        force_time = pos["entry_time"] + FORCE_CLOSE_EXTRA + \
                     (pos["end_dt"] - datetime.now(timezone.utc)).total_seconds()

        if not past_close:
            continue

        # Try to get actual resolution from Polymarket
        resolved_yes = await _query_resolution(cid)

        won = False
        if resolved_yes is not None:
            won = (pos["side"] == "YES" and resolved_yes) or \
                  (pos["side"] == "NO"  and not resolved_yes)
            reason = "resolved_api"
        else:
            # Heuristic: if entry price ≥ 0.96 on our side, market was very confident
            won = pos["entry_price"] >= 0.96
            reason = "heuristic"

        # If we're past the force-close window and resolution is still unknown → assume loss
        secs_past = (now - pos["end_dt"]).total_seconds()
        if resolved_yes is None and secs_past > FORCE_CLOSE_EXTRA:
            won = False
            reason = "force_close_timeout"

        if resolved_yes is None and secs_past < 30:
            # Give the API 30s grace period after close before force-resolving
            continue

        payout = 1.0 if won else 0.0
        pnl    = round(payout - pos["cost"], 4)
        _balance    = round(_balance + payout, 4)
        _total_pnl  = round(_total_pnl + pnl, 4)
        _total_trades += 1
        if won:
            _wins += 1

        _sim_trades.appendleft({
            "ts":           ts,
            "question":     pos["question"][:70],
            "side":         pos["side"],
            "entry_price":  pos["entry_price"],
            "momentum_pct": round(pos["momentum"] * 100, 3),
            "cost":         pos["cost"],
            "payout":       payout,
            "pnl":          pnl,
            "won":          won,
            "reason":       reason,
        })
        to_close.append(cid)
        print(
            f"[SNIPER] CLOSE {pos['side']} '{pos['question'][:40]}' "
            f"won={won} pnl=${pnl:+.4f} [{reason}]"
        )

    for cid in to_close:
        _positions.pop(cid, None)


# ─── Main scan ─────────────────────────────────────────────────────────────────

async def scan() -> dict:
    global _balance, _scan_count, _last_scan_ts

    _scan_count += 1
    ts = datetime.now(timezone.utc).isoformat()
    _last_scan_ts = ts

    # Update BTC price history
    new_price = await _fetch_btc()
    if new_price > 0:
        _btc_history.append((time.time(), new_price))

    momentum = _get_momentum()

    # Resolve finished positions first
    await _resolve_positions(ts)

    markets = await _fetch_sniper_markets()
    opps: List[dict] = []

    for mkt in markets:
        cid = mkt["condition_id"]
        if cid in _positions:
            continue
        if len(_positions) >= MAX_POSITIONS:
            opps.append({**mkt, "action": "SKIP — max positions reached",
                         "momentum_pct": round((momentum or 0) * 100, 3)})
            continue

        side = entry_price = None
        skip_reason = ""

        if momentum is None:
            skip_reason = "no_momentum_data (<10 BTC ticks)"
        elif momentum > MOMENTUM_THRESH and mkt["yes_price"] >= MIN_ENTRY_PRICE:
            side, entry_price = "YES", mkt["yes_price"]
        elif momentum < -MOMENTUM_THRESH and mkt["no_price"] >= MIN_ENTRY_PRICE:
            side, entry_price = "NO", mkt["no_price"]
        elif abs(momentum) <= MOMENTUM_THRESH:
            skip_reason = f"momentum weak ({momentum*100:.3f}% < ±{MOMENTUM_THRESH*100:.1f}%)"
        else:
            skip_reason = f"price too low ({mkt['yes_price']:.3f}/{mkt['no_price']:.3f} < {MIN_ENTRY_PRICE})"

        opps.append({
            "question":      mkt["question"][:70],
            "secs_left":     mkt["secs_left"],
            "yes_price":     mkt["yes_price"],
            "no_price":      mkt["no_price"],
            "liquidity":     mkt["liquidity"],
            "momentum_pct":  round((momentum or 0) * 100, 3),
            "action":        f"BUY {side} @{entry_price:.3f}" if side
                             else f"SKIP — {skip_reason}",
        })

        if side and entry_price and _balance >= MAX_TRADE_USD:
            cost = round(min(MAX_TRADE_USD, _balance) * entry_price, 4)
            _balance = round(_balance - cost, 4)
            _positions[cid] = {
                "question":    mkt["question"][:70],
                "side":        side,
                "entry_price": entry_price,
                "momentum":    momentum,
                "cost":        cost,
                "entry_time":  time.time(),
                "end_dt":      mkt["end_dt"],
            }
            print(
                f"[SNIPER] OPEN {side} @{entry_price:.3f} "
                f"'{mkt['question'][:45]}' momentum={momentum*100:.3f}%"
            )

    _scan_log.appendleft({
        "ts":               ts,
        "scan":             _scan_count,
        "btc":              round(_btc_price, 2),
        "momentum_pct":     round((momentum or 0) * 100, 3),
        "markets_in_window": len(markets),
        "positions":        len(_positions),
        "opps":             len(opps),
    })

    return get_status()


def get_status() -> dict:
    mom = _get_momentum()
    tot = _total_trades
    open_pos = [
        {
            "question":     pos["question"][:60],
            "side":         pos["side"],
            "entry_price":  pos["entry_price"],
            "momentum_pct": round(pos["momentum"] * 100, 3),
            "cost":         pos["cost"],
            "held_s":       round(time.time() - pos["entry_time"]),
            "secs_to_close": max(0, round(
                (pos["end_dt"] - datetime.now(timezone.utc)).total_seconds()
            )),
        }
        for pos in _positions.values()
    ]
    return {
        "balance":           round(_balance, 2),
        "total_pnl":         round(_total_pnl, 4),
        "trades":            tot,
        "wins":              _wins,
        "win_rate":          round(_wins / tot, 4) if tot else 0.0,
        "open_positions":    open_pos,
        "sim_trades":        list(_sim_trades)[:20],
        "scan_log":          list(_scan_log)[:10],
        "btc_price":         round(_btc_price, 2),
        "momentum_pct":      round((mom or 0) * 100, 3),
        "scan_count":        _scan_count,
        "last_scan":         _last_scan_ts,
        "initial_balance":   INITIAL_BALANCE,
        "momentum_thresh":   MOMENTUM_THRESH,
        "min_entry_price":   MIN_ENTRY_PRICE,
        "close_window_s":    CLOSE_WINDOW_S,
    }
