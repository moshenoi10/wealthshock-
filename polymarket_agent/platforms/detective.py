"""
Detective Mode: reverse-engineer winning patterns on Polymarket.
Fetches public trade data, identifies profitable wallets, detects strategies.
No auth needed — uses Gamma API + CLOB public endpoints only.
"""
import asyncio
import csv
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import httpx

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"
DATA_DIR  = Path("data")
SCAN_TTL  = 7200  # re-scan at most every 2 hours

_last_scan: float = 0.0
_result: dict = {
    "status": "ממתין לסריקה ראשונה",
    "last_scan": None,
    "total_trades": 0,
    "total_markets": 0,
    "total_wallets": 0,
    "patterns": [],
    "top_wallets": [],
    "categories": [],
    "timing_analysis": [],
    "size_analysis": [],
    "top3_patterns": [],
    "worst_patterns": [],
}


# ── Data fetchers ─────────────────────────────────────────────────────────────

async def _fetch_closed_markets(limit: int = 50) -> List[dict]:
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as c:
            r = await c.get(
                f"{GAMMA_API}/markets",
                params={"closed": "true", "order": "volume", "ascending": "false", "limit": limit},
            )
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else data.get("markets", [])
    except Exception as e:
        print(f"[DETECTIVE] fetch markets: {e}")
    return []


async def _fetch_active_markets(limit: int = 30) -> List[dict]:
    try:
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as c:
            r = await c.get(
                f"{GAMMA_API}/markets",
                params={"active": "true", "closed": "false", "order": "volume",
                        "ascending": "false", "limit": limit},
            )
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else data.get("markets", [])
    except Exception as e:
        print(f"[DETECTIVE] fetch active markets: {e}")
    return []


async def _fetch_trades(condition_id: str, limit: int = 200) -> List[dict]:
    for endpoint in [
        f"{CLOB_API}/data/trades",
        f"{CLOB_API}/trades",
    ]:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
                r = await c.get(endpoint, params={"market": condition_id, "limit": limit})
                if r.status_code == 200:
                    data = r.json()
                    trades = data if isinstance(data, list) else data.get("trades", [])
                    if trades:
                        return trades
        except Exception:
            pass
    return []


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_resolution(market: dict) -> Optional[str]:
    import json as _json
    for key in ["resolution", "winner", "winnerOutcome", "outcome", "resolved_outcome"]:
        v = market.get(key)
        if v:
            return str(v).strip().upper()
    # Infer from outcomePrices: the outcome with price ≥ 0.99 won
    try:
        prices   = _json.loads(market.get("outcomePrices") or "[]")
        outcomes = _json.loads(market.get("outcomes") or "[]")
        for i, p in enumerate(prices):
            if float(p) >= 0.99 and i < len(outcomes):
                return outcomes[i].upper()
    except Exception:
        pass
    return None


def _category(question: str) -> str:
    q = question.lower()
    if any(w in q for w in ["btc", "eth", "bitcoin", "ethereum", "crypto", "sol", "xrp", "doge"]):
        return "קריפטו"
    if any(w in q for w in ["trump", "biden", "harris", "president", "election", "senate",
                              "congress", "vote", "poll", "democrat", "republican"]):
        return "פוליטיקה"
    if any(w in q for w in ["nba", "nfl", "mlb", "fifa", "soccer", "basketball", "football",
                              "nhl", "ufc", "olympic", "sport", "game", "match", "championship"]):
        return "ספורט"
    if any(w in q for w in ["fed", "fomc", "inflation", "gdp", "rate", "economy",
                              "recession", "market", "stock", "dow", "s&p"]):
        return "מאקרו"
    return "אחר"


def _normalize_side(raw: str) -> str:
    raw = raw.upper()
    if "YES" in raw or "BUY" in raw:
        return "YES"
    if "NO" in raw or "SELL" in raw:
        return "NO"
    return raw


def _parse_ts(raw) -> Optional[datetime]:
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
        if isinstance(raw, str):
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        pass
    return None


def _wr(total: int, wins: int) -> float:
    return round(wins / total, 3) if total else 0.0


# ── Main scan ─────────────────────────────────────────────────────────────────

async def scan() -> dict:
    global _last_scan, _result

    print("[DETECTIVE] Starting full scan…")

    closed_markets  = await _fetch_closed_markets(50)
    active_markets  = await _fetch_active_markets(20)
    all_markets = closed_markets + active_markets

    if not all_markets:
        _result["status"] = "לא ניתן לאחזר שווקים מ-API"
        return _result

    all_trades: List[dict] = []
    wallet_raw: Dict[str, dict] = defaultdict(lambda: {
        "total_invested": 0.0, "total_returned": 0.0,
        "trades": 0, "wins": 0, "categories": defaultdict(int),
    })
    cat_agg: Dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "invested": 0.0})

    # Timing buckets: [total, wins]
    T = {"<5min": [0,0], "5-30min": [0,0], "30-60min": [0,0], ">1hr": [0,0]}
    S = {"<$10": [0,0], "$10-50": [0,0], "$50-100": [0,0], ">$100": [0,0]}

    scanned = 0

    for market in all_markets[:40]:
        cid      = market.get("conditionId") or market.get("condition_id") or market.get("id", "")
        question = market.get("question", "")
        end_raw  = market.get("endDate") or market.get("end_date_iso") or ""
        cat      = _category(question)
        res      = _get_resolution(market)

        try:
            end_dt = _parse_ts(end_raw) if end_raw else None
        except Exception:
            end_dt = None

        trades = await _fetch_trades(cid, 200)
        if not trades:
            await asyncio.sleep(0.2)
            continue
        scanned += 1

        for t in trades:
            maker   = (t.get("maker_address") or t.get("maker") or t.get("trader") or "").strip()
            side    = _normalize_side(t.get("outcome") or t.get("side") or "")
            price   = float(t.get("price") or 0)
            size    = float(t.get("matched_amount") or t.get("amount") or t.get("size") or 0)
            ts      = _parse_ts(t.get("timestamp") or t.get("created_at") or 0)

            if not maker or price <= 0 or size <= 0:
                continue

            cost = price * size  # dollars invested

            hrs_to_res: Optional[float] = None
            if ts and end_dt:
                delta_h = (end_dt - ts).total_seconds() / 3600
                hrs_to_res = max(delta_h, 0)

            is_win = bool(
                res and side and
                (("YES" in side and "YES" in res) or ("NO" in side and "NO" in res))
            )

            all_trades.append({
                "market_id":         cid[:20],
                "wallet_id":         maker[:10] + "…",
                "wallet_full":       maker,
                "question":          question[:70],
                "category":          cat,
                "side":              side,
                "price":             round(price, 4),
                "size_usd":          round(size, 4),
                "cost":              round(cost, 4),
                "resolution":        res or "",
                "is_win":            is_win,
                "hrs_to_resolution": round(hrs_to_res, 3) if hrs_to_res is not None else "",
                "ts":                ts.isoformat() if ts else "",
            })

            # Wallet aggregation (min $0.50 per trade, min 3 trades later)
            if cost >= 0.5 and maker:
                ws = wallet_raw[maker]
                ws["trades"] += 1
                ws["total_invested"] += cost
                ws["categories"][cat] += 1
                if is_win:
                    ws["wins"] += 1
                    ws["total_returned"] += cost / price  # resolves to $1/share

            # Category aggregation
            cs = cat_agg[cat]
            cs["trades"] += 1
            cs["invested"] += cost
            if is_win:
                cs["wins"] += 1

            # Timing pattern
            if hrs_to_res is not None:
                bk = ("<5min" if hrs_to_res < 0.083 else
                      "5-30min" if hrs_to_res < 0.5 else
                      "30-60min" if hrs_to_res < 1.0 else ">1hr")
                T[bk][0] += 1
                if is_win: T[bk][1] += 1

            # Size pattern
            bk2 = ("<$10" if cost < 10 else "$10-50" if cost < 50 else "$50-100" if cost < 100 else ">$100")
            S[bk2][0] += 1
            if is_win: S[bk2][1] += 1

        await asyncio.sleep(0.25)  # polite rate limiting

    # ── Save CSVs ─────────────────────────────────────────────────────────────
    DATA_DIR.mkdir(exist_ok=True)

    if all_trades:
        fields = ["market_id","wallet_id","question","category","side","price",
                  "size_usd","cost","resolution","is_win","hrs_to_resolution","ts"]
        with open(DATA_DIR / "trades_raw.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader(); w.writerows(all_trades)

    qualified = {addr: ws for addr, ws in wallet_raw.items()
                 if ws["total_invested"] >= 5 and ws["trades"] >= 3}
    sorted_w = sorted(qualified.items(),
                      key=lambda x: x[1]["total_returned"] - x[1]["total_invested"],
                      reverse=True)
    top_wallets = []
    for addr, ws in sorted_w[:50]:
        net = ws["total_returned"] - ws["total_invested"]
        wr  = _wr(ws["trades"], ws["wins"])
        top_cat = max(ws["categories"], key=ws["categories"].get) if ws["categories"] else "?"
        top_wallets.append({
            "wallet":         addr[:12] + "…",
            "trades":         ws["trades"],
            "win_rate":       wr,
            "total_invested": round(ws["total_invested"], 2),
            "net_pnl":        round(net, 2),
            "top_category":   top_cat,
            "is_whale":       bool(net > 50),
        })

    if top_wallets:
        with open(DATA_DIR / "winning_wallets.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=top_wallets[0].keys())
            w.writeheader(); w.writerows(top_wallets)

    # ── Pattern objects ───────────────────────────────────────────────────────

    timing_pats = [
        {"label": "כניסה <5 דקות לפני סגירה",    "win_rate": _wr(*T["<5min"]),    "trades": T["<5min"][0],    "group": "timing"},
        {"label": "כניסה 5-30 דקות לפני סגירה",  "win_rate": _wr(*T["5-30min"]),  "trades": T["5-30min"][0],  "group": "timing"},
        {"label": "כניסה 30-60 דקות לפני סגירה", "win_rate": _wr(*T["30-60min"]), "trades": T["30-60min"][0], "group": "timing"},
        {"label": "כניסה >1 שעה לפני סגירה",     "win_rate": _wr(*T[">1hr"]),     "trades": T[">1hr"][0],     "group": "timing"},
    ]
    size_pats = [
        {"label": "פוזיציה <$10",    "win_rate": _wr(*S["<$10"]),    "trades": S["<$10"][0],    "group": "size"},
        {"label": "פוזיציה $10-50",  "win_rate": _wr(*S["$10-50"]),  "trades": S["$10-50"][0],  "group": "size"},
        {"label": "פוזיציה $50-100", "win_rate": _wr(*S["$50-100"]), "trades": S["$50-100"][0], "group": "size"},
        {"label": "פוזיציה >$100",   "win_rate": _wr(*S[">$100"]),   "trades": S[">$100"][0],   "group": "size"},
    ]
    cat_pats = [
        {"label": cat, "win_rate": _wr(cs["trades"], cs["wins"]),
         "trades": cs["trades"], "group": "category"}
        for cat, cs in sorted(cat_agg.items(), key=lambda x: x[1]["trades"], reverse=True)
        if cs["trades"] >= 5
    ]

    all_pats = timing_pats + size_pats + cat_pats
    all_pats.sort(key=lambda p: p["win_rate"], reverse=True)

    top3    = [p for p in all_pats if p["trades"] >= 10][:3]
    worst3  = [p for p in reversed(all_pats) if p["trades"] >= 10][:3]

    # ── PATTERNS_FOUND.md ─────────────────────────────────────────────────────
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# דפוסים מנצחים ב-Polymarket ({now_str})",
        f"\nשווקים שנסרקו: **{scanned}** | עסקאות: **{len(all_trades)}** | "
        f"ארנקים (≥$5 ≥3 עסקאות): **{len(qualified)}**\n",
        "## 3 הדפוסים המנצחים ביותר\n",
    ]
    for i, p in enumerate(top3, 1):
        lines.append(f"{i}. **{p['label']}** → {p['win_rate']*100:.0f}% הצלחה ({p['trades']} עסקאות)")
    lines += ["", "## דפוסים גרועים (להימנע)\n"]
    for p in worst3:
        lines.append(f"- **{p['label']}** → {p['win_rate']*100:.0f}% הצלחה ({p['trades']} עסקאות)")
    lines += ["", "## ניתוח לפי קטגוריה\n"]
    for p in cat_pats:
        lines.append(f"- **{p['label']}**: {p['win_rate']*100:.0f}% הצלחה, {p['trades']} עסקאות")
    lines += ["", "## קבצי נתונים שנשמרו", "- `data/trades_raw.csv`", "- `data/winning_wallets.csv`"]

    with open(DATA_DIR / "PATTERNS_FOUND.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ── Cache result ──────────────────────────────────────────────────────────
    _last_scan = time.time()
    _result = {
        "status":         "הושלם",
        "last_scan":      datetime.now(timezone.utc).isoformat(),
        "total_trades":   len(all_trades),
        "total_markets":  scanned,
        "total_wallets":  len(qualified),
        "patterns":       all_pats[:20],
        "top_wallets":    top_wallets[:20],
        "categories":     [
            {"category": cat, "win_rate": _wr(cs["trades"], cs["wins"]),
             "trades": cs["trades"], "invested": round(cs["invested"], 2)}
            for cat, cs in sorted(cat_agg.items(), key=lambda x: x[1]["trades"], reverse=True)
        ],
        "timing_analysis": timing_pats,
        "size_analysis":   size_pats,
        "top3_patterns":   top3,
        "worst_patterns":  worst3,
    }
    print(f"[DETECTIVE] Done: {scanned} markets, {len(all_trades)} trades, "
          f"{len(qualified)} wallets, {len(all_pats)} patterns")
    return _result


def get_status() -> dict:
    return _result


async def maybe_scan():
    """Trigger scan if results are stale (>2 hours)."""
    if time.time() - _last_scan > SCAN_TTL:
        await scan()
