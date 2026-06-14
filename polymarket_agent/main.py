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
from platforms import kalshi_sim, crypto_arb_sim, detective
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
