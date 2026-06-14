"""
Crypto arbitrage simulator — paper trading only, no API keys needed.
Fetches public prices from Binance, Kraken, Coinbase and finds
inter-exchange price spreads. Simulates trades when spread > 0.1%.
"""
import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import httpx

PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
MIN_SPREAD = 0.001   # 0.1% minimum spread to simulate a trade
MAX_SPREAD = 0.05    # cap at 5% (stale data guard)
INITIAL_BALANCE = 100.0
MAX_POSITION_PCT = 0.15
CACHE_TTL = 30  # seconds

_balance: float = INITIAL_BALANCE
_total_pnl: float = 0.0
_sim_trades: deque = deque(maxlen=200)
_opps_found: int = 0
_price_cache: Dict[str, dict] = {}
_cache_ts: float = 0.0


async def _fetch_binance(pair: str) -> Optional[float]:
    symbol = pair.replace("/", "")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
            if r.status_code == 200:
                return float(r.json()["price"])
    except Exception:
        pass
    return None


async def _fetch_kraken(pair: str) -> Optional[float]:
    # Kraken uses different symbol format
    kraken_map = {"BTC/USDT": "XBTUSDT", "ETH/USDT": "ETHUSDT", "SOL/USDT": "SOLUSDT"}
    symbol = kraken_map.get(pair, pair.replace("/", ""))
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.kraken.com/0/public/Ticker?pair={symbol}")
            if r.status_code == 200:
                data = r.json()
                if not data.get("error"):
                    result = data.get("result", {})
                    if result:
                        ticker = next(iter(result.values()))
                        return float(ticker["c"][0])  # last close price
    except Exception:
        pass
    return None


async def _fetch_coinbase(pair: str) -> Optional[float]:
    cb_pair = pair.replace("/USDT", "-USD").replace("/", "-")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.coinbase.com/v2/prices/{cb_pair}/spot")
            if r.status_code == 200:
                return float(r.json()["data"]["amount"])
    except Exception:
        pass
    return None


async def _fetch_all_prices() -> Dict[str, dict]:
    global _price_cache, _cache_ts
    if time.time() - _cache_ts < CACHE_TTL:
        return _price_cache

    results = await asyncio.gather(
        *[
            asyncio.gather(
                _fetch_binance(p),
                _fetch_kraken(p),
                _fetch_coinbase(p),
            )
            for p in PAIRS
        ],
        return_exceptions=True,
    )

    prices = {}
    for i, pair in enumerate(PAIRS):
        res = results[i] if not isinstance(results[i], Exception) else [None, None, None]
        binance, kraken, coinbase = (res[0], res[1], res[2]) if isinstance(res, (list, tuple)) else (None, None, None)
        prices[pair] = {
            "binance": round(binance, 4) if binance else None,
            "kraken": round(kraken, 4) if kraken else None,
            "coinbase": round(coinbase, 4) if coinbase else None,
        }

    _price_cache = prices
    _cache_ts = time.time()
    return prices


def _find_arb(pair: str, prices: dict) -> Optional[dict]:
    """Find best arb opportunity for a pair across the 3 exchanges."""
    exchange_prices = {
        "Binance": prices.get("binance"),
        "Kraken": prices.get("kraken"),
        "Coinbase": prices.get("coinbase"),
    }
    valid = {k: v for k, v in exchange_prices.items() if v and v > 0}
    if len(valid) < 2:
        return None

    buy_ex = min(valid, key=valid.get)
    sell_ex = max(valid, key=valid.get)
    buy_price = valid[buy_ex]
    sell_price = valid[sell_ex]
    spread_pct = (sell_price - buy_price) / buy_price

    if spread_pct < MIN_SPREAD or spread_pct > MAX_SPREAD:
        return None

    size = min(_balance * MAX_POSITION_PCT, 20.0)
    return {
        "pair": pair,
        "buy_exchange": buy_ex,
        "sell_exchange": sell_ex,
        "buy_price": round(buy_price, 4),
        "sell_price": round(sell_price, 4),
        "spread_pct": round(spread_pct, 6),
        "size": round(size, 2),
        "estimated_pnl": round(size * spread_pct * 0.7, 4),  # 70% efficiency
    }


def get_status() -> dict:
    return {
        "balance": round(_balance, 4),
        "total_pnl": round(_total_pnl, 4),
        "opportunities_found": _opps_found,
        "trades": len(_sim_trades),
        "price_comparison": [
            {
                "pair": p,
                "binance": _price_cache.get(p, {}).get("binance"),
                "kraken": _price_cache.get(p, {}).get("kraken"),
                "coinbase": _price_cache.get(p, {}).get("coinbase"),
                "max_spread": 0,
            }
            for p in PAIRS
        ],
        "arb_opportunities": [],
        "sim_trades": list(_sim_trades)[:30],
    }


async def scan() -> dict:
    global _balance, _total_pnl, _opps_found

    prices = await _fetch_all_prices()

    arb_opps = []
    price_comparison = []

    for pair in PAIRS:
        p = prices.get(pair, {})
        vals = [v for v in [p.get("binance"), p.get("kraken"), p.get("coinbase")] if v]
        max_spread = (max(vals) - min(vals)) / min(vals) if len(vals) >= 2 else 0
        price_comparison.append({
            "pair": pair,
            "binance": p.get("binance"),
            "kraken": p.get("kraken"),
            "coinbase": p.get("coinbase"),
            "max_spread": round(max_spread, 6),
        })

        arb = _find_arb(pair, p)
        if arb:
            _opps_found += 1
            arb_opps.append(arb)

            # Simulate trade
            pnl = arb["estimated_pnl"]
            _balance = round(_balance + pnl, 4)
            _total_pnl = round(_total_pnl + pnl, 4)
            _sim_trades.appendleft({
                "ts": datetime.now(timezone.utc).isoformat(),
                "pair": pair,
                "buy_exchange": arb["buy_exchange"],
                "sell_exchange": arb["sell_exchange"],
                "buy_price": arb["buy_price"],
                "sell_price": arb["sell_price"],
                "spread_pct": arb["spread_pct"],
                "size": arb["size"],
                "pnl": pnl,
            })

    return {
        "balance": round(_balance, 4),
        "total_pnl": round(_total_pnl, 4),
        "opportunities_found": _opps_found,
        "trades": len(_sim_trades),
        "price_comparison": price_comparison,
        "arb_opportunities": arb_opps,
        "sim_trades": list(_sim_trades)[:30],
    }
