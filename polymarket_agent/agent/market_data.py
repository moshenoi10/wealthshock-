import asyncio
import json
from typing import List

import httpx

import config

GAMMA_HOST = "https://gamma-api.polymarket.com"
PAGE_SIZE = 100
MAX_PAGES = 5  # up to 500 active markets per tick


def _normalize(raw: dict) -> dict:
    """Convert Gamma API market format to the internal format strategies expect."""
    outcomes = json.loads(raw.get("outcomes") or "[]")
    prices = json.loads(raw.get("outcomePrices") or "[]")
    token_ids = json.loads(raw.get("clobTokenIds") or "[]")

    tokens = []
    for i, outcome in enumerate(outcomes):
        tokens.append({
            "token_id": token_ids[i] if i < len(token_ids) else "",
            "outcome": outcome,
            "price": prices[i] if i < len(prices) else "0",
        })

    return {
        "id": raw.get("id", raw.get("conditionId", "")),
        "condition_id": raw.get("conditionId", ""),
        "question": raw.get("question", ""),
        "end_date_iso": raw.get("endDate", ""),
        "active": raw.get("active", False),
        "closed": raw.get("closed", True),
        "accepting_orders": raw.get("enableOrderBook", False),
        "volume": raw.get("volume"),
        "liquidity": raw.get("liquidity"),
        "created_at": raw.get("createdAt") or raw.get("created_at") or raw.get("startDate"),
        "tokens": tokens,
    }


class MarketDataFetcher:
    def __init__(self):
        self._client = httpx.AsyncClient(timeout=25)

    async def fetch_active_markets(self) -> List[dict]:
        markets: List[dict] = []
        offset = 0

        for _ in range(MAX_PAGES):
            try:
                resp = await self._client.get(
                    f"{GAMMA_HOST}/markets",
                    params={
                        "active": "true",
                        "closed": "false",
                        "limit": PAGE_SIZE,
                        "offset": offset,
                        "order": "volume",
                        "ascending": "false",
                    },
                )
                resp.raise_for_status()
                batch = resp.json()
                if not isinstance(batch, list):
                    batch = batch.get("data", [])

                if not batch:
                    break

                for raw in batch:
                    if not raw.get("enableOrderBook"):
                        continue
                    m = _normalize(raw)
                    liq = float(m.get("liquidity") or m.get("volume") or 0)
                    if liq >= config.MIN_LIQUIDITY:
                        markets.append(m)

                offset += PAGE_SIZE
                if len(batch) < PAGE_SIZE:
                    break
            except Exception as exc:
                print(f"[MARKET] Fetch error: {exc}")
                break

        return markets

    async def get_price_history(
        self, token_id: str, interval: str = "5m", fidelity: int = 5
    ) -> List[dict]:
        try:
            resp = await self._client.get(
                f"{config.POLYMARKET_HOST}/prices-history",
                params={"market": token_id, "interval": interval, "fidelity": fidelity},
            )
            return resp.json().get("history", [])
        except Exception:
            return []

    async def get_order_book(self, token_id: str) -> dict:
        try:
            resp = await self._client.get(
                f"{config.POLYMARKET_HOST}/book",
                params={"token_id": token_id},
            )
            return resp.json()
        except Exception:
            return {}

    async def get_trades_for_wallet(self, wallet: str, limit: int = 100) -> List[dict]:
        try:
            resp = await self._client.get(
                f"{config.POLYMARKET_HOST}/data/trade-history",
                params={"maker": wallet, "limit": limit},
            )
            return resp.json().get("history", [])
        except Exception:
            return []

    async def get_positions_for_wallet(self, wallet: str) -> List[dict]:
        try:
            resp = await self._client.get(
                f"{config.POLYMARKET_HOST}/data/positions",
                params={"user": wallet},
            )
            return resp.json().get("positions", [])
        except Exception:
            return []
