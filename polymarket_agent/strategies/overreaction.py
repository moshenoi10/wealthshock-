"""
Strategy — Overreaction Fade (paper mode only)

When a YES token jumps or drops more than PAPER_OVERREACTION_JUMP_PCT
within 60 seconds (2 scans at 10s interval → ~6 scans at old 30s interval),
bet against the move. Prediction market prices are mean-reverting in the short
term because:
  - A single large order moves the thin book temporarily
  - Informed traders slowly push back to fair value
  - Resolution-anchored prices don't sustain momentum from non-informational flow

Evidence from the LP Tool repo: ~60-70% of >8% single-minute jumps reverted
within 5 minutes on Polymarket binary markets.

Fade logic:
  - Price up >8% → buy NO (or sell YES proxy)
  - Price down >8% → buy YES (discounted entry)

Risk: this LOSES when the jump is informational (real news). We accept that.
All sizing uses PAPER_MAX_POSITION_PCT.
"""
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import config
from strategies.base import Opportunity

NAME = "overreaction"

# Snapshot: token_id → (timestamp, price) taken LOOKBACK_SECONDS ago
_snapshots: Dict[str, Tuple[float, float]] = {}
_price_now:  Dict[str, float] = {}
LOOKBACK_SECONDS = 60.0

_triggered: int = 0
_last_scan_ts: Optional[str] = None
_recent_signals: deque = deque(maxlen=50)


def _update_snapshots(markets: List[dict]):
    """
    Two-phase: save current prices as "now", then age the old snapshot
    and replace with current if it's old enough.
    """
    now_ts = time.time()
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

            _price_now[tid] = price

            if tid not in _snapshots:
                # First time seeing this token — save as baseline
                _snapshots[tid] = (now_ts, price)
            else:
                snap_ts, _ = _snapshots[tid]
                if now_ts - snap_ts >= LOOKBACK_SECONDS:
                    # Snapshot is old enough — replace with a fresh one
                    _snapshots[tid] = (now_ts, price)


def _overreaction_signal(tid: str) -> Optional[Tuple[str, float, float]]:
    """
    Returns (direction, jump_pct, current_price) or None.
    direction is "fade_up" (price jumped up → buy NO) or
    "fade_down" (price fell → buy YES).
    """
    if tid not in _snapshots or tid not in _price_now:
        return None

    snap_ts, snap_price = _snapshots[tid]
    current = _price_now[tid]
    now_ts  = time.time()

    # Only compare snapshots that are 45–90 seconds old (our target window)
    age = now_ts - snap_ts
    if age < 45 or age > 90:
        return None

    if snap_price <= 0:
        return None

    jump = (current - snap_price) / snap_price

    threshold = config.PAPER_OVERREACTION_JUMP_PCT

    if jump >= threshold:
        return ("fade_up", jump, current)
    if jump <= -threshold:
        return ("fade_down", abs(jump), current)
    return None


async def run(markets: List[dict], positions: dict, balance: float) -> List[Opportunity]:
    global _triggered, _last_scan_ts

    if config.TRADING_MODE != "paper":
        return []   # hard gate: overreaction is paper-only

    _last_scan_ts = datetime.now(timezone.utc).isoformat()
    _update_snapshots(markets)

    opps: List[Opportunity] = []
    pos_pct = config.PAPER_MAX_POSITION_PCT

    for market in markets:
        cid      = market.get("condition_id", "")
        question = market.get("question", "")[:120]
        tokens   = market.get("tokens", [])
        if len(tokens) < 2:
            continue

        for token in tokens:
            tid     = token.get("token_id", "")
            outcome = token.get("outcome", "")
            try:
                price = float(token.get("price") or 0)
            except (TypeError, ValueError):
                continue

            if not tid or price <= 0.05 or price >= 0.95:
                continue
            if tid in positions:
                continue

            signal = _overreaction_signal(tid)
            if signal is None:
                continue

            direction, jump_pct, current_price = signal

            if direction == "fade_up":
                # YES jumped → buy NO (opposite token)
                no_token = next(
                    (t for t in tokens if t.get("token_id") != tid),
                    None,
                )
                if not no_token:
                    continue
                no_tid   = no_token.get("token_id", "")
                no_price = float(no_token.get("price") or 0)
                if not no_tid or no_tid in positions or not (0.05 < no_price < 0.95):
                    continue

                ev = min(jump_pct * 0.6, 0.15)  # expect 60% of jump to revert
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
                        f"Fade↑ overreaction: {outcome} jumped {jump_pct*100:.1f}% in 60s "
                        f"({current_price:.3f}) → buy NO@{no_price:.3f}"
                    ),
                    market_question=question,
                ))
                _triggered += 1
                _recent_signals.appendleft({
                    "ts": _last_scan_ts, "market": question[:60],
                    "direction": "fade_up", "jump_pct": round(jump_pct * 100, 2),
                    "entry_price": no_price,
                })

            elif direction == "fade_down":
                # YES fell → buy YES (discount entry after overreaction)
                ev = min(jump_pct * 0.6, 0.15)
                opps.append(Opportunity(
                    strategy=NAME,
                    market_id=market.get("id", ""),
                    condition_id=cid,
                    token_id=tid,
                    side="BUY",
                    price=current_price,
                    size=balance * pos_pct,
                    ev=ev,
                    reasoning=(
                        f"Fade↓ overreaction: {outcome} fell {jump_pct*100:.1f}% in 60s "
                        f"→ buy YES@{current_price:.3f} (discount)"
                    ),
                    market_question=question,
                ))
                _triggered += 1
                _recent_signals.appendleft({
                    "ts": _last_scan_ts, "market": question[:60],
                    "direction": "fade_down", "jump_pct": round(jump_pct * 100, 2),
                    "entry_price": current_price,
                })

    if opps:
        print(f"[OVERREACTION] {len(opps)} fade signal(s)")
    return opps


def get_debug() -> dict:
    return {
        "last_scan_ts":    _last_scan_ts,
        "tokens_tracked":  len(_price_now),
        "total_triggered": _triggered,
        "jump_threshold_pct": config.PAPER_OVERREACTION_JUMP_PCT * 100,
        "lookback_seconds":   LOOKBACK_SECONDS,
        "recent_signals":     list(_recent_signals)[:20],
    }
