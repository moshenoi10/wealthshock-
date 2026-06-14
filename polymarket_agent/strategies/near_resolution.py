"""
Strategy 1 — Near Resolution

Fetch markets sorted by endDate ASC (separate NR fetch in market_data).
Buy the leading side when closing within NEAR_RESOLUTION_WINDOW_MIN minutes
and the price is below NEAR_RESOLUTION_MAX_PRICE.

Scan log records EVERY market processed with rejection reason, so the debug
endpoint can show why nothing triggered each cycle.
"""
from collections import deque
from datetime import datetime, timezone
from typing import List

import config
from logger import rejected_logger
from strategies.base import Opportunity

NAME = "near_resolution"

# ── Scan-level debug log ────────────────────────────────────────────────────
_scan_log: deque = deque(maxlen=500)   # last 500 market evaluations
_summary_log: deque = deque(maxlen=50)  # one row per strategy run

_last_scan_markets = 0
_last_scan_found   = 0
_last_scan_ts      = None


def _parse_end_dt(end_str: str):
    """Parse endDate from Gamma API — handles both date-only and full ISO formats."""
    if not end_str:
        return None
    end_str = end_str.strip()
    # Replace trailing Z before parsing
    end_str = end_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(end_str)
        # If naive (no tzinfo), assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


async def run(markets: List[dict], positions: dict, balance: float) -> List[Opportunity]:
    global _last_scan_markets, _last_scan_found, _last_scan_ts

    opps: List[Opportunity] = []
    now = datetime.now(timezone.utc)
    scan_ts = now.isoformat()
    _last_scan_ts = scan_ts
    _last_scan_markets = len(markets)

    batch_entries = []

    for market in markets:
        question = market.get("question", "")[:80]
        end_str   = market.get("end_date_iso") or market.get("end_date") or ""

        # ── Filter: has end date?
        if not end_str:
            batch_entries.append({
                "q": question, "reason": "no_end_date",
                "min_left": None, "price": None,
            })
            continue

        end_dt = _parse_end_dt(end_str)
        if end_dt is None:
            batch_entries.append({
                "q": question, "reason": f"bad_date_format:{end_str[:20]}",
                "min_left": None, "price": None,
            })
            continue

        minutes_left = (end_dt - now).total_seconds() / 60

        # ── Filter: within window?
        if minutes_left <= 0:
            batch_entries.append({
                "q": question, "reason": "already_closed",
                "min_left": round(minutes_left, 1), "price": None,
            })
            continue
        if minutes_left >= config.NEAR_RESOLUTION_WINDOW_MIN:
            batch_entries.append({
                "q": question, "reason": f"too_far:{round(minutes_left,1)}min",
                "min_left": round(minutes_left, 1), "price": None,
            })
            continue

        # ── Filter: has tokens?
        tokens = market.get("tokens", [])
        if len(tokens) < 2:
            batch_entries.append({
                "q": question, "reason": "no_tokens",
                "min_left": round(minutes_left, 1), "price": None,
            })
            continue

        # ── Evaluate each token
        any_entered = False
        for token in tokens:
            try:
                price = float(token.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            tid     = token.get("token_id", "")
            outcome = token.get("outcome", "?")

            if not tid or price <= 0:
                continue

            # Price range: must be above 0.50 (likely winner) but below max_price
            if price <= 0.50:
                batch_entries.append({
                    "q": question, "reason": f"price_too_low:{price:.3f}({outcome})",
                    "min_left": round(minutes_left, 1), "price": price,
                })
                continue
            if price >= config.NEAR_RESOLUTION_MAX_PRICE:
                batch_entries.append({
                    "q": question, "reason": f"price_already_resolved:{price:.3f}({outcome})",
                    "min_left": round(minutes_left, 1), "price": price,
                })
                continue

            ev = 1.0 - price

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
                        f"{outcome} @ {price:.4f}, edge={ev:.4f}"
                    ),
                    market_question=market.get("question", "")[:120],
                ))
                batch_entries.append({
                    "q": question, "reason": f"TRIGGERED({outcome}@{price:.3f})",
                    "min_left": round(minutes_left, 1), "price": price,
                    "ev": round(ev, 4),
                })
                any_entered = True
            else:
                # Near-miss
                batch_entries.append({
                    "q": question,
                    "reason": f"ev_too_low:{ev:.4f}<{config.NEAR_RESOLUTION_MIN_EDGE}({outcome}@{price:.3f})",
                    "min_left": round(minutes_left, 1), "price": price,
                    "ev": round(ev, 4),
                })
                rejected_logger.log(
                    source=NAME,
                    reason="ev_below_min_edge",
                    market_question=market.get("question", ""),
                    token_id=tid,
                    value=ev,
                    threshold=config.NEAR_RESOLUTION_MIN_EDGE,
                    extra={"price": price, "minutes_left": round(minutes_left, 2)},
                )

    # Append batch to scan log (newest first)
    for e in reversed(batch_entries):
        _scan_log.appendleft({**e, "ts": scan_ts})

    _last_scan_found = len(opps)
    _summary_log.appendleft({
        "ts":        scan_ts,
        "markets":   len(markets),
        "evaluated": len(batch_entries),
        "triggered": len(opps),
        "window_min":  config.NEAR_RESOLUTION_WINDOW_MIN,
        "max_price":   config.NEAR_RESOLUTION_MAX_PRICE,
    })

    # Always print something so Railway logs are useful
    in_window = [e for e in batch_entries if e.get("min_left") is not None and 0 < (e.get("min_left") or 999) < config.NEAR_RESOLUTION_WINDOW_MIN]
    print(
        f"[NR] {len(markets)} markets | "
        f"{len(in_window)} in window | "
        f"{len(opps)} triggered | "
        f"window={config.NEAR_RESOLUTION_WINDOW_MIN}min"
    )

    return opps


def get_debug() -> dict:
    return {
        "last_scan_ts":      _last_scan_ts,
        "last_scan_markets": _last_scan_markets,
        "last_scan_found":   _last_scan_found,
        "window_min":        config.NEAR_RESOLUTION_WINDOW_MIN,
        "max_price":         config.NEAR_RESOLUTION_MAX_PRICE,
        "min_edge":          config.NEAR_RESOLUTION_MIN_EDGE,
        "recent_evals":      list(_scan_log)[:50],
        "summary_log":       list(_summary_log)[:20],
    }
