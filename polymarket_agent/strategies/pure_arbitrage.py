"""
Strategy 2 — Pure Arbitrage
If YES_price + NO_price < 0.98, buy both sides for guaranteed profit.
"""
from typing import List

import config
from strategies.base import Opportunity

NAME = "pure_arbitrage"


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
        if total >= config.ARBITRAGE_MAX_SUM:
            continue

        edge = 1.0 - total
        if edge < 0.005:
            continue

        budget = min(balance * config.MAX_POSITION_PCT, 5.0)
        for token, price in zip(tokens, prices):
            weight = price / total  # proportional allocation
            opps.append(Opportunity(
                strategy=NAME,
                market_id=market.get("id", ""),
                condition_id=market.get("condition_id", ""),
                token_id=token.get("token_id", ""),
                side="BUY",
                price=price,
                size=budget * weight,
                ev=edge,
                reasoning=(
                    f"Pure arb: YES+NO={total:.4f} (edge={edge:.4f}), "
                    f"{token.get('outcome')} @ {price:.4f}"
                ),
                market_question=market.get("question", "")[:120],
            ))

    return opps
