"""
Strategy — Momentum Following (paper mode only)

Tracks the price direction of YES tokens across consecutive scans.
If the price has moved in the same direction for PAPER_MOMENTUM_LOOKBACK_SCANS
consecutive scans AND the total move exceeds PAPER_MOMENTUM_MIN_MOVE, enter
in that direction (buy whichever token is rising).

Why this can work on prediction markets:
- Informed traders accumulate positions before news hits widely
- Short-term price momentum persists for 1-3 scan cycles (30-90 seconds)
- We ride the tail, not the front

Why it fails:
- If the move was a single large order, it will revert (see overreaction.py)
- Near-resolved markets can snap to 0 or 1 instantly

The strategy declines any market where a position is already held.
All sizing uses PAPER_MAX_POSITION_PCT — never touches live risk settings.
"""
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import config
from strategies.base import Opportunity

NAME = "momentum"

# price cache: token_id → deque of (scan_idx, price)
_price_cache: Dict[str, deque] = {}
_scan_idx: int = 0
_triggered: int = 0
_last_scan_ts: Optional[str] = None


def _record_prices(markets: List[dict]):
    """Update price cache for all YES tokens in this scan."""
    global _scan_idx
    _scan_idx += 1
    for market in markets:
        for token in market.get("tokens", []):
            tid = token.get("token_id", "")
            if not tid:
                continue
            try:
                price = float(token.get("price") or 0)
            except (TypeError, ValueError):
                continue
            if price <= 0 or price >= 1:
                continue
            if tid not in _price_cache:
                _price_cache[tid] = deque(maxlen=config.PAPER_MOMENTUM_LOOKBACK_SCANS + 2)
            _price_cache[tid].append((_scan_idx, price))


def _momentum_signal(tid: str) -> Optional[Tuple[str, float]]:
    """
    Returns ("BUY", magnitude) if consistent upward momentum,
    ("SELL_SHORT", magnitude) for consistent downward,
    or None if no signal.
    """
    history = _price_cache.get(tid)
    if not history or len(history) < config.PAPER_MOMENTUM_LOOKBACK_SCANS:
        return None

    prices = [p for _, p in list(history)[-config.PAPER_MOMENTUM_LOOKBACK_SCANS:]]
    deltas = [prices[i+1] - prices[i] for i in range(len(prices) - 1)]
    if not deltas:
        return None

    all_up   = all(d > 0 for d in deltas)
    all_down = all(d < 0 for d in deltas)
    total_move = prices[-1] - prices[0]

    if abs(total_move) < config.PAPER_MOMENTUM_MIN_MOVE:
        return None

    if all_up:
        return ("BUY", total_move)
    if all_down:
        # Buy the opposite token (NO) — represented as buying YES with flipped side
        # We signal a BUY on the falling YES token only if NO is cheap enough
        return ("SELL", abs(total_move))
    return None


async def run(markets: List[dict], positions: dict, balance: float) -> List[Opportunity]:
    global _triggered, _last_scan_ts

    if config.TRADING_MODE != "paper":
        return []   # hard gate: momentum is paper-only

    _last_scan_ts = datetime.now(timezone.utc).isoformat()
    _record_prices(markets)

    opps: List[Opportunity] = []
    pos_pct = config.PAPER_MAX_POSITION_PCT

    for market in markets:
        cid      = market.get("condition_id", "")
        question = market.get("question", "")[:120]

        for token in market.get("tokens", []):
            tid     = token.get("token_id", "")
            outcome = token.get("outcome", "")
            try:
                price = float(token.get("price") or 0)
            except (TypeError, ValueError):
                continue

            if not tid or price <= 0.05 or price >= 0.95:
                continue  # skip near-resolved
            if tid in positions:
                continue  # already holding

            signal = _momentum_signal(tid)
            if signal is None:
                continue

            direction, magnitude = signal

            if direction == "BUY":
                # Price is rising — buy YES
                ev = min(magnitude * 2.5, 0.12)  # conservative EV estimate
                opps.append(Opportunity(
                    strategy=NAME,
                    market_id=market.get("id", ""),
                    condition_id=cid,
                    token_id=tid,
                    side="BUY",
                    price=price,
                    size=balance * pos_pct,
                    ev=ev,
                    reasoning=(
                        f"Momentum↑ {outcome}@{price:.3f} "
                        f"move={magnitude*100:+.1f}% "
                        f"over {config.PAPER_MOMENTUM_LOOKBACK_SCANS} scans"
                    ),
                    market_question=question,
                ))
                _triggered += 1

            elif direction == "SELL" and price < 0.45:
                # Falling YES token — if it's already the losing side, skip.
                # Only fade falling prices when we can buy NO at a discount.
                # We approximate "buy NO" by buying YES on the other token.
                yes_token = next(
                    (t for t in market.get("tokens", [])
                     if t.get("outcome", "").upper() == "NO"),
                    None,
                )
                if yes_token:
                    no_tid   = yes_token.get("token_id", "")
                    no_price = float(yes_token.get("price") or 0)
                    if no_tid and no_tid not in positions and 0.05 < no_price < 0.95:
                        ev = min(magnitude * 2.0, 0.10)
                        opps.append(Opportunity(
                            strategy=NAME,
                            market_id=market.get("id", ""),
                            condition_id=cid,
                            token_id=no_tid,
                            side="BUY",
                            price=no_price,
                            size=balance * pos_pct,
                            ev=ev,
                            reasoning=(
                                f"Momentum↓ YES falling → buy NO@{no_price:.3f} "
                                f"move={magnitude*100:.1f}% "
                                f"over {config.PAPER_MOMENTUM_LOOKBACK_SCANS} scans"
                            ),
                            market_question=question,
                        ))
                        _triggered += 1

    if opps:
        print(f"[MOMENTUM] {len(opps)} signal(s) | scan={_scan_idx}")
    return opps


def get_debug() -> dict:
    return {
        "scan_idx":      _scan_idx,
        "last_scan_ts":  _last_scan_ts,
        "markets_tracked": len(_price_cache),
        "total_triggered": _triggered,
        "lookback_scans": config.PAPER_MOMENTUM_LOOKBACK_SCANS,
        "min_move_pct":   config.PAPER_MOMENTUM_MIN_MOVE * 100,
    }
