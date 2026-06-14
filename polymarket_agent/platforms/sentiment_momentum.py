"""
Sentiment Momentum — paper trading only.

Two free signal sources (no Twitter API required):
  1. Binance BTCUSDT order book depth (bid vs ask volume ratio)
     › bid/ask > 1.25 = buying pressure (bullish)
     › bid/ask < 0.80 = selling pressure (bearish)

  2. Alternative.me Crypto Fear & Greed Index (free, no key)
     › Score > 60 = Greed (bullish)
     › Score < 40 = Fear (bearish)

Entry conditions — require BOTH signals aligned:
  Bullish: depth_ratio > 1.25 AND fear_greed > 60
    → buy YES on BTC markets priced 0.35–0.70 (underpriced given bullish sentiment)
  Bearish: depth_ratio < 0.80 AND fear_greed < 40
    → buy NO  on BTC markets priced 0.35–0.70

Exit: 15-minute timeout or if Polymarket price hits 0.80+ (take profit) / 0.10 (cut loss).

Virtual balance: $500 | Max $50/trade | Max 4 concurrent positions

Note: Integrating X/Twitter sentiment would upgrade conviction further and
is the intended upgrade path. Requires Elevated API access ($100/mo Basic tier).
"""
import time
import json as _json
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import httpx

INITIAL_BALANCE   = 500.0
MAX_TRADE_USD     = 50.0
MAX_POSITIONS     = 4
DEPTH_BULL        = 1.25   # bid/ask ratio above this = bullish
DEPTH_BEAR        = 0.80   # bid/ask ratio below this = bearish
FNG_BULL          = 60     # fear & greed above = greed (bullish)
FNG_BEAR          = 40     # fear & greed below = fear (bearish)
MIN_PRICE         = 0.35   # only trade markets priced between these
MAX_PRICE         = 0.70
TAKE_PROFIT_PRICE = 0.80
CUT_LOSS_PRICE    = 0.10
MAX_HOLD_S        = 900    # 15 minutes max hold
FNG_TTL           = 300    # fear & greed changes slowly, cache 5 min
DEPTH_TTL         = 10     # order book refreshes every 10s
MKT_TTL           = 30

_balance:      float = INITIAL_BALANCE
_total_pnl:    float = 0.0
_wins:         int   = 0
_total_trades: int   = 0
_positions:    Dict[str, dict] = {}
_sim_trades:   deque = deque(maxlen=200)
_scan_log:     deque = deque(maxlen=50)
_scan_count:   int   = 0
_last_scan_ts: Optional[str] = None

_fng_score:    int   = 50     # default neutral
_fng_label:    str   = "Neutral"
_fng_ts:       float = 0.0

_depth_ratio:  float = 1.0
_depth_ts:     float = 0.0

_btc_price:    float = 0.0

_mkt_cache:    List[dict] = []
_mkt_ts:       float = 0.0


# ─── Data fetchers ─────────────────────────────────────────────────────────────

async def _fetch_fng() -> Tuple[int, str]:
    """Alternative.me Fear & Greed Index — free, no API key."""
    global _fng_score, _fng_label, _fng_ts
    if time.time() - _fng_ts < FNG_TTL:
        return _fng_score, _fng_label
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get("https://api.alternative.me/fng/?limit=1")
            d = r.json()["data"][0]
            _fng_score = int(d["value"])
            _fng_label = d["value_classification"]
            _fng_ts    = time.time()
    except Exception as e:
        print(f"[SENTIMENT] FNG fetch error: {e}")
    return _fng_score, _fng_label


async def _fetch_depth_ratio() -> float:
    """Binance BTCUSDT order book depth: bid_vol / ask_vol (top 20 levels)."""
    global _depth_ratio, _depth_ts, _btc_price
    if time.time() - _depth_ts < DEPTH_TTL:
        return _depth_ratio
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get(
                "https://api.binance.com/api/v3/depth",
                params={"symbol": "BTCUSDT", "limit": 20},
            )
            d  = r.json()
            bd = sum(float(b[1]) for b in d["bids"])
            ad = sum(float(a[1]) for a in d["asks"])
            if ad > 0:
                _depth_ratio = round(bd / ad, 4)
            # also grab mid price
            if d["bids"] and d["asks"]:
                best_bid = float(d["bids"][0][0])
                best_ask = float(d["asks"][0][0])
                _btc_price = round((best_bid + best_ask) / 2, 2)
            _depth_ts = time.time()
    except Exception as e:
        print(f"[SENTIMENT] Depth fetch error: {e}")
    return _depth_ratio


async def _fetch_btc_markets() -> List[dict]:
    global _mkt_cache, _mkt_ts
    if time.time() - _mkt_ts < MKT_TTL:
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
                if float(raw.get("liquidity") or 0) < 200:
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
                results.append({
                    "condition_id": raw.get("conditionId", ""),
                    "question":     raw.get("question", ""),
                    "yes_price":    yes_p,
                    "no_price":     no_p,
                    "liquidity":    float(raw.get("liquidity") or 0),
                })
    except Exception as e:
        print(f"[SENTIMENT] Markets fetch error: {e}")
    if results:
        _mkt_cache = results
        _mkt_ts    = time.time()
    return _mkt_cache


# ─── Signal logic ──────────────────────────────────────────────────────────────

def _get_signal(fng: int, depth: float) -> Optional[str]:
    """Return 'bullish', 'bearish', or None (conflicting / weak)."""
    bull = depth > DEPTH_BULL and fng > FNG_BULL
    bear = depth < DEPTH_BEAR and fng < FNG_BEAR
    if bull and not bear:
        return "bullish"
    if bear and not bull:
        return "bearish"
    return None


# ─── Position management ───────────────────────────────────────────────────────

def _check_exits(markets: List[dict], ts: str):
    global _balance, _total_pnl, _wins, _total_trades
    idx = {m["condition_id"]: m for m in markets}
    to_close: List[str] = []

    for cid, pos in _positions.items():
        mkt = idx.get(cid)
        cur_yes = mkt["yes_price"] if mkt else pos["entry_yes"]
        held    = time.time() - pos["entry_time"]

        side       = pos["side"]
        cur_price  = cur_yes if side == "YES" else (1.0 - cur_yes)
        take_profit = cur_price >= TAKE_PROFIT_PRICE
        cut_loss    = cur_price <= CUT_LOSS_PRICE
        timeout     = held >= MAX_HOLD_S

        if not (take_profit or cut_loss or timeout):
            continue

        pnl    = round((cur_price - pos["entry_price"]) * pos["size"], 4)
        _balance    = round(_balance + pos["size"] * cur_price, 4)
        _total_pnl  = round(_total_pnl + pnl, 4)
        _total_trades += 1
        if pnl > 0:
            _wins += 1

        reason = "take_profit" if take_profit else "cut_loss" if cut_loss else "timeout"
        _sim_trades.appendleft({
            "ts":           ts,
            "question":     pos["question"][:70],
            "side":         side,
            "entry_price":  pos["entry_price"],
            "exit_price":   round(cur_price, 4),
            "signal":       pos["signal"],
            "fng_at_entry": pos["fng"],
            "depth_at_entry": pos["depth"],
            "size":         pos["size"],
            "held_min":     round(held / 60, 1),
            "pnl":          pnl,
            "reason":       reason,
        })
        to_close.append(cid)
        print(
            f"[SENTIMENT] CLOSE {side} '{pos['question'][:40]}' "
            f"pnl=${pnl:+.4f} [{reason}]"
        )

    for cid in to_close:
        _positions.pop(cid, None)


# ─── Main scan ─────────────────────────────────────────────────────────────────

async def scan() -> dict:
    global _balance, _scan_count, _last_scan_ts

    _scan_count += 1
    ts = datetime.now(timezone.utc).isoformat()
    _last_scan_ts = ts

    fng_score, fng_label = await _fetch_fng()
    depth_ratio          = await _fetch_depth_ratio()
    markets              = await _fetch_btc_markets()
    signal               = _get_signal(fng_score, depth_ratio)

    _check_exits(markets, ts)

    entries: List[dict] = []
    for mkt in markets:
        cid = mkt["condition_id"]
        if cid in _positions:
            continue
        if len(_positions) >= MAX_POSITIONS:
            break
        if _balance < MAX_TRADE_USD:
            break

        yes_p = mkt["yes_price"]

        if signal == "bullish" and MIN_PRICE <= yes_p <= MAX_PRICE:
            side, entry_price = "YES", yes_p
        elif signal == "bearish" and MIN_PRICE <= (1.0 - yes_p) <= MAX_PRICE:
            side, entry_price = "NO", 1.0 - yes_p
        else:
            continue

        size = round(min(MAX_TRADE_USD, _balance) * entry_price, 4)
        _balance = round(_balance - size, 4)
        _positions[cid] = {
            "question":     mkt["question"][:70],
            "side":         side,
            "entry_price":  round(entry_price, 4),
            "entry_yes":    yes_p,
            "signal":       signal,
            "fng":          fng_score,
            "depth":        round(depth_ratio, 4),
            "size":         size,
            "entry_time":   time.time(),
        }
        entries.append({
            "question":    mkt["question"][:60],
            "side":        side,
            "entry_price": round(entry_price, 4),
        })
        print(
            f"[SENTIMENT] OPEN {side} @{entry_price:.3f} "
            f"'{mkt['question'][:40]}' "
            f"signal={signal} fng={fng_score} depth={depth_ratio:.3f}"
        )

    _scan_log.appendleft({
        "ts":          ts,
        "scan":        _scan_count,
        "btc_price":   round(_btc_price, 2),
        "fng_score":   fng_score,
        "fng_label":   fng_label,
        "depth_ratio": round(depth_ratio, 4),
        "signal":      signal or "none",
        "positions":   len(_positions),
        "new_entries": len(entries),
    })

    print(
        f"[SENTIMENT] #{_scan_count} fng={fng_score}({fng_label}) "
        f"depth={depth_ratio:.3f} signal={signal or 'none'} pos={len(_positions)}"
    )
    return get_status()


def get_status() -> dict:
    tot = _total_trades
    open_pos = [
        {
            "question":    pos["question"][:60],
            "side":        pos["side"],
            "entry_price": pos["entry_price"],
            "signal":      pos["signal"],
            "fng":         pos["fng"],
            "depth":       pos["depth"],
            "size":        pos["size"],
            "held_min":    round((time.time() - pos["entry_time"]) / 60, 1),
        }
        for pos in _positions.values()
    ]
    return {
        "balance":       round(_balance, 2),
        "total_pnl":     round(_total_pnl, 4),
        "trades":        tot,
        "wins":          _wins,
        "win_rate":      round(_wins / tot, 4) if tot else 0.0,
        "open_positions": open_pos,
        "sim_trades":    list(_sim_trades)[:20],
        "scan_log":      list(_scan_log)[:10],
        "scan_count":    _scan_count,
        "last_scan":     _last_scan_ts,
        "btc_price":     round(_btc_price, 2),
        "fng_score":     _fng_score,
        "fng_label":     _fng_label,
        "depth_ratio":   round(_depth_ratio, 4),
        "current_signal": _get_signal(_fng_score, _depth_ratio) or "none",
        "initial_balance": INITIAL_BALANCE,
    }
