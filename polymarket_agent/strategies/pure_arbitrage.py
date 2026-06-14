"""
Strategy 2 — Pure Arbitrage
If YES_price + NO_price < 0.995 (was 0.98), buy both sides for guaranteed profit.
"""
from typing import List

import config
from logger import rejected_logger
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
        edge = 1.0 - total
        question = market.get("question", "")

        if total >= config.ARBITRAGE_MAX_SUM:
            # Near-miss: sum is above threshold but total < 100% (some positive edge exists)
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

        budget = min(balance * config.MAX_POSITION_PCT, 5.0)
        for token, price in zip(tokens, prices):
            weight = price / total
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
                market_question=question[:120],
            ))

    return opps
