"""
Rejected opportunity logger.

Logs every opportunity that was EVALUATED but did not trigger a trade —
either because it fell short of a strategy entry threshold (near-miss)
or because the risk manager blocked it (risk rejection).

Output: data/rejected_opportunities.json  (last 2000 entries, newest last)
"""
import json
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import config

MAX_ENTRIES = 2_000

_log: deque = deque(maxlen=MAX_ENTRIES)
_dirty = False


def log(
    *,
    source: str,          # e.g. "near_resolution", "risk_manager"
    reason: str,          # e.g. "ev_below_min_edge", "already_holding"
    market_question: str,
    token_id: str = "",
    value: float,         # the metric that was measured
    threshold: float,     # the threshold it was compared against
    extra: Optional[dict] = None,
) -> None:
    delta = round(value - threshold, 6)   # negative = missed by |delta|
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "reason": reason,
        "question": market_question[:90],
        "token_id": token_id[:20] if token_id else "",
        "value": round(value, 6),
        "threshold": round(threshold, 6),
        "delta": delta,                   # how far off (negative = below threshold)
    }
    if extra:
        entry.update(extra)
    _log.append(entry)
    _flush()


def _flush() -> None:
    try:
        config.DATA_DIR.mkdir(exist_ok=True)
        config.REJECTED_FILE.write_text(json.dumps(list(_log), indent=2))
    except Exception:
        pass


def get_recent(n: int = 100) -> list:
    return list(_log)[-n:]
