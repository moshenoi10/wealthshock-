"""
Strategy 6 — Copy Trading
Score top-10 profitable wallets by win rate + recency (via Polymarket leaderboard).
Mirror their open positions. Stop following after 3 consecutive losses.
Rankings refresh daily.

Thresholds (loosened 2026-06-14):
  MIN_WIN_RATE: 0.44  (was 0.55  — ×0.8)
  MIN_TRADES:   16    (was 20    — ×0.8)
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx

import config
from logger import rejected_logger
from strategies.base import Opportunity

NAME = "copy_trading"
TOP_N = 10
MIN_WIN_RATE          = config.COPY_TRADING_MIN_WIN_RATE   # 0.44
MIN_TRADES            = config.COPY_TRADING_MIN_TRADES      # 16
MAX_CONSECUTIVE_LOSSES = 3
RECENCY_DAYS          = 30
MIN_PROFIT_FACTOR     = 1.3    # total win-side value / total loss-side value
MIN_VOLUME_USD        = 50.0   # ignore wallets with < $50 estimated volume

_wallet_scores: Dict[str, dict] = {}
_loss_streak: Dict[str, int] = {}
_last_score_update: Optional[datetime] = None


async def _fetch_leaderboard() -> List[str]:
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(
                "https://gamma-api.polymarket.com/leaderboard",
                params={"limit": 50, "window": "all"},
            )
            items = resp.json() if resp.status_code == 200 else []
            if isinstance(items, list):
                return [
                    it.get("proxyWallet") or it.get("address")
                    for it in items
                    if it.get("proxyWallet") or it.get("address")
                ]
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
            # Near-miss: wallet exists but too few trades
            if trades:
                rejected_logger.log(
                    source=NAME,
                    reason="insufficient_trades",
                    market_question=f"wallet {wallet[:12]}",
                    token_id=wallet,
                    value=float(len(trades)),
                    threshold=float(MIN_TRADES),
                    extra={"wallet": wallet},
                )
            return None

        recent = [
            t for t in trades
            if datetime.fromisoformat(
                (t.get("timestamp") or "2000-01-01T00:00:00Z").replace("Z", "+00:00")
            ) > cutoff
        ]
        if len(recent) < 5:
            return None

        # --- Profit factor (Smart Money pattern): weight wins vs losses by price distance ---
        # A trade is a "strong win" if entry price was low (<0.5) and market resolved YES,
        # or entry was high (>0.5) — proxy: use price deviation from 0.5 as conviction weight.
        win_value   = 0.0
        loss_value  = 0.0
        win_count   = 0
        total_vol   = 0.0
        for t in recent:
            price = float(t.get("price") or 0)
            size  = float(t.get("size") or t.get("amount") or 1.0)
            if price <= 0 or price >= 1:
                continue
            total_vol += price * size
            if price > 0.5:
                # Buying YES when market thinks it's likely → lower conviction win
                win_count  += 1
                win_value  += (1.0 - price) * size  # potential upside is small
            else:
                # Buying YES when market is skeptical → contrarian, higher conviction
                win_count  += 1
                win_value  += (1.0 - price) * size

        # Rough loss proxy: trades that resolved against the buyer (use price > 0.8 as overconfident)
        for t in recent:
            price = float(t.get("price") or 0)
            size  = float(t.get("size") or t.get("amount") or 1.0)
            if price > 0.8:  # bought near-certainty, likely overfit
                loss_value += price * size

        win_rate = win_count / len(recent)

        if win_rate < MIN_WIN_RATE:
            rejected_logger.log(
                source=NAME,
                reason="win_rate_below_threshold",
                market_question=f"wallet {wallet[:12]}",
                token_id=wallet,
                value=win_rate,
                threshold=MIN_WIN_RATE,
                extra={"wallet": wallet, "trades": len(recent)},
            )
            return None

        # Profit factor: total value captured on wins / value burned on losses
        profit_factor = win_value / max(loss_value, 0.01)
        if profit_factor < MIN_PROFIT_FACTOR:
            rejected_logger.log(
                source=NAME,
                reason="profit_factor_below_threshold",
                market_question=f"wallet {wallet[:12]}",
                token_id=wallet,
                value=profit_factor,
                threshold=MIN_PROFIT_FACTOR,
                extra={"wallet": wallet, "profit_factor": round(profit_factor, 3)},
            )
            return None

        # Volume floor: ignore micro-traders
        if total_vol < MIN_VOLUME_USD:
            return None

        return {
            "wallet":        wallet,
            "win_rate":      win_rate,
            "profit_factor": round(profit_factor, 3),
            "trade_count":   len(recent),
            "total_vol":     round(total_vol, 2),
            # Score: win rate × profit factor × log(trade_count) — balances quality vs quantity
            "score": win_rate * profit_factor * (len(recent) ** 0.3),
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
    print(f"[COPY] Scored {len(scored)} top wallets (min_win_rate={MIN_WIN_RATE:.0%}, min_trades={MIN_TRADES})")


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
                    f"wr={score['win_rate']:.1%} "
                    f"pf={score.get('profit_factor', '?')} "
                    f"score={score['score']:.2f}"
                ),
                market_question=market.get("question", "")[:120],
            ))

    return opps[:5]
