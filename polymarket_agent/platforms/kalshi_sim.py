"""
Kalshi simulation — paper trading only, no auth needed.
Fetches public Kalshi market data, finds cross-platform arb vs Polymarket.
Simulates a trade whenever the same event shows price divergence > threshold.

Auto-threshold: if 0 spreads found for 3 consecutive scans, threshold drops by 0.5%.
Floor at 0.5%. If still 0 after floor reached, logs "prices identical" verdict.
"""
import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import List, Optional

import httpx

KALSHI_URLS = [
    "https://api.kalshi.com/trade-api/v2",
    "https://api.elections.kalshi.com/trade-api/v2",
    "https://trading-api.kalshi.com/trade-api/v2",
]
INITIAL_BALANCE  = 100.0
_MIN_EDGE_START  = 0.025   # 2.5% — starting threshold
_MIN_EDGE_FLOOR  = 0.005   # 0.5% — lowest we'll go
_MIN_EDGE_STEP   = 0.005   # drop 0.5% each time
_ZERO_RUNS_LIMIT = 3       # consecutive zero-spread scans before lowering
MAX_POSITION_PCT = 0.10

_balance:  float = INITIAL_BALANCE
_total_pnl: float = 0.0
_wins:     int   = 0
_sim_trades: deque = deque(maxlen=200)
_last_opportunities: List[dict] = []
_scanned:  int   = 0
_kalshi_markets_cache: List[dict] = []
_last_scan_ts: Optional[str] = None
_last_scan_detail: dict = {}
_SCAN_LOG: deque = deque(maxlen=100)

# Auto-threshold state
_current_min_edge: float = _MIN_EDGE_START
_consecutive_zero_runs: int = 0
_threshold_verdict: str = ""   # set when we've bottomed out


async def _fetch_kalshi_markets() -> List[dict]:
    for base_url in KALSHI_URLS:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(
                    f"{base_url}/markets",
                    params={"status": "open", "limit": 200},
                    headers={"accept": "application/json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    markets = data.get("markets", [])
                    if markets:
                        print(f"[KALSHI] OK — {len(markets)} markets from {base_url}")
                        return markets
                else:
                    print(f"[KALSHI] {base_url} → HTTP {resp.status_code}")
        except Exception as exc:
            print(f"[KALSHI] {base_url} error: {type(exc).__name__}: {exc}")
    print("[KALSHI] All endpoints failed — returning empty")
    return []


def _extract_yes_price(market: dict) -> Optional[float]:
    """Return mid YES price in [0,1]. Kalshi quotes in cents."""
    yes_bid = market.get("yes_bid")
    yes_ask = market.get("yes_ask")
    last    = market.get("last_price")
    result  = market.get("result")

    if result and result not in ("", "n/a", None):
        return None

    if yes_bid is not None and yes_ask is not None:
        try:
            mid = (float(yes_bid) + float(yes_ask)) / 2 / 100
            return mid if 0 < mid < 1 else None
        except Exception:
            pass
    if last is not None:
        try:
            p = float(last) / 100
            return p if 0 < p < 1 else None
        except Exception:
            pass
    return None


_STOPWORDS = {"will", "the", "a", "an", "to", "in", "of", "by", "be", "at", "or",
              "is", "it", "on", "for", "with", "this", "that", "than", "their"}


def _match_polymarket(kalshi_title: str, poly_markets: List[dict]) -> Optional[dict]:
    kl       = kalshi_title.lower()
    keywords = [w for w in kl.split() if len(w) > 3 and w not in _STOPWORDS]
    if not keywords:
        return None
    best_match, best_score = None, 0
    for pm in poly_markets:
        pl    = pm.get("question", "").lower()
        score = sum(1 for w in keywords if w in pl)
        if score > best_score:
            best_score, best_match = score, pm
    return best_match if best_score >= 1 else None


async def scan(poly_markets: List[dict]) -> dict:
    global _balance, _total_pnl, _wins, _scanned
    global _kalshi_markets_cache, _last_scan_ts, _last_scan_detail
    global _current_min_edge, _consecutive_zero_runs, _threshold_verdict

    ts = datetime.now(timezone.utc).isoformat()
    _last_scan_ts = ts

    kalshi_markets = await _fetch_kalshi_markets()
    _scanned += len(kalshi_markets)
    if kalshi_markets:
        _kalshi_markets_cache = kalshi_markets

    # --- Per-scan detail dict ---
    detail: dict = {
        "ts":                      ts,
        "kalshi_markets_fetched":  len(kalshi_markets),
        "poly_markets_available":  len(poly_markets),
        "with_price":              0,
        "matched_to_poly":         0,
        "spread_above_min":        0,
        "trades_this_scan":        0,
        "current_threshold_pct":   round(_current_min_edge * 100, 2),
        "top5_spreads":            [],   # [(title, spread_pct)]
        "reasons_no_trade":        [],
        "verdict":                 "",
    }

    if not kalshi_markets:
        detail["verdict"] = "API_FAILURE — all 3 Kalshi endpoints returned empty"
        detail["reasons_no_trade"].append(detail["verdict"])
        _finalize_detail(detail)
        return get_status()

    if not poly_markets:
        detail["verdict"] = "NO_POLY_MARKETS — Polymarket fetch returned 0 markets"
        detail["reasons_no_trade"].append(detail["verdict"])
        _finalize_detail(detail)
        return get_status()

    # --- Match and compare ---
    opportunities = []
    prices_found  = 0
    matched       = 0
    all_spreads: List[tuple] = []  # (spread, title)

    for km in kalshi_markets:
        title   = km.get("title", "") or km.get("subtitle", "") or km.get("question", "") or ""
        k_price = _extract_yes_price(km)

        if k_price is None:
            continue
        prices_found += 1

        pm = _match_polymarket(title, poly_markets)
        if not pm:
            continue
        matched += 1

        yes_token = next(
            (t for t in pm.get("tokens", []) if "YES" in (t.get("outcome") or "").upper()),
            None,
        )
        if not yes_token:
            continue

        p_price = float(yes_token.get("price") or 0)
        if not (0 < p_price < 1):
            continue

        edge = abs(p_price - k_price)
        all_spreads.append((edge, title[:50]))

        if edge < _current_min_edge:
            continue

        detail["spread_above_min"] += 1
        buy_plat  = "Kalshi" if k_price < p_price else "Polymarket"
        sell_plat = "Polymarket" if k_price < p_price else "Kalshi"
        buy_p     = k_price if k_price < p_price else p_price
        sell_p    = p_price if k_price < p_price else k_price

        opportunities.append({
            "question":      title[:80],
            "poly_question": pm.get("question", "")[:60],
            "poly_price":    round(p_price,  4),
            "kalshi_price":  round(k_price,  4),
            "edge":          round(edge,     4),
            "buy_platform":  buy_plat,
            "sell_platform": sell_plat,
            "buy_price":     round(buy_p,    4),
            "sell_price":    round(sell_p,   4),
        })

        size    = min(_balance * MAX_POSITION_PCT, 5.0)
        if size >= 0.5:
            sim_pnl      = round(size * edge * 0.75, 4)
            _balance     = round(_balance   + sim_pnl, 4)
            _total_pnl   = round(_total_pnl + sim_pnl, 4)
            if sim_pnl > 0:
                _wins += 1
            detail["trades_this_scan"] += 1
            _sim_trades.appendleft({
                "ts":       ts,
                "question": title[:60],
                "side":     f"קנה {buy_plat} / מכור {sell_plat}",
                "price":    round(buy_p,  4),
                "size":     round(size,   2),
                "edge_pct": round(edge * 100, 2),
                "pnl":      sim_pnl,
            })

    detail["with_price"]      = prices_found
    detail["matched_to_poly"] = matched

    # Top-5 spreads found (regardless of threshold)
    all_spreads.sort(reverse=True)
    detail["top5_spreads"] = [
        {"title": t, "spread_pct": round(s * 100, 3)}
        for s, t in all_spreads[:5]
    ]

    # --- Auto-threshold logic ---
    if detail["spread_above_min"] == 0:
        _consecutive_zero_runs += 1
        if _threshold_verdict:
            detail["verdict"] = _threshold_verdict
        elif _consecutive_zero_runs >= _ZERO_RUNS_LIMIT:
            if _current_min_edge > _MIN_EDGE_FLOOR:
                _current_min_edge = max(_MIN_EDGE_FLOOR, _current_min_edge - _MIN_EDGE_STEP)
                _consecutive_zero_runs = 0
                detail["verdict"] = (
                    f"AUTO-LOWERED threshold to {_current_min_edge*100:.1f}% "
                    f"(was {(_current_min_edge+_MIN_EDGE_STEP)*100:.1f}%)"
                )
            else:
                # Hit the floor — issue verdict on whether prices are identical
                max_spread_seen = all_spreads[0][0] if all_spreads else 0.0
                if max_spread_seen < 0.003:
                    _threshold_verdict = (
                        f"VERDICT: Kalshi & Polymarket prices are IDENTICAL (<0.3% max spread). "
                        f"No arb possible. Best spread seen: {max_spread_seen*100:.3f}%"
                    )
                else:
                    _threshold_verdict = (
                        f"VERDICT: Spreads exist but only {max_spread_seen*100:.3f}% max. "
                        f"Below execution cost. No profitable arb."
                    )
                detail["verdict"] = _threshold_verdict
        else:
            detail["verdict"] = (
                f"0 spreads ≥{_current_min_edge*100:.1f}% "
                f"({_ZERO_RUNS_LIMIT - _consecutive_zero_runs} more zero scans before auto-lower)"
            )
    else:
        _consecutive_zero_runs = 0
        _threshold_verdict = ""
        detail["verdict"] = f"Found {detail['spread_above_min']} spread(s) ≥{_current_min_edge*100:.1f}%"

    detail["current_threshold_pct"] = round(_current_min_edge * 100, 2)

    _finalize_detail(detail)
    _last_opportunities.clear()
    _last_opportunities.extend(
        sorted(opportunities, key=lambda x: x["edge"], reverse=True)[:5]
    )

    print(
        f"[KALSHI] Scan | fetched={len(kalshi_markets)} "
        f"with_price={prices_found} matched={matched} "
        f"arb≥{_current_min_edge*100:.1f}%={detail['spread_above_min']} "
        f"traded={detail['trades_this_scan']} | {detail['verdict']}"
    )
    return get_status()


def _finalize_detail(detail: dict):
    global _last_scan_detail
    _last_scan_detail = detail
    _SCAN_LOG.appendleft(detail)


def get_status() -> dict:
    total    = len(_sim_trades)
    win_rate = round(_wins / total, 4) if total > 0 else 0.0

    top_markets = []
    for km in _kalshi_markets_cache[:15]:
        title = km.get("title") or km.get("subtitle") or km.get("question") or ""
        price = _extract_yes_price(km)
        if title and price is not None:
            top_markets.append({
                "title":     title[:70],
                "yes_price": round(price, 4),
                "volume":    km.get("volume", 0),
            })

    return {
        "balance":            round(_balance,           4),
        "total_pnl":          round(_total_pnl,         4),
        "trades":             total,
        "wins":               _wins,
        "win_rate":           win_rate,
        "scanned":            _scanned,
        "current_threshold":  f"{_current_min_edge*100:.1f}%",
        "consecutive_zeros":  _consecutive_zero_runs,
        "verdict":            _threshold_verdict,
        "last_scan":          _last_scan_ts,
        "last_scan_detail":   _last_scan_detail,
        "scan_log":           list(_SCAN_LOG)[:15],
        "opportunities":      _last_opportunities,
        "top_markets":        top_markets,
        "sim_trades":         list(_sim_trades)[:30],
    }
