import asyncio
import csv
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from agent.core import AgentCore
from auth import init_default_users
from auth.manager import (
    create_user, delete_user, list_users, update_user, verify_password,
    update_last_login,
)
from auth.middleware import get_current_user, require_role
from auth.rate_limiter import check as rl_check, clear as rl_clear, record_failure as rl_fail
from auth.tokens import create_token, revoke_token
from logger.trade_logger import TradeLogger
from logger import rejected_logger
from platforms import (
    kalshi_sim, crypto_arb_sim, detective,
    btc_lag_arb, market_maker_sim,
    sniper_30s, maker_spread_sim, sentiment_momentum,
)
import config

_agent: AgentCore = None
_logger: TradeLogger = None


# ── Background tasks ──────────────────────────────────────────────────────────

async def _platform_loop():
    while True:
        try:
            markets = []
            if _agent:
                markets = await _agent.market_data.fetch_active_markets()
            await asyncio.gather(
                kalshi_sim.scan(markets),
                crypto_arb_sim.scan(),
                return_exceptions=True,
            )
        except Exception as exc:
            print(f"[PLATFORMS] {exc}")
        await asyncio.sleep(30)


async def _btc_lag_loop():
    while True:
        try:
            await btc_lag_arb.scan()
        except Exception as exc:
            print(f"[BTC-LAG LOOP] {exc}")
        await asyncio.sleep(10)


async def _market_maker_loop():
    while True:
        try:
            await market_maker_sim.scan()
        except Exception as exc:
            print(f"[MM LOOP] {exc}")
        await asyncio.sleep(30)


async def _sniper_loop():
    while True:
        try:
            await sniper_30s.scan()
        except Exception as exc:
            print(f"[SNIPER LOOP] {exc}")
        await asyncio.sleep(10)


async def _maker_spread_loop():
    while True:
        try:
            await maker_spread_sim.scan()
        except Exception as exc:
            print(f"[MAKER-SPREAD LOOP] {exc}")
        await asyncio.sleep(30)


async def _sentiment_loop():
    while True:
        try:
            await sentiment_momentum.scan()
        except Exception as exc:
            print(f"[SENTIMENT LOOP] {exc}")
        await asyncio.sleep(30)


async def _detective_loop():
    await asyncio.sleep(10)
    while True:
        try:
            await detective.maybe_scan()
        except Exception as exc:
            print(f"[DETECTIVE] {exc}")
        await asyncio.sleep(7200)


async def _exploit_monitor_loop():
    """Write hourly CSV row with exploit strategy performance."""
    csv_path = Path("data/exploit_daily.csv")
    while True:
        await asyncio.sleep(3600)
        try:
            if _agent is None:
                continue
            stats = _agent.get_status().get("strategy_stats", {})
            row = {
                "ts":          datetime.now(timezone.utc).isoformat(),
                "nm_trades":   stats.get("exploit_new_market",    {}).get("trades", 0),
                "nm_wins":     stats.get("exploit_new_market",    {}).get("wins",   0),
                "nm_pnl":      stats.get("exploit_new_market",    {}).get("pnl",    0.0),
                "lt_trades":   stats.get("exploit_liquidity_trap",{}).get("trades", 0),
                "lt_wins":     stats.get("exploit_liquidity_trap",{}).get("wins",   0),
                "lt_pnl":      stats.get("exploit_liquidity_trap",{}).get("pnl",    0.0),
                "balance":     _agent.balance,
                "loop_count":  _agent.loop_count,
            }
            write_header = not csv_path.exists()
            csv_path.parent.mkdir(exist_ok=True)
            with open(csv_path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=row.keys())
                if write_header:
                    w.writeheader()
                w.writerow(row)
        except Exception as exc:
            print(f"[EXPLOIT MONITOR] {exc}")


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent, _logger
    init_default_users()   # create mnoishtat/025951 if not exists
    _logger = TradeLogger()
    _agent  = AgentCore(_logger)
    tasks = [
        asyncio.create_task(_agent.run_loop()),
        asyncio.create_task(_platform_loop()),
        asyncio.create_task(_detective_loop()),
        asyncio.create_task(_exploit_monitor_loop()),
        asyncio.create_task(_btc_lag_loop()),
        asyncio.create_task(_market_maker_loop()),
        asyncio.create_task(_sniper_loop()),
        asyncio.create_task(_maker_spread_loop()),
        asyncio.create_task(_sentiment_loop()),
    ]
    yield
    for t in tasks:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Polymarket Trading Agent", lifespan=lifespan)


# ── Public endpoints (no auth) ────────────────────────────────────────────────

@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "mode": config.TRADING_MODE})


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("dashboard/static/index.html", encoding="utf-8") as f:
        return f.read()


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.post("/auth/login")
async def login(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    ip = request.client.host if request.client else "unknown"

    allowed, retry_after = rl_check(ip)
    if not allowed:
        return JSONResponse(
            {"error": f"Too many attempts. Retry in {retry_after}s"},
            status_code=429,
        )

    user = verify_password(username, password)
    if not user:
        rl_fail(ip)
        return JSONResponse({"error": "Invalid username or password"}, status_code=401)

    rl_clear(ip)
    update_last_login(username)
    token = create_token(username, user["role"])
    resp = JSONResponse({
        "token":      token,
        "username":   username,
        "role":       user["role"],
        "expires_in": 86400,
    })
    resp.set_cookie(
        key="auth_token", value=token,
        httponly=True, secure=True, samesite="strict", max_age=86400,
    )
    print(f"[AUTH] Login: {username} ({user['role']}) from {ip}")
    return resp


@app.post("/auth/logout")
async def logout(request: Request, user: dict = Depends(get_current_user)):
    revoke_token(user["token"])
    resp = JSONResponse({"status": "logged_out", "username": user["username"]})
    resp.delete_cookie("auth_token")
    return resp


@app.get("/auth/me")
async def auth_me(user: dict = Depends(get_current_user)):
    return JSONResponse({"username": user["username"], "role": user["role"]})


# ── Admin: user management ────────────────────────────────────────────────────

@app.get("/admin/users")
async def admin_list_users(_: dict = Depends(require_role("admin"))):
    return JSONResponse(list_users())


@app.post("/admin/users")
async def admin_create_user(request: Request, _: dict = Depends(require_role("admin"))):
    body = await request.json()
    try:
        user = create_user(
            username=body.get("username", "").strip(),
            password=body.get("password", ""),
            role=body.get("role", "viewer"),
        )
        return JSONResponse(user, status_code=201)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.put("/admin/users/{uid}")
async def admin_update_user(uid: str, request: Request, _: dict = Depends(require_role("admin"))):
    body = await request.json()
    try:
        updated = update_user(uid, role=body.get("role"), password=body.get("password"))
        if not updated:
            return JSONResponse({"error": "User not found"}, status_code=404)
        return JSONResponse(updated)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/admin/users/{uid}")
async def admin_delete_user(uid: str, _: dict = Depends(require_role("admin"))):
    try:
        if not delete_user(uid):
            return JSONResponse({"error": "User not found"}, status_code=404)
        return JSONResponse({"status": "deleted"})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ── Protected API endpoints ───────────────────────────────────────────────────

_auth_viewer = Depends(require_role("viewer"))
_auth_trader = Depends(require_role("trader"))
_auth_admin  = Depends(require_role("admin"))


@app.get("/api/status")
async def api_status(_: dict = _auth_viewer):
    if _agent is None:
        return JSONResponse({"error": "not ready"}, status_code=503)
    return JSONResponse(_agent.get_status())


@app.get("/api/trades")
async def api_trades(_: dict = _auth_viewer):
    if _logger is None:
        return JSONResponse([])
    return JSONResponse(_logger.get_recent_trades(50))


@app.get("/api/positions")
async def api_positions(_: dict = _auth_viewer):
    if _agent is None:
        return JSONResponse([])
    return JSONResponse(_agent.get_positions())


@app.get("/api/rejections")
async def api_rejections(_: dict = _auth_viewer):
    return JSONResponse(rejected_logger.get_recent(200))


@app.get("/api/pnl-history")
async def api_pnl_history(_: dict = _auth_viewer):
    if _agent is None:
        return JSONResponse([])
    return JSONResponse(_agent.get_pnl_history())


@app.get("/api/activity")
async def api_activity(_: dict = _auth_viewer):
    if _agent is None:
        return JSONResponse([])
    return JSONResponse(_agent.get_activity(150))


@app.get("/api/platforms/kalshi")
async def api_kalshi(_: dict = _auth_viewer):
    return JSONResponse(kalshi_sim.get_status())


@app.get("/api/platforms/crypto")
async def api_crypto(_: dict = _auth_viewer):
    if time.time() - crypto_arb_sim._cache_ts > crypto_arb_sim.CACHE_TTL:
        try:
            data = await crypto_arb_sim.scan()
            return JSONResponse(data)
        except Exception:
            pass
    return JSONResponse(crypto_arb_sim.get_status())


@app.get("/api/detective")
async def api_detective(_: dict = _auth_viewer):
    return JSONResponse(detective.get_status())


@app.post("/api/detective/scan")
async def api_detective_scan(_: dict = _auth_trader):
    asyncio.create_task(detective.scan())
    return JSONResponse({"status": "scanning"})


@app.get("/api/btc-lag")
async def api_btc_lag(_: dict = _auth_viewer):
    return JSONResponse(btc_lag_arb.get_status())


@app.get("/api/market-maker")
async def api_market_maker(_: dict = _auth_viewer):
    return JSONResponse(market_maker_sim.get_status())


@app.get("/api/sniper")
async def api_sniper(_: dict = _auth_viewer):
    return JSONResponse(sniper_30s.get_status())


@app.get("/api/maker-spread")
async def api_maker_spread(_: dict = _auth_viewer):
    return JSONResponse(maker_spread_sim.get_status())


@app.get("/api/sentiment")
async def api_sentiment(_: dict = _auth_viewer):
    return JSONResponse(sentiment_momentum.get_status())


@app.get("/api/platform-summary")
async def api_platform_summary(_: dict = _auth_viewer):
    """Combined stats across all 3 platforms for the unified header row."""
    poly_balance = _agent.balance if _agent else 0.0
    poly_pnl     = _agent.total_pnl if _agent else 0.0

    ks = kalshi_sim.get_status()
    cs = crypto_arb_sim.get_status()
    bs = btc_lag_arb.get_status()
    ms = market_maker_sim.get_status()

    k_balance = ks.get("balance", kalshi_sim.INITIAL_BALANCE)
    k_pnl     = ks.get("total_pnl", 0.0)
    c_balance = cs.get("balance", crypto_arb_sim.INITIAL_BALANCE)
    c_pnl     = cs.get("total_pnl", 0.0)
    b_balance = bs.get("balance", btc_lag_arb.INITIAL_BALANCE)
    b_pnl     = bs.get("total_pnl", 0.0)
    m_balance = ms.get("balance", market_maker_sim.INITIAL_BALANCE)
    m_pnl     = ms.get("total_pnl", 0.0)

    combined_balance = round(poly_balance + k_balance + c_balance + b_balance + m_balance, 2)
    combined_pnl     = round(poly_pnl    + k_pnl     + c_pnl     + b_pnl     + m_pnl,     2)

    # Per-platform health signal
    def _health(pnl, trades):
        if trades == 0:
            return "waiting"
        return "green" if pnl >= 0 else "red"

    poly_trades = _agent.get_status().get("trades_today", 0) if _agent else 0
    return JSONResponse({
        "combined_balance": combined_balance,
        "combined_pnl":     combined_pnl,
        "platforms": {
            "polymarket": {
                "balance": round(poly_balance, 2),
                "pnl":     round(poly_pnl, 4),
                "trades":  poly_trades,
                "health":  _health(poly_pnl, poly_trades),
                "active_strategy": "copy_trading",
            },
            "kalshi": {
                "balance":    round(k_balance, 2),
                "pnl":        round(k_pnl, 4),
                "trades":     ks.get("trades", 0),
                "health":     _health(k_pnl, ks.get("trades", 0)),
                "threshold":  ks.get("current_threshold", "?"),
                "verdict":    ks.get("verdict", ""),
            },
            "crypto": {
                "balance": round(c_balance, 2),
                "pnl":     round(c_pnl, 4),
                "trades":  cs.get("trades", 0),
                "health":  _health(c_pnl, cs.get("trades", 0)),
                "scan_count": cs.get("scan_count", 0),
            },
            "btc_lag": {
                "balance":   round(b_balance, 2),
                "pnl":       round(b_pnl, 4),
                "trades":    bs.get("trades", 0),
                "health":    _health(b_pnl, bs.get("trades", 0)),
                "btc_price": bs.get("btc_price"),
                "open_positions": len(bs.get("open_positions", [])),
            },
            "market_maker": {
                "balance":        round(m_balance, 2),
                "pnl":            round(m_pnl, 4),
                "trades":         ms.get("trades", 0),
                "health":         _health(m_pnl, ms.get("trades", 0)),
                "open_positions": len(ms.get("open_positions", [])),
                "best_spread":    ms.get("scan_log", [{}])[0].get("best_spread", 0) if ms.get("scan_log") else 0,
            },
        },
    })


@app.get("/api/debug/near-resolution")
async def api_nr_debug(_: dict = _auth_viewer):
    """Per-market scan log for near-resolution strategy."""
    from strategies import near_resolution
    return JSONResponse(near_resolution.get_debug())


@app.get("/api/debug/btc-lag")
async def api_btc_lag_debug(_: dict = _auth_viewer):
    """Detailed scan log for BTC lag arb including per-market expected vs actual prices."""
    status = btc_lag_arb.get_status()
    return JSONResponse({
        "btc_price":     status.get("btc_price"),
        "markets_tracked": status.get("markets_tracked"),
        "scan_count":    status.get("scan_count"),
        "last_scan":     status.get("last_scan"),
        "max_end_hours": btc_lag_arb.MAX_END_DATE_HOURS,
        "min_lag_pct":   btc_lag_arb.MIN_LAG_PCT * 100,
        "scan_log":      status.get("scan_log", []),
        "opportunities": status.get("opportunities", []),
    })


@app.get("/api/kalshi/debug")
async def api_kalshi_debug(_: dict = _auth_viewer):
    """Full Kalshi scan log with per-scan spread details."""
    status = kalshi_sim.get_status()
    return JSONResponse({
        "current_threshold":  status.get("current_threshold"),
        "consecutive_zeros":  status.get("consecutive_zeros"),
        "verdict":            status.get("verdict"),
        "last_scan":          status.get("last_scan"),
        "last_scan_detail":   status.get("last_scan_detail"),
        "scan_log":           status.get("scan_log", []),
        "total_scanned":      status.get("scanned", 0),
    })


@app.get("/api/exploit-log")
async def api_exploit_log(_: dict = _auth_viewer):
    """Return exploit strategy stats + daily CSV rows."""
    stats = {}
    if _agent:
        s = _agent.get_status()
        for name in ["exploit_new_market", "exploit_liquidity_trap"]:
            st = s.get("strategy_stats", {}).get(name, {})
            t  = st.get("trades", 0)
            w  = st.get("wins",   0)
            stats[name] = {
                "trades":   t,
                "wins":     w,
                "pnl":      st.get("pnl", 0.0),
                "win_rate": round(w / t, 4) if t else 0.0,
            }

    csv_rows = []
    csv_path = Path("data/exploit_daily.csv")
    if csv_path.exists():
        with open(csv_path, newline="") as f:
            csv_rows = list(csv.DictReader(f))

    return JSONResponse({
        "date":        datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "strategy_stats": stats,
        "hourly_log":  csv_rows[-48:],  # last 48h
    })


@app.post("/reset")
async def reset_agent(_: dict = Depends(require_role("admin"))):
    if _agent is None:
        return JSONResponse({"error": "not ready"}, status_code=503)
    return JSONResponse(_agent.reset())


@app.post("/golive")
async def go_live(_: dict = Depends(require_role("admin"))):
    os.environ["TRADING_MODE"] = "live"
    config.TRADING_MODE = "live"
    if _agent:
        _agent.mode = "live"
    print("\n[!!!] SWITCHED TO LIVE TRADING — REAL MONEY ACTIVE [!!!]\n")
    return JSONResponse({"status": "live", "warning": "REAL MONEY NOW ACTIVE"})


@app.get("/api/data/{filename}")
async def api_data_file(filename: str, _: dict = _auth_viewer):
    if not re.match(r'^[\w\-]+\.(csv|md)$', filename):
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    path = Path(f"data/{filename}")
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    media = "text/csv" if filename.endswith(".csv") else "text/markdown"
    return FileResponse(str(path), media_type=media, filename=filename)


if __name__ == "__main__":
    print(f"[BOOT] Polymarket Trading Agent | mode={config.TRADING_MODE} | balance=${config.INITIAL_BALANCE}")
    print(f"[BOOT] Dashboard: http://localhost:{config.PORT}")
    if config.TRADING_MODE == "paper":
        print("[BOOT] Running in PAPER TRADING mode.")
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, log_level="warning")
