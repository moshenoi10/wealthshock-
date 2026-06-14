"""
Kalshi simulation — paper trading only, no auth needed.
Fetches public Kalshi market data, finds cross-platform arb vs Polymarket.
Simulates a trade whenever the same event shows >1.5% price divergence.
"""
import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import List, Optional

import httpx

# Primary API, fallback to elections subdomain
KALSHI_URLS = [
    "https://api.kalshi.com/trade-api/v2",
    "https://api.elections.kalshi.com/trade-api/v2",
]
INITIAL_BALANCE = 100.0
MIN_EDGE = 0.015        # 1.5% minimum spread to trade
MAX_POSITION_PCT = 0.10

_balance: float = INITIAL_BALANCE
_total_pnl: float = 0.0
_wins: int = 0
_sim_trades: deque = deque(maxlen=200)
_last_opportunities: List[dict] = []
_scanned: int = 0
_kalshi_markets_cache: List[dict] = []  # latest raw Kalshi markets for display


async def _fetch_kalshi_markets() -> List[dict]:
    for base_url in KALSHI_URLS:
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(
                    f"{base_url}/markets",
                    params={"status": "open", "limit": 100},
                    headers={"accept": "application/json"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    markets = data.get("markets", [])
                    if markets:
                        print(f"[KALSHI] Fetched {len(markets)} markets from {base_url}")
                        return markets
        except Exception as exc:
            print(f"[KALSHI] {base_url} error: {exc}")
    return []


def _extract_yes_price(market: dict) -> Optional[float]:
    """Return mid YES price in [0,1]. Kalshi quotes in cents."""
    yes_bid = market.get("yes_bid")
    yes_ask = market.get("yes_ask")
    last = market.get("last_price")
    if yes_bid is not None and yes_ask is not None:
        return (float(yes_bid) + float(yes_ask)) / 2 / 100
    if last is not None:
        return float(last) / 100
    return None


def _match_polymarket(kalshi_title: str, poly_markets: List[dict]) -> Optional[dict]:
    """
    Find the best-matching Polymarket market for a Kalshi event.
    Requires at least 1 meaningful keyword in common.
    """
    kl = kalshi_title.lower()
    stopwords = {"will", "the", "a", "an", "to", "in", "of", "by", "be", "at", "or"}
    keywords = [w for w in kl.split() if len(w) > 3 and w not in stopwords]

    best_match, best_score = None, 0
    for pm in poly_markets:
        pl = pm.get("question", "").lower()
        score = sum(1 for w in keywords if w in pl)
        if score > best_score and score >= 1:
            best_score, best_match = score, pm
    return best_match


async def scan(poly_markets: List[dict]) -> dict:
    global _balance, _total_pnl, _wins, _scanned, _kalshi_markets_cache

    kalshi_markets = await _fetch_kalshi_markets()
    _scanned += len(kalshi_markets)
    if kalshi_markets:
        _kalshi_markets_cache = kalshi_markets

    opportunities = []

    for km in kalshi_markets:
        title = km.get("title", "") or km.get("subtitle", "") or ""
        if not title:
            continue
        k_price = _extract_yes_price(km)
        if k_price is None or not (0 < k_price < 1):
            continue

        pm = _match_polymarket(title, poly_markets)
        if not pm:
            continue

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
        if edge < MIN_EDGE:
            continue

        if k_price < p_price:
            buy_plat, sell_plat = "Kalshi", "Polymarket"
            buy_price, sell_price = k_price, p_price
        else:
            buy_plat, sell_plat = "Polymarket", "Kalshi"
            buy_price, sell_price = p_price, k_price

        opportunities.append({
            "question": title[:80],
            "poly_question": pm.get("question", "")[:60],
            "poly_price": round(p_price, 4),
            "kalshi_price": round(k_price, 4),
            "edge": round(edge, 4),
            "buy_platform": buy_plat,
            "sell_platform": sell_plat,
            "buy_price": round(buy_price, 4),
            "sell_price": round(sell_price, 4),
        })

        # Simulate paper trade
        size = min(_balance * MAX_POSITION_PCT, 5.0)
        if size >= 0.5:
            # Conservative: assume 75% of spread captured after slippage/fees
            sim_pnl = round(size * edge * 0.75, 4)
            _balance = round(_balance + sim_pnl, 4)
            _total_pnl = round(_total_pnl + sim_pnl, 4)
            if sim_pnl > 0:
                _wins += 1
            _sim_trades.appendleft({
                "ts": datetime.now(timezone.utc).isoformat(),
                "question": title[:60],
                "side": f"קנה {buy_plat} / מכור {sell_plat}",
                "price": round(buy_price, 4),
                "size": round(size, 2),
                "edge_pct": round(edge * 100, 2),
                "pnl": sim_pnl,
            })

    _last_opportunities.clear()
    _last_opportunities.extend(
        sorted(opportunities, key=lambda x: x["edge"], reverse=True)[:5]
    )

    return get_status()


def get_status() -> dict:
    total = len(_sim_trades)
    win_rate = round(_wins / total, 4) if total > 0 else 0.0

    # Build a display list of top Kalshi markets even without Poly match
    top_markets = []
    for km in _kalshi_markets_cache[:10]:
        title = km.get("title") or km.get("subtitle") or ""
        price = _extract_yes_price(km)
        if title and price is not None:
            top_markets.append({
                "title": title[:70],
                "yes_price": round(price, 4),
                "volume": km.get("volume", 0),
            })

    return {
        "balance": round(_balance, 4),
        "total_pnl": round(_total_pnl, 4),
        "trades": total,
        "wins": _wins,
        "win_rate": win_rate,
        "scanned": _scanned,
        "opportunities": _last_opportunities,
        "top_markets": top_markets,
        "sim_trades": list(_sim_trades)[:30],
    }
