"""
Strategy 1 — Near Resolution
Buy the leading side of markets closing in <5 minutes when it trades below 0.98.
Minimum edge: 1.5%.
"""
from datetime import datetime, timezone
from typing import List

import config
from strategies.base import Opportunity

NAME = "near_resolution"


async def run(markets: List[dict], positions: dict, balance: float) -> List[Opportunity]:
    opps: List[Opportunity] = []
    now = datetime.now(timezone.utc)

    for market in markets:
        try:
            end_str = market.get("end_date_iso") or market.get("end_date") or ""
            if not end_str:
                continue

            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            minutes_left = (end_dt - now).total_seconds() / 60

            if not (0 < minutes_left < config.NEAR_RESOLUTION_WINDOW_MIN):
                continue

            tokens = market.get("tokens", [])
            if len(tokens) < 2:
                continue

            prices = {t.get("token_id"): float(t.get("price") or 0) for t in tokens}
            for token in tokens:
                price = float(token.get("price") or 0)
                tid = token.get("token_id", "")
                if not tid or price <= 0:
                    continue

                # Leading side: price > 0.5 but still below 0.98 → buy it
                if 0.50 < price < config.NEAR_RESOLUTION_MAX_PRICE:
                    ev = (1.0 - price)  # expected gain per dollar
                    if ev >= config.NEAR_RESOLUTION_MIN_EDGE:
                        opps.append(Opportunity(
                            strategy=NAME,
                            market_id=market.get("id", ""),
                            condition_id=market.get("condition_id", ""),
                            token_id=tid,
                            side="BUY",
                            price=price,
                            size=min(balance * config.MAX_POSITION_PCT, 5.0),
                            ev=ev,
                            reasoning=(
                                f"Near resolution ({minutes_left:.1f}min): "
                                f"{token.get('outcome')} @ {price:.4f}, edge={ev:.4f}"
                            ),
                            market_question=market.get("question", "")[:120],
                        ))
        except Exception:
            continue

    return opps
