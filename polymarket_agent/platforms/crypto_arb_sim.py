"""
Crypto arbitrage simulator — paper trading only, no API keys needed.
Fetches live public prices from Binance, Kraken, Coinbase every 30s.
Simulates a buy/sell trade whenever same pair shows spread > 0.03%.

DEBUG: detailed logging added — every scan prints prices + spread.
"""
import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
MIN_SPREAD = 0.0002     # 0.02% — ultra-conservative to catch real spreads
MAX_SPREAD = 0.05       # 5% cap — stale-data guard
INITIAL_BALANCE = 100.0
MAX_POSITION_PCT = 0.20
CACHE_TTL = 30

_balance: float = INITIAL_BALANCE
_total_pnl: float = 0.0
_wins: int = 0
_sim_trades: deque = deque(maxlen=200)
_opps_found: int = 0
_price_cache: Dict[str, dict] = {}
_price_comparison_cache: List[dict] = []
_cache_ts: float = 0.0
_scan_count: int = 0
_last_scan_ts: Optional[str] = None
_scan_log: deque = deque(maxlen=50)


# ── Pair → exchange symbols ───────────────────────────────────────────────────
# Kraken uses XBT for Bitcoin; Coinbase uses -USD suffix (not -USDT)
_SYMBOLS = {
    "BTC/USDT": ("BTCUSDT",  "XBTUSD",   "BTC-USD"),
    "ETH/USDT": ("ETHUSDT",  "ETHUSD",   "ETH-USD"),
    "SOL/USDT": ("SOLUSDT",  "SOLUSD",   "SOL-USD"),
}


# ── Price fetchers ────────────────────────────────────────────────────────────

async def _fetch_binance(symbol: str) -> Optional[float]:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://api.binance.com/api/v3/ticker/price",
                params={"symbol": symbol},
            )
            if r.status_code == 200:
                return float(r.json()["price"])
            print(f"[CRYPTO] Binance {symbol}: HTTP {r.status_code}")
    except Exception as e:
        print(f"[CRYPTO] Binance {symbol}: {type(e).__name__}: {e}")
    return None


async def _fetch_kraken(symbol: str) -> Optional[float]:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://api.kraken.com/0/public/Ticker",
                params={"pair": symbol},
            )
            if r.status_code == 200:
                data = r.json()
                if not data.get("error"):
                    result = data.get("result", {})
                    if result:
                        ticker = next(iter(result.values()))
                        return float(ticker["c"][0])
                else:
                    print(f"[CRYPTO] Kraken {symbol}: {data['error']}")
    except Exception as e:
        print(f"[CRYPTO] Kraken {symbol}: {type(e).__name__}: {e}")
    return None


async def _fetch_coinbase(product_id: str) -> Optional[float]:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.coinbase.com/v2/prices/{product_id}/spot")
            if r.status_code == 200:
                return float(r.json()["data"]["amount"])
            print(f"[CRYPTO] Coinbase {product_id}: HTTP {r.status_code}")
    except Exception as e:
        print(f"[CRYPTO] Coinbase {product_id}: {type(e).__name__}: {e}")
    return None


async def _fetch_all_prices() -> Dict[str, dict]:
    global _price_cache, _cache_ts

    if time.time() - _cache_ts < CACHE_TTL:
        return _price_cache

    fetches = []
    for pair in PAIRS:
        bn, kr, cb = _SYMBOLS[pair]
        fetches.append(asyncio.gather(
            _fetch_binance(bn),
            _fetch_kraken(kr),
            _fetch_coinbase(cb),
            return_exceptions=True,
        ))

    results = await asyncio.gather(*fetches, return_exceptions=True)
    prices: Dict[str, dict] = {}

    for i, pair in enumerate(PAIRS):
        res = results[i] if not isinstance(results[i], Exception) else [None, None, None]
        if isinstance(res, Exception):
            res = [None, None, None]
        bn_p, kr_p, cb_p = (
            (res[0] if not isinstance(res[0], Exception) else None),
            (res[1] if not isinstance(res[1], Exception) else None),
            (res[2] if not isinstance(res[2], Exception) else None),
        )
        prices[pair] = {
            "binance":  round(float(bn_p), 2) if bn_p else None,
            "kraken":   round(float(kr_p), 2) if kr_p else None,
            "coinbase": round(float(cb_p), 2) if cb_p else None,
        }

    _price_cache = prices
    _cache_ts = time.time()
    return prices


def _find_arb(pair: str, p: dict) -> Optional[dict]:
    ex = {k: v for k, v in {
        "Binance": p.get("binance"),
        "Kraken":  p.get("kraken"),
        "Coinbase": p.get("coinbase"),
    }.items() if v and v > 0}

    if len(ex) < 2:
        return None

    buy_ex  = min(ex, key=ex.get)
    sell_ex = max(ex, key=ex.get)
    buy_p, sell_p = ex[buy_ex], ex[sell_ex]
    spread = (sell_p - buy_p) / buy_p

    print(f"[CRYPTO] {pair}: {buy_ex}=${buy_p:.2f} {sell_ex}=${sell_p:.2f} spread={spread*100:.4f}%")

    if spread < MIN_SPREAD:
        return None
    if spread > MAX_SPREAD:
        print(f"[CRYPTO] {pair}: spread {spread*100:.2f}% > max {MAX_SPREAD*100:.0f}% — stale data guard")
        return None

    size    = min(_balance * MAX_POSITION_PCT, 20.0)
    est_pnl = round(size * spread * 0.65, 4)
    return {
        "pair":          pair,
        "buy_exchange":  buy_ex,
        "sell_exchange": sell_ex,
        "buy_price":     round(buy_p,  2),
        "sell_price":    round(sell_p, 2),
        "spread_pct":    round(spread, 6),
        "size":          round(size,   2),
        "estimated_pnl": est_pnl,
    }


async def scan() -> dict:
    global _balance, _total_pnl, _wins, _opps_found
    global _price_comparison_cache, _scan_count, _last_scan_ts

    _scan_count += 1
    ts = datetime.now(timezone.utc).isoformat()
    _last_scan_ts = ts

    prices   = await _fetch_all_prices()
    arb_opps: List[dict] = []
    comparison: List[dict] = []

    prices_ok = sum(
        1 for pair in PAIRS
        for v in prices.get(pair, {}).values() if v
    )
    print(f"[CRYPTO] Scan #{_scan_count} — {prices_ok}/{len(PAIRS)*3} prices fetched")

    for pair in PAIRS:
        p    = prices.get(pair, {})
        vals = [v for v in [p.get("binance"), p.get("kraken"), p.get("coinbase")] if v]
        max_spread = round((max(vals) - min(vals)) / min(vals), 6) if len(vals) >= 2 else 0
        comparison.append({
            "pair":       pair,
            "binance":    p.get("binance"),
            "kraken":     p.get("kraken"),
            "coinbase":   p.get("coinbase"),
            "max_spread": max_spread,
        })

        arb = _find_arb(pair, p)
        if arb:
            _opps_found += 1
            arb_opps.append(arb)
            pnl          = arb["estimated_pnl"]
            _balance     = round(_balance    + pnl, 4)
            _total_pnl   = round(_total_pnl  + pnl, 4)
            if pnl > 0:
                _wins += 1
            _sim_trades.appendleft({
                "ts":            ts,
                "pair":          pair,
                "buy_exchange":  arb["buy_exchange"],
                "sell_exchange": arb["sell_exchange"],
                "buy_price":     arb["buy_price"],
                "sell_price":    arb["sell_price"],
                "spread_pct":    arb["spread_pct"],
                "size":          arb["size"],
                "pnl":           pnl,
            })
            print(f"[CRYPTO] ✓ Arb trade: {pair} {arb['buy_exchange']}→{arb['sell_exchange']} "
                  f"spread={arb['spread_pct']*100:.4f}% pnl=${pnl:.4f}")

    scan_entry = {
        "ts": ts, "scan": _scan_count, "prices_ok": prices_ok,
        "arb_found": len(arb_opps), "threshold": f"{MIN_SPREAD*100:.3f}%",
    }
    _scan_log.appendleft(scan_entry)

    _price_comparison_cache = comparison
    return _build_status(comparison, arb_opps)


def _build_status(comparison: List[dict], arb_opps: List[dict]) -> dict:
    total = len(_sim_trades)
    return {
        "balance":             round(_balance,   4),
        "total_pnl":           round(_total_pnl, 4),
        "trades":              total,
        "wins":                _wins,
        "win_rate":            round(_wins / total, 4) if total else 0.0,
        "opportunities_found": _opps_found,
        "scan_count":          _scan_count,
        "last_scan":           _last_scan_ts,
        "min_spread_pct":      f"{MIN_SPREAD*100:.3f}%",
        "price_comparison":    comparison,
        "arb_opportunities":   arb_opps,
        "scan_log":            list(_scan_log)[:10],
        "sim_trades":          list(_sim_trades)[:30],
    }


def get_status() -> dict:
    return _build_status(_price_comparison_cache, [])
