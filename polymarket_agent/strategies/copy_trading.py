"""
Strategy 6 — Copy Trading
Score top-10 profitable wallets by win rate + PnL + recency using poly_data and
the Polymarket API. Mirror their open positions. Stop following after 3 losses.
Wallet rankings refresh daily.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx

import config
from strategies.base import Opportunity

NAME = "copy_trading"
TOP_N = 10
MIN_WIN_RATE = 0.55
MIN_TRADES = 20
MAX_CONSECUTIVE_LOSSES = 3
RECENCY_DAYS = 30

_wallet_scores: Dict[str, dict] = {}
_loss_streak: Dict[str, int] = {}
_last_score_update: Optional[datetime] = None


async def _fetch_leaderboard() -> List[str]:
    """Pull top wallets from Polymarket leaderboard."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://gamma-api.polymarket.com/leaderboard",
                params={"limit": 50, "window": "all"},
            )
            items = resp.json() if resp.status_code == 200 else []
            if isinstance(items, list):
                return [it.get("proxyWallet") or it.get("address") for it in items if it.get("proxyWallet") or it.get("address")]
    except Exception as exc:
        print(f"[COPY] Leaderboard error: {exc}")
    return []


async def _score_wallet(wallet: str) -> Optional[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENCY_DAYS)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{config.POLYMARKET_HOST}/data/trade-history",
                params={"maker": wallet, "limit": 200},
            )
            if resp.status_code != 200:
                return None
            trades = resp.json().get("history", [])

        if len(trades) < MIN_TRADES:
            return None

        recent = [
            t for t in trades
            if datetime.fromisoformat(
                (t.get("timestamp") or "2000-01-01T00:00:00Z").replace("Z", "+00:00")
            ) > cutoff
        ]
        if len(recent) < 5:
            return None

        wins = sum(1 for t in recent if float(t.get("price") or 0) > 0.5)
        win_rate = wins / len(recent)
        if win_rate < MIN_WIN_RATE:
            return None

        return {
            "wallet": wallet,
            "win_rate": win_rate,
            "trade_count": len(recent),
            "score": win_rate * (len(recent) ** 0.3),
        }
    except Exception:
        return None


async def update_wallet_scores():
    global _last_score_update
    print("[COPY] Refreshing wallet scores…")
    wallets = await _fetch_leaderboard()
    if not wallets:
        print("[COPY] No wallets from leaderboard")
        return

    results = await asyncio.gather(*[_score_wallet(w) for w in wallets[:30]], return_exceptions=True)
    scored = sorted(
        [r for r in results if r and not isinstance(r, Exception)],
        key=lambda x: x["score"],
        reverse=True,
    )[:TOP_N]

    _wallet_scores.clear()
    for w in scored:
        _wallet_scores[w["wallet"]] = w

    config.DATA_DIR.mkdir(exist_ok=True)
    config.WALLET_SCORES_FILE.write_text(json.dumps(scored, indent=2))
    _last_score_update = datetime.now(timezone.utc)
    print(f"[COPY] Scored {len(scored)} top wallets")


def _load_cached_scores():
    if _wallet_scores:
        return
    if config.WALLET_SCORES_FILE.exists():
        try:
            for w in json.loads(config.WALLET_SCORES_FILE.read_text()):
                _wallet_scores[w["wallet"]] = w
        except Exception:
            pass


def record_loss(token_id: str, wallet: str):
    _loss_streak[wallet] = _loss_streak.get(wallet, 0) + 1
    if _loss_streak[wallet] >= MAX_CONSECUTIVE_LOSSES:
        print(f"[COPY] Dropping wallet {wallet[:8]}… ({MAX_CONSECUTIVE_LOSSES} consecutive losses)")
        _wallet_scores.pop(wallet, None)


def record_win(wallet: str):
    _loss_streak[wallet] = 0


async def run(markets: List[dict], positions: dict, balance: float) -> List[Opportunity]:
    global _last_score_update

    _load_cached_scores()

    # Refresh rankings daily
    if _last_score_update is None or (
        datetime.now(timezone.utc) - _last_score_update
    ).total_seconds() > 86_400:
        asyncio.create_task(update_wallet_scores())

    if not _wallet_scores:
        return []

    market_map = {m.get("condition_id"): m for m in markets}
    opps: List[Opportunity] = []

    for wallet, score in _wallet_scores.items():
        if _loss_streak.get(wallet, 0) >= MAX_CONSECUTIVE_LOSSES:
            continue

        try:
            async with httpx.AsyncClient(timeout=12) as client:
                resp = await client.get(
                    f"{config.POLYMARKET_HOST}/data/positions",
                    params={"user": wallet},
                )
                wallet_positions = resp.json().get("positions", []) if resp.status_code == 200 else []
        except Exception:
            continue

        for pos in wallet_positions:
            cid = pos.get("conditionId") or pos.get("condition_id")
            market = market_map.get(cid)
            if not market:
                continue

            tid = pos.get("tokenId") or pos.get("token_id")
            size = float(pos.get("size") or 0)
            if not tid or size < 1:
                continue

            token = next((t for t in market.get("tokens", []) if t.get("token_id") == tid), None)
            if not token:
                continue

            price = float(token.get("price") or 0)
            if price <= 0:
                continue

            opps.append(Opportunity(
                strategy=NAME,
                market_id=market.get("id", ""),
                condition_id=cid,
                token_id=tid,
                side="BUY",
                price=price,
                size=min(balance * config.MAX_POSITION_PCT, 5.0),
                ev=score["win_rate"],
                reasoning=(
                    f"Copy {wallet[:8]}… "
                    f"win_rate={score['win_rate']:.1%} "
                    f"score={score['score']:.2f}"
                ),
                market_question=market.get("question", "")[:120],
            ))

    return opps[:5]
