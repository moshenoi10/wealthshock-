"""
Strategy 5 — Cross Timeframe
Group markets with the same underlying asset + price threshold but different expiry.
If they diverge >1.5% (was 3%), buy the lagging one.
"""
import re
from typing import List, Optional

import config
from logger import rejected_logger
from strategies.base import Opportunity

NAME = "cross_timeframe"
MIN_DIVERGENCE = config.CROSS_TIMEFRAME_MIN_DIVERGENCE


def _asset_key(question: str) -> Optional[str]:
    ql = question.lower()
    for asset in ("btc", "bitcoin", "eth", "ethereum", "sol", "solana"):
        if asset in ql:
            m = re.search(r"\$[\d,kK\.]+", question, re.IGNORECASE)
            return f"{asset}_{m.group(0)}" if m else None
    return None


async def run(markets: List[dict], positions: dict, balance: float) -> List[Opportunity]:
    opps: List[Opportunity] = []

    groups: dict = {}
    for market in markets:
        key = _asset_key(market.get("question", ""))
        if not key:
            continue
        groups.setdefault(key, []).append(market)

    for key, group in groups.items():
        if len(group) < 2:
            continue

        group.sort(key=lambda m: m.get("end_date_iso") or "")

        for i in range(len(group) - 1):
            near = group[i]
            far = group[i + 1]

            near_yes = next(
                (t for t in near.get("tokens", []) if "YES" in (t.get("outcome") or "").upper()),
                None,
            )
            far_yes = next(
                (t for t in far.get("tokens", []) if "YES" in (t.get("outcome") or "").upper()),
                None,
            )
            if not near_yes or not far_yes:
                continue

            np_ = float(near_yes.get("price") or 0)
            fp_ = float(far_yes.get("price") or 0)
            if np_ <= 0 or fp_ <= 0:
                continue

            divergence = abs(np_ - fp_)
            question_near = near.get("question", "")
            question_far = far.get("question", "")

            if divergence < MIN_DIVERGENCE:
                if divergence > 0:
                    # Near-miss: divergence exists but below entry threshold
                    rejected_logger.log(
                        source=NAME,
                        reason="divergence_below_threshold",
                        market_question=f"{question_near[:50]} vs {question_far[:50]}",
                        value=divergence,
                        threshold=MIN_DIVERGENCE,
                        extra={"near_price": np_, "far_price": fp_, "key": key},
                    )
                continue

            if np_ < fp_:
                lagging_token = near_yes
                lagging_market = near
                ev = divergence
            else:
                lagging_token = far_yes
                lagging_market = far
                ev = divergence * 0.7

            if ev >= MIN_DIVERGENCE:
                opps.append(Opportunity(
                    strategy=NAME,
                    market_id=lagging_market.get("id", ""),
                    condition_id=lagging_market.get("condition_id", ""),
                    token_id=lagging_token.get("token_id", ""),
                    side="BUY",
                    price=float(lagging_token.get("price") or 0),
                    size=min(balance * config.MAX_POSITION_PCT, 5.0),
                    ev=ev,
                    reasoning=(
                        f"Cross-timeframe {key}: near={np_:.4f} far={fp_:.4f} "
                        f"divergence={divergence:.4f}"
                    ),
                    market_question=lagging_market.get("question", "")[:120],
                ))

    return opps
