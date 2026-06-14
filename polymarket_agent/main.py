import asyncio
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from agent.core import AgentCore
from logger.trade_logger import TradeLogger
from logger import rejected_logger
from platforms import kalshi_sim, crypto_arb_sim
import config

_agent: AgentCore = None
_logger: TradeLogger = None


async def _platform_loop():
    """Background loop scanning Kalshi + crypto arb every 60s."""
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
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent, _logger
    _logger = TradeLogger()
    _agent = AgentCore(_logger)
    task = asyncio.create_task(_agent.run_loop())
    plat_task = asyncio.create_task(_platform_loop())
    yield
    task.cancel()
    plat_task.cancel()
    for t in (task, plat_task):
        try:
            await t
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Polymarket Trading Agent", lifespan=lifespan)


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok", "mode": config.TRADING_MODE})


@app.get("/api/status")
async def api_status():
    if _agent is None:
        return JSONResponse({"error": "not ready"}, status_code=503)
    return JSONResponse(_agent.get_status())


@app.get("/api/trades")
async def api_trades():
    if _logger is None:
        return JSONResponse([])
    return JSONResponse(_logger.get_recent_trades(50))


@app.get("/api/positions")
async def api_positions():
    if _agent is None:
        return JSONResponse([])
    return JSONResponse(_agent.get_positions())


@app.get("/api/rejections")
async def api_rejections():
    return JSONResponse(rejected_logger.get_recent(200))


@app.get("/api/pnl-history")
async def api_pnl_history():
    if _agent is None:
        return JSONResponse([])
    return JSONResponse(_agent.get_pnl_history())


@app.get("/api/activity")
async def api_activity():
    if _agent is None:
        return JSONResponse([])
    return JSONResponse(_agent.get_activity(150))


@app.get("/api/platforms/kalshi")
async def api_kalshi():
    return JSONResponse(kalshi_sim.get_status())


@app.get("/api/platforms/crypto")
async def api_crypto():
    return JSONResponse(crypto_arb_sim.get_status())


@app.post("/golive")
async def go_live():
    os.environ["TRADING_MODE"] = "live"
    config.TRADING_MODE = "live"
    if _agent:
        _agent.mode = "live"
    print("\n[!!!] SWITCHED TO LIVE TRADING — REAL MONEY ACTIVE [!!!]\n")
    return JSONResponse({"status": "live", "warning": "REAL MONEY NOW ACTIVE"})


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    with open("dashboard/static/index.html") as f:
        return f.read()


if __name__ == "__main__":
    print(f"[BOOT] Polymarket Trading Agent | mode={config.TRADING_MODE} | balance=${config.INITIAL_BALANCE}")
    print(f"[BOOT] Dashboard: http://localhost:{config.PORT}")
    print(f"[BOOT] Health:    http://localhost:{config.PORT}/health")
    if config.TRADING_MODE == "paper":
        print("[BOOT] Running in PAPER TRADING mode. POST /golive to go live.")
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, log_level="warning")
