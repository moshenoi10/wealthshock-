"""
Strategy 4 — Repricing / Fair Value
Fetch BTC/ETH/SOL from CoinGecko. If Polymarket lags >1% from fair value (was 2%),
buy the undervalued side with a limit order.
"""
import re
import time
from typing import List, Optional

import httpx

import config
from logger import rejected_logger
from strategies.base import Opportunity

NAME = "repricing"

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

ASSET_KEYWORDS: dict = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
    "SOL": ["solana", "sol"],
}

# Keywords indicating YES = price going DOWN (inverts fair_prob)
_BEARISH = ("dip", "fall", "drop", "crash", "below", "under", "decline", "lose")

_price_cache: dict = {}
_cache_ts: float = 0.0


async def _crypto_prices() -> dict:
    global _price_cache, _cache_ts
    if time.time() - _cache_ts < 60 and _price_cache:
        return _price_cache
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                COINGECKO_URL,
                params={"ids": "bitcoin,ethereum,solana", "vs_currencies": "usd"},
            )
            data = resp.json()
        _price_cache = {
            "BTC": float(data.get("bitcoin", {}).get("usd", 0) or 0),
            "ETH": float(data.get("ethereum", {}).get("usd", 0) or 0),
            "SOL": float(data.get("solana", {}).get("usd", 0) or 0),
        }
        _cache_ts = time.time()
    except Exception as exc:
        print(f"[REPRICING] CoinGecko error: {exc}")
    return _price_cache


def _detect_asset(question: str) -> Optional[str]:
    ql = question.lower()
    for asset, kws in ASSET_KEYWORDS.items():
        if any(kw in ql for kw in kws):
            return asset
    return None


def _extract_threshold(question: str) -> Optional[float]:
    patterns = [
        (r"\$\s*([0-9,]+)\s*[kK]", 1_000),
        (r"\$\s*([0-9,]+(?:\.[0-9]+)?)", 1),
        (r"([0-9,]+)\s*[kK]", 1_000),
    ]
    for pattern, mult in patterns:
        m = re.search(pattern, question, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", "")) * mult
            except ValueError:
                continue
    return None


def _fair_prob_above(current: float, threshold: float) -> float:
    """P(price > threshold) at current price."""
    if threshold <= 0 or current <= 0:
        return 0.5
    r = current / threshold
    if r >= 1.15:   return 0.95
    elif r >= 1.10: return 0.90
    elif r >= 1.05: return 0.80
    elif r >= 1.02: return 0.70
    elif r >= 1.00: return 0.60
    elif r >= 0.98: return 0.45
    elif r >= 0.95: return 0.30
    elif r >= 0.90: return 0.20
    elif r >= 0.85: return 0.10
    else:           return 0.05


async def run(markets: List[dict], positions: dict, balance: float) -> List[Opportunity]:
    opps: List[Opportunity] = []
    prices = await _crypto_prices()

    for market in markets:
        try:
            question = market.get("question", "")
            ql = question.lower()

            # Skip range markets — directional logic doesn't apply
            if "between" in ql and " and " in ql:
                continue

            asset = _detect_asset(question)
            if not asset:
                continue

            current = prices.get(asset, 0)
            if not current:
                continue

            threshold = _extract_threshold(question)
            if not threshold:
                continue

            prob_above = _fair_prob_above(current, threshold)
            bearish = any(kw in ql for kw in _BEARISH)

            for token in market.get("tokens", []):
                tp = float(token.get("price") or 0)
                tid = token.get("token_id", "")
                outcome = (token.get("outcome") or "").upper()
                if not tid or tp <= 0:
                    continue

                if "YES" in outcome:
                    fair = (1.0 - prob_above) if bearish else prob_above
                elif "NO" in outcome:
                    fair = prob_above if bearish else (1.0 - prob_above)
                else:
                    continue

                deviation = fair - tp

                if deviation > config.REPRICING_MIN_DEVIATION:
                    direction = "bearish" if bearish else "bullish"
                    opps.append(Opportunity(
                        strategy=NAME,
                        market_id=market.get("id", ""),
                        condition_id=market.get("condition_id", ""),
                        token_id=tid,
                        side="BUY",
                        price=round(min(tp + 0.002, 0.97), 4),
                        size=min(balance * config.MAX_POSITION_PCT, 5.0),
                        ev=deviation,
                        reasoning=(
                            f"Repricing({direction}): {asset}=${current:,.0f} "
                            f"thresh=${threshold:,.0f} "
                            f"fair={fair:.3f} mkt={tp:.3f} Δ={deviation:.3f}"
                        ),
                        market_question=question[:120],
                    ))
                elif 0 < deviation <= config.REPRICING_MIN_DEVIATION:
                    # Near-miss: mispricing exists but below entry threshold
                    rejected_logger.log(
                        source=NAME,
                        reason="deviation_below_threshold",
                        market_question=question,
                        token_id=tid,
                        value=deviation,
                        threshold=config.REPRICING_MIN_DEVIATION,
                        extra={
                            "asset": asset,
                            "current_price": current,
                            "fair": round(fair, 4),
                            "market_price": tp,
                            "bearish": bearish,
                        },
                    )
        except Exception:
            continue

    return opps
