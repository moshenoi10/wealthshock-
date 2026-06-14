"""
Kalshi simulation — paper trading only, no auth needed.
Fetches public Kalshi market data, finds cross-platform arbitrage
opportunities vs Polymarket prices, and simulates trades.
"""
import asyncio
import json
from collections import deque
from datetime import datetime, timezone
from typing import List, Optional

import httpx

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
INITIAL_BALANCE = 100.0
MIN_EDGE = 0.015  # 1.5% minimum arb edge to simulate a trade
MAX_POSITION_PCT = 0.10

_balance: float = INITIAL_BALANCE
_total_pnl: float = 0.0
_sim_trades: deque = deque(maxlen=200)
_last_opportunities: List[dict] = []
_scanned: int = 0


async def _fetch_kalshi_markets() -> List[dict]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{KALSHI_API}/markets",
                params={"status": "open", "limit": 100},
                headers={"accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("markets", [])
    except Exception as exc:
        print(f"[KALSHI] Fetch error: {exc}")
    return []


def _extract_kalshi_price(market: dict) -> Optional[float]:
    """Extract YES price from Kalshi market (0–1 range)."""
    yes_bid = market.get("yes_bid")
    yes_ask = market.get("yes_ask")
    if yes_bid is not None and yes_ask is not None:
        return (float(yes_bid) + float(yes_ask)) / 2 / 100  # Kalshi uses cents
    return None


def _match_to_polymarket(kalshi_title: str, poly_markets: List[dict]) -> Optional[dict]:
    """Find a Polymarket market matching the Kalshi event by keyword overlap."""
    kl = kalshi_title.lower()
    words = set(w for w in kl.split() if len(w) > 4)
    best_match, best_score = None, 0
    for pm in poly_markets:
        pl = pm.get("question", "").lower()
        score = sum(1 for w in words if w in pl)
        if score > best_score and score >= 2:
            best_score, best_match = score, pm
    return best_match


async def scan(poly_markets: List[dict]) -> dict:
    global _balance, _total_pnl, _scanned

    kalshi_markets = await _fetch_kalshi_markets()
    _scanned += len(kalshi_markets)

    opportunities = []
    for km in kalshi_markets:
        title = km.get("title", "")
        k_price = _extract_kalshi_price(km)
        if k_price is None or k_price <= 0 or k_price >= 1:
            continue

        pm = _match_to_polymarket(title, poly_markets)
        if not pm:
            continue

        # Get Polymarket YES price
        yes_token = next(
            (t for t in pm.get("tokens", []) if "YES" in (t.get("outcome") or "").upper()),
            None,
        )
        if not yes_token:
            continue
        p_price = float(yes_token.get("price") or 0)
        if p_price <= 0 or p_price >= 1:
            continue

        edge = abs(p_price - k_price)
        if edge < MIN_EDGE:
            continue

        # Determine direction: buy the cheaper one
        if k_price < p_price:
            buy_platform, sell_platform = "Kalshi", "Polymarket"
            buy_price, sell_price = k_price, p_price
        else:
            buy_platform, sell_platform = "Polymarket", "Kalshi"
            buy_price, sell_price = p_price, k_price

        opportunities.append({
            "question": title[:80],
            "poly_price": round(p_price, 4),
            "kalshi_price": round(k_price, 4),
            "edge": round(edge, 4),
            "buy_platform": buy_platform,
            "sell_platform": sell_platform,
            "buy_price": round(buy_price, 4),
            "sell_price": round(sell_price, 4),
        })

        # Simulate a paper trade if balance allows
        size = min(_balance * MAX_POSITION_PCT, 5.0)
        if size > 0.5:
            simulated_pnl = round(size * edge * 0.8, 4)  # 80% efficiency assumption
            _balance = round(_balance + simulated_pnl, 4)
            _total_pnl = round(_total_pnl + simulated_pnl, 4)
            _sim_trades.appendleft({
                "ts": datetime.now(timezone.utc).isoformat(),
                "question": title[:60],
                "side": "BUY",
                "price": round(buy_price, 4),
                "size": round(size, 2),
                "pnl": simulated_pnl,
            })

    _last_opportunities.clear()
    _last_opportunities.extend(sorted(opportunities, key=lambda x: x["edge"], reverse=True)[:5])

    return get_status()


def get_status() -> dict:
    return {
        "balance": round(_balance, 4),
        "total_pnl": round(_total_pnl, 4),
        "trades": len(_sim_trades),
        "scanned": _scanned,
        "opportunities": _last_opportunities,
        "sim_trades": list(_sim_trades)[:30],
    }
