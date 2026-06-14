"""
Simple in-memory rate limiter for login attempts.
3 failures → 10s cooldown. 10 failures → 1h block.
Keyed by IP address.
"""
import time
from collections import defaultdict
from typing import Tuple

_attempts: dict = defaultdict(list)  # ip → [timestamp, ...]
_WINDOW   = 3600  # 1 hour window for counting attempts
_SOFT_N   = 3     # failures before short cooldown
_SOFT_S   = 10    # 10-second cooldown
_HARD_N   = 10    # failures before 1-hour block
_HARD_S   = 3600  # 1-hour block


def _clean(ip: str):
    now = time.time()
    _attempts[ip] = [t for t in _attempts[ip] if now - t < _WINDOW]


def check(ip: str) -> Tuple[bool, int]:
    """
    Returns (allowed, retry_after_seconds).
    allowed=False means the request should be rejected.
    """
    _clean(ip)
    failures = _attempts[ip]
    n = len(failures)

    if n >= _HARD_N:
        wait = _HARD_S - (time.time() - failures[-1])
        return False, max(int(wait), 1)

    if n >= _SOFT_N:
        last = failures[-1] if failures else 0
        elapsed = time.time() - last
        if elapsed < _SOFT_S:
            return False, int(_SOFT_S - elapsed) + 1

    return True, 0


def record_failure(ip: str):
    _attempts[ip].append(time.time())


def clear(ip: str):
    _attempts.pop(ip, None)
