"""
Market Maker Simulator — paper trading only.

Finds markets where YES + NO mid-price sum < (1 - TARGET_SPREAD_PCT).
"Buys" both sides and waits for resolution. When one side resolves to ~1.0,
the other collapses to ~0.0 — we collect the spread.

Example:
  YES @ 0.42  +  NO @ 0.52  =  sum 0.94
  Guaranteed resolution payout: $1.00 per unit
  Gross profit: $0.06 per unit (6% regardless of outcome)

Virtual balance: $500 (independent of other strategies)
Scan interval: every 30s (driven by main platform loop)
"""
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

INITIAL_BALANCE  = 500.0
MAX_TRADE_USD    = 50.0       # per market (split evenly YES+NO)
TARGET_SPREAD    = 0.04       # require at least 4% spread (sum ≤ 0.96)
MIN_LIQUIDITY    = 100.0      # both sides need some depth
EXIT_PRICE       = 0.97       # exit winning side when it hits this
STOP_LOSS_PRICE  = 0.05       # exit losing side if we're clearly wrong
CACHE_TTL        = 25         # seconds between Gamma API calls

_balance:       float = INITIAL_BALANCE
_total_pnl:     float = 0.0
_wins:          int   = 0
_total_trades:  int   = 0
_positions:     Dict[str, dict] = {}  # keyed by market condition_id
_sim_trades:    deque = deque(maxlen=200)
_scan_log:      deque = deque(maxlen=50)
_opportunities: List[dict] = []
_scan_count:    int   = 0
_last_scan_ts:  Optional[str] = None
_cache_ts:      float = 0.0
_markets_cache: List[dict] = []


# ─── Market fetch ──────────────────────────────────────────────────────────────

async def _fetch_markets() -> List[dict]:
    global _markets_cache, _cache_ts
    if time.time() - _cache_ts < CACHE_TTL:
        return _markets_cache

    import json as _json
    results = []
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            resp = await c.get(
                "https://gamma-api.polymarket.com/markets",
                params={
                    "active":    "true",
                    "closed":    "false",
                    "limit":     300,
                    "order":     "volume",
                    "ascending": "false",
                },
            )
            if resp.status_code != 200:
                return _markets_cache

            batch = resp.json()
            if not isinstance(batch, list):
                batch = batch.get("data", [])

            for raw in batch:
                q         = raw.get("question", "")
                outcomes  = _json.loads(raw.get("outcomes")      or "[]")
                prices    = _json.loads(raw.get("outcomePrices") or "[]")
                token_ids = _json.loads(raw.get("clobTokenIds")  or "[]")

                if len(outcomes) < 2 or len(prices) < 2:
                    continue

                yes_price = no_price = yes_token = no_token = None
                for i, o in enumerate(outcomes):
                    p   = float(prices[i]) if i < len(prices) else 0.0
                    tid = token_ids[i]     if i < len(token_ids) else ""
                    if o.upper() == "YES":
                        yes_price, yes_token = p, tid
                    elif o.upper() == "NO":
                        no_price, no_token = p, tid

                if yes_price is None or no_price is None:
                    continue

                results.append({
                    "condition_id": raw.get("conditionId", ""),
                    "question":     q,
                    "yes_price":    yes_price,
                    "no_price":     no_price,
                    "yes_token":    yes_token or "",
                    "no_token":     no_token  or "",
                    "liquidity":    float(raw.get("liquidity") or 0),
                    "end_date":     raw.get("endDate", ""),
                })

    except Exception as e:
        print(f"[MM] Fetch error: {e}")

    if results:
        _markets_cache = results
        _cache_ts = time.time()
    return _markets_cache


# ─── Position management ───────────────────────────────────────────────────────

def _check_exits(markets: List[dict], ts: str):
    global _balance, _total_pnl, _wins, _total_trades

    idx = {m["condition_id"]: m for m in markets}
    to_close = []

    for cid, pos in _positions.items():
        mkt = idx.get(cid)
        if mkt:
            yes_cur = mkt["yes_price"]
            no_cur  = mkt["no_price"]
        else:
            yes_cur = pos["yes_entry"]
            no_cur  = pos["no_entry"]

        # One side near resolution → close both
        if yes_cur >= EXIT_PRICE or yes_cur <= STOP_LOSS_PRICE:
            resolution_yes = yes_cur >= EXIT_PRICE
            winner_payout  = 1.0 if resolution_yes else yes_cur
            loser_payout   = no_cur if resolution_yes else 1.0

            pnl = round(
                pos["unit_size"] * (winner_payout + loser_payout)
                - pos["total_cost"],
                4,
            )
            reason = f"YES→{yes_cur:.3f}" if yes_cur >= EXIT_PRICE else f"YES→{yes_cur:.3f}(loss)"

            _balance   = round(_balance  + pnl, 4)
            _total_pnl = round(_total_pnl + pnl, 4)
            _total_trades += 1
            if pnl > 0:
                _wins += 1

            _sim_trades.appendleft({
                "ts":           ts,
                "question":     pos["question"][:70],
                "yes_entry":    pos["yes_entry"],
                "no_entry":     pos["no_entry"],
                "sum_entry":    round(pos["yes_entry"] + pos["no_entry"], 4),
                "yes_exit":     round(yes_cur, 4),
                "no_exit":      round(no_cur, 4),
                "spread_pct":   round((1.0 - pos["yes_entry"] - pos["no_entry"]) * 100, 2),
                "unit_size":    pos["unit_size"],
                "total_cost":   pos["total_cost"],
                "pnl":          pnl,
                "exit_reason":  reason,
            })
            to_close.append(cid)
            print(f"[MM] CLOSE '{pos['question'][:40]}' pnl=${pnl:+.4f} [{reason}]")

    for cid in to_close:
        _positions.pop(cid, None)


# ─── Main scan ─────────────────────────────────────────────────────────────────

async def scan(markets_from_agent: Optional[List[dict]] = None) -> dict:
    global _balance, _scan_count, _last_scan_ts, _opportunities

    _scan_count += 1
    ts = datetime.now(timezone.utc).isoformat()
    _last_scan_ts = ts

    markets = await _fetch_markets()

    # Check exits on known positions
    if markets:
        _check_exits(markets, ts)

    opps_found = []

    for mkt in markets:
        cid       = mkt["condition_id"]
        yes_price = mkt["yes_price"]
        no_price  = mkt["no_price"]
        total     = yes_price + no_price
        spread    = 1.0 - total
        liquidity = mkt["liquidity"]

        # Must have enough spread and liquidity
        if spread < TARGET_SPREAD:
            continue
        if liquidity < MIN_LIQUIDITY:
            continue
        # Don't double-enter
        if cid in _positions:
            continue

        opps_found.append({
            "question":   mkt["question"][:80],
            "yes_price":  yes_price,
            "no_price":   no_price,
            "sum":        round(total, 4),
            "spread_pct": round(spread * 100, 2),
            "liquidity":  liquidity,
        })

        # Open position if balance allows
        if _balance >= MAX_TRADE_USD:
            unit_size  = MAX_TRADE_USD / 2  # split evenly
            total_cost = unit_size * yes_price + unit_size * no_price

            _balance = round(_balance - total_cost, 4)
            _positions[cid] = {
                "question":   mkt["question"][:80],
                "yes_entry":  yes_price,
                "no_entry":   no_price,
                "yes_token":  mkt["yes_token"],
                "no_token":   mkt["no_token"],
                "unit_size":  unit_size,
                "total_cost": round(total_cost, 4),
                "entry_time": time.time(),
            }
            print(
                f"[MM] OPEN '{mkt['question'][:45]}' "
                f"YES@{yes_price:.3f}+NO@{no_price:.3f}=sum{total:.3f} "
                f"spread={spread*100:.2f}%"
            )

    opps_found.sort(key=lambda x: x["spread_pct"], reverse=True)
    _opportunities = opps_found[:10]

    _scan_log.appendleft({
        "ts":           ts,
        "scan":         _scan_count,
        "markets":      len(markets),
        "opps":         len(opps_found),
        "positions":    len(_positions),
        "best_spread":  round(opps_found[0]["spread_pct"], 2) if opps_found else 0,
    })

    print(
        f"[MM] #{_scan_count} mkts={len(markets)} "
        f"opps={len(opps_found)} pos={len(_positions)} "
        f"best_spread={opps_found[0]['spread_pct']:.2f}%" if opps_found
        else f"[MM] #{_scan_count} mkts={len(markets)} no spread opportunities"
    )

    return get_status()


def get_status() -> dict:
    total    = len(_sim_trades)
    win_rate = round(_wins / total, 4) if total else 0.0

    open_pos = [
        {
            "question":   pos["question"][:65],
            "yes_entry":  pos["yes_entry"],
            "no_entry":   pos["no_entry"],
            "spread_pct": round((1.0 - pos["yes_entry"] - pos["no_entry"]) * 100, 2),
            "unit_size":  pos["unit_size"],
            "total_cost": pos["total_cost"],
            "held_min":   round((time.time() - pos["entry_time"]) / 60, 1),
        }
        for pos in _positions.values()
    ]

    return {
        "balance":         round(_balance, 2),
        "total_pnl":       round(_total_pnl, 4),
        "trades":          total,
        "wins":            _wins,
        "win_rate":        win_rate,
        "open_positions":  open_pos,
        "opportunities":   _opportunities,
        "sim_trades":      list(_sim_trades)[:20],
        "scan_count":      _scan_count,
        "last_scan":       _last_scan_ts,
        "scan_log":        list(_scan_log)[:10],
        "markets_checked": len(_markets_cache),
        "initial_balance": INITIAL_BALANCE,
        "target_spread":   TARGET_SPREAD,
    }
