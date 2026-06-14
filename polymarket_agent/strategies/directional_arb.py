"""
Strategy 3 — Directional Arbitrage
Like pure arb (threshold 0.995) but tilts 70/30 toward the side with stronger
recent 5-minute momentum.
"""
import asyncio
from typing import List

import httpx

import config
from logger import rejected_logger
from strategies.base import Opportunity

NAME = "directional_arb"
TILT_STRONG = 0.70
TILT_WEAK = 0.30


async def _momentum(token_id: str) -> float:
    """Returns 5-minute price change; positive = rising."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{config.POLYMARKET_HOST}/prices-history",
                params={"market": token_id, "interval": "5m", "fidelity": 5},
            )
            history = resp.json().get("history", [])
        if len(history) < 3:
            return 0.0
        prices = [float(h.get("p", 0)) for h in history[-6:]]
        return prices[-1] - prices[0]
    except Exception:
        return 0.0


async def run(markets: List[dict], positions: dict, balance: float) -> List[Opportunity]:
    opps: List[Opportunity] = []

    for market in markets:
        tokens = market.get("tokens", [])
        if len(tokens) != 2:
            continue

        prices = [float(t.get("price") or 0) for t in tokens]
        if not all(p > 0 for p in prices):
            continue

        total = sum(prices)
        edge = 1.0 - total
        question = market.get("question", "")

        if total >= config.ARBITRAGE_MAX_SUM:
            # Near-miss: prices sum to just above threshold
            if 0 < edge < (1.0 - config.ARBITRAGE_MAX_SUM):
                rejected_logger.log(
                    source=NAME,
                    reason="sum_above_threshold",
                    market_question=question,
                    value=total,
                    threshold=config.ARBITRAGE_MAX_SUM,
                    extra={"edge": round(edge, 6)},
                )
            continue

        if edge < config.ARBITRAGE_MIN_EDGE:
            continue

        mom0, mom1 = await asyncio.gather(
            _momentum(tokens[0].get("token_id", "")),
            _momentum(tokens[1].get("token_id", "")),
        )

        weights = (TILT_STRONG, TILT_WEAK) if mom0 >= mom1 else (TILT_WEAK, TILT_STRONG)
        budget = min(balance * config.MAX_POSITION_PCT, 5.0)

        for token, price, weight, mom in zip(tokens, prices, weights, [mom0, mom1]):
            opps.append(Opportunity(
                strategy=NAME,
                market_id=market.get("id", ""),
                condition_id=market.get("condition_id", ""),
                token_id=token.get("token_id", ""),
                side="BUY",
                price=price,
                size=budget * weight,
                ev=edge + abs(mom) * 0.3,
                reasoning=(
                    f"Dir arb: sum={total:.4f} edge={edge:.4f} "
                    f"weight={weight} mom={mom:+.4f}"
                ),
                market_question=question[:120],
            ))

    return opps
