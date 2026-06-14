import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from typing import Dict, List, Optional

import config
from agent.market_data import MarketDataFetcher
from agent.executor import OrderExecutor
from strategies import run_all_strategies
from risk.manager import RiskManager
from hedge.system import HedgeSystem
from logger.trade_logger import TradeLogger

_STRATEGY_NAMES = ["near_resolution"]


class AgentCore:
    def __init__(self, trade_logger: TradeLogger):
        self.logger = trade_logger
        self.mode: str = config.TRADING_MODE
        self.balance: float = config.INITIAL_BALANCE
        self.daily_start_balance: float = config.INITIAL_BALANCE
        self.daily_pnl: float = 0.0
        self.total_pnl: float = 0.0
        self.trades_today: int = 0
        self.is_paused: bool = False
        self.loop_count: int = 0
        self.last_loop: Optional[str] = None
        self.errors_today: int = 0
        self.positions: Dict[str, dict] = {}

        self._start_time: float = time.time()
        # PnL snapshots: {ts, value} — 24h at 30s interval = 2880 max
        self._pnl_history: deque = deque(maxlen=2880)
        # Activity feed: {ts, type, msg} — newest first (appendleft)
        self._activity: deque = deque(maxlen=500)
        # Per-strategy stats
        self._strategy_stats: Dict[str, dict] = {
            s: {"trades": 0, "wins": 0, "pnl": 0.0} for s in _STRATEGY_NAMES
        }

        self.market_data = MarketDataFetcher()
        self.executor = OrderExecutor()
        self.risk = RiskManager(self)
        self.hedge = HedgeSystem(self)

        print(f"\n{'='*60}")
        print(f"  Polymarket Trading Agent")
        print(f"  Mode    : {'PAPER TRADING' if self.mode == 'paper' else '>>> LIVE TRADING <<<'}")
        print(f"  Balance : ${self.balance:.2f} USDC")
        print(f"  Max pos : ${self.balance * config.MAX_POSITION_PCT:.2f} per trade")
        print(f"  Budget  : ${config.MAX_TOTAL_BUDGET:.2f} hard cap")
        print(f"{'='*60}\n")

    async def run_loop(self):
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.errors_today += 1
                print(f"[AGENT ERROR] {exc}")
                self._activity.appendleft({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "type": "error",
                    "msg": f"שגיאה: {str(exc)[:80]}",
                })
            await asyncio.sleep(config.LOOP_INTERVAL)

    def _record_pnl_snapshot(self):
        positions_value = sum(
            (p["current_price"] / p["entry_price"]) * p["size"]
            for p in self.positions.values()
            if p.get("entry_price", 0) > 0
        )
        self._pnl_history.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "value": round(self.balance + positions_value, 4),
        })

    async def _tick(self):
        self.loop_count += 1
        self.last_loop = datetime.now(timezone.utc).isoformat()
        self._record_pnl_snapshot()

        if self.is_paused:
            print(f"[AGENT] ⏸  Paused. Balance=${self.balance:.2f} daily_pnl=${self.daily_pnl:.4f}")
            return

        if self.balance < config.MIN_BALANCE:
            self.is_paused = True
            print(f"[AGENT] Balance ${self.balance:.2f} < floor ${config.MIN_BALANCE}. Pausing.")
            return

        # Fetch markets
        markets = await self.market_data.fetch_active_markets()
        if not markets:
            print(f"[AGENT] #{self.loop_count:04d} — no markets returned")
            return

        print(
            f"[AGENT] #{self.loop_count:04d} | {len(markets)} markets | "
            f"balance=${self.balance:.2f} | positions={len(self.positions)} | "
            f"mode={self.mode.upper()}"
        )

        # Update prices on open positions
        self._refresh_positions(markets)
        await self.hedge.check_and_escalate()
        self._settle_expired(markets)

        # Run all 6 strategies in parallel
        opps = await run_all_strategies(markets, self.positions, self.balance)
        print(f"[AGENT] Found {len(opps)} raw opportunities")

        self._activity.appendleft({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "loop",
            "msg": f"#{self.loop_count} | {len(markets)} שווקים | {len(opps)} הזדמנויות גלמיות",
        })

        # Risk filter + rank by EV
        valid = self.risk.filter(opps)
        if not valid:
            print("[AGENT] No opportunities passed risk filter")
            return

        valid.sort(key=lambda o: o.ev, reverse=True)
        best = valid[0]

        print(
            f"[AGENT] Best: {best.strategy} | EV={best.ev:.4f} | "
            f"{best.market_question[:50]}"
        )

        # Execute
        result = await self.executor.execute(best, self.mode, self.balance)
        if result:
            self._record_entry(result)
            self.logger.log_trade(result)
            await self.hedge.apply(best, result)

        self._check_drawdown()
        self._print_daily_summary()

    def _record_entry(self, result: dict):
        cost = result["size"]
        self.balance = round(self.balance - cost, 6)
        self.trades_today += 1

        strategy = result.get("strategy", "unknown")
        if strategy in self._strategy_stats:
            self._strategy_stats[strategy]["trades"] += 1

        self._activity.appendleft({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "trade",
            "msg": (
                f"עסקה: {strategy} | "
                f"{result.get('market_question','')[:45]} | "
                f"{result.get('side','BUY')} @{result.get('price',0):.4f}"
            ),
        })

        tid = result.get("token_id")
        if tid and tid not in self.positions:
            entry_price = result.get("price", 0)
            self.positions[tid] = {
                "market_id": result.get("market_id"),
                "token_id": tid,
                "side": result.get("side"),
                "entry_price": entry_price,
                "size": result.get("size"),
                "current_price": entry_price,
                "strategy": strategy,
                "market_question": result.get("market_question", ""),
                "stop_loss_price": round(entry_price * (1 - config.STOP_LOSS_PCT), 6),
                "hedge_size": 0.0,
                "full_hedge_active": False,
                "timestamp": result.get("timestamp"),
                "price_history": [entry_price],
            }

    def _refresh_positions(self, markets: List[dict]):
        market_by_cid = {m.get("condition_id"): m for m in markets}
        for tid, pos in self.positions.items():
            market = market_by_cid.get(pos.get("market_id"))
            if not market:
                continue
            for token in market.get("tokens", []):
                if token.get("token_id") == tid:
                    new_price = float(token.get("price") or pos["current_price"])
                    pos["current_price"] = new_price
                    # Append to sparkline history (cap at 60 points)
                    ph = pos.setdefault("price_history", [])
                    if not ph or abs(new_price - ph[-1]) > 0.0001:
                        ph.append(new_price)
                        if len(ph) > 60:
                            ph.pop(0)
                    break

    def _settle_expired(self, markets: List[dict]):
        now = datetime.now(timezone.utc)
        active_cids = {m.get("condition_id") for m in markets}

        for tid in list(self.positions.keys()):
            pos = self.positions[tid]
            # If market no longer active → settle at current price
            if pos.get("market_id") not in active_cids:
                self._close_position(tid, pos["current_price"], "market_resolved")
                continue

            # Stop loss
            if pos["current_price"] <= pos["stop_loss_price"]:
                self._close_position(tid, pos["current_price"], "stop_loss")

    def _close_position(self, token_id: str, exit_price: float, reason: str):
        pos = self.positions.pop(token_id, None)
        if not pos:
            return

        proceeds = pos["size"] * (exit_price / pos["entry_price"])
        pnl = round(proceeds - pos["size"], 6)
        self.balance = round(self.balance + pos["size"] + pnl, 6)
        self.total_pnl = round(self.total_pnl + pnl, 6)
        self.daily_pnl = round(self.daily_pnl + pnl, 6)

        close_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy": pos.get("strategy"),
            "market_id": pos.get("market_id"),
            "token_id": token_id,
            "market_question": pos.get("market_question", ""),
            "side": "SELL",
            "price": exit_price,
            "entry_price": pos["entry_price"],
            "size": pos["size"],
            "pnl": pnl,
            "reasoning": f"Closed: {reason}",
            "mode": self.mode,
            "status": "closed",
        }
        self.logger.log_trade(close_record)
        print(
            f"[CLOSE] {token_id[:10]}… | reason={reason} | "
            f"entry={pos['entry_price']:.4f} exit={exit_price:.4f} | "
            f"PnL=${pnl:+.4f}"
        )

        strategy = pos.get("strategy", "unknown")
        if strategy in self._strategy_stats:
            self._strategy_stats[strategy]["pnl"] = round(
                self._strategy_stats[strategy]["pnl"] + pnl, 6
            )
            if pnl > 0:
                self._strategy_stats[strategy]["wins"] += 1

        # Notify copy-trading tracker
        if pos.get("strategy") == "copy_trading":
            from strategies.copy_trading import record_win, record_loss
            if pnl > 0:
                record_win(pos.get("wallet", ""))
            else:
                record_loss(token_id, pos.get("wallet", ""))

    def _check_drawdown(self):
        if self.daily_start_balance <= 0:
            return
        # Total portfolio value = cash + current market value of all open positions
        positions_value = sum(
            (pos["current_price"] / pos["entry_price"]) * pos["size"]
            for pos in self.positions.values()
            if pos.get("entry_price", 0) > 0
        )
        total_value = self.balance + positions_value
        drawdown = (self.daily_start_balance - total_value) / self.daily_start_balance
        if drawdown >= config.DAILY_DRAWDOWN_LIMIT and not self.is_paused:
            self.is_paused = True
            print(
                f"[RISK] ⛔ Daily drawdown {drawdown:.1%} ≥ {config.DAILY_DRAWDOWN_LIMIT:.0%}. "
                f"All trading paused."
            )
        if self.daily_pnl < 0 and abs(self.daily_pnl) / self.daily_start_balance > 0.05:
            print(
                f"[ALERT] ⚠️  Daily loss exceeds 5%: "
                f"${self.daily_pnl:.2f} ({abs(self.daily_pnl)/self.daily_start_balance:.1%})"
            )

    def _print_daily_summary(self):
        if self.loop_count % 20 == 0:  # every ~10 minutes
            summary = self.logger.daily_summary()
            print(
                f"\n[DAILY SUMMARY] trades={summary['total_trades']} "
                f"PnL=${summary['total_pnl']:+.4f} "
                f"win_rate={summary['win_rate']:.1%} "
                f"by_strategy={summary['by_strategy']}\n"
            )

    def get_status(self) -> dict:
        unrealized = sum(
            (p["current_price"] - p["entry_price"]) * (p["size"] / p["entry_price"])
            if p["entry_price"] > 0 else 0
            for p in self.positions.values()
        )
        return {
            "mode": self.mode,
            "balance": round(self.balance, 4),
            "total_pnl": round(self.total_pnl, 4),
            "daily_pnl": round(self.daily_pnl, 4),
            "unrealized_pnl": round(unrealized, 4),
            "open_positions": len(self.positions),
            "trades_today": self.trades_today,
            "is_paused": self.is_paused,
            "loop_count": self.loop_count,
            "last_loop": self.last_loop,
            "errors_today": self.errors_today,
            "uptime_seconds": int(time.time() - self._start_time),
            "strategy_stats": {k: dict(v) for k, v in self._strategy_stats.items()},
        }

    def reset(self) -> dict:
        """Full portfolio reset: restore initial balance, clear positions, unpause."""
        self.balance = config.INITIAL_BALANCE
        self.daily_start_balance = config.INITIAL_BALANCE
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.trades_today = 0
        self.errors_today = 0
        self.is_paused = False
        self.positions.clear()
        self._pnl_history.clear()
        self._activity.appendleft({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "loop",
            "msg": f"↺ איפוס מלא — יתרה חודשה ל-${config.INITIAL_BALANCE:.2f}, הסוכן פעיל מחדש",
        })
        print(f"[RESET] Full reset — balance=${config.INITIAL_BALANCE:.2f}, unpaused")
        return {"status": "reset", "balance": config.INITIAL_BALANCE, "paused": False}

    def get_pnl_history(self) -> list:
        return list(self._pnl_history)

    def get_activity(self, n: int = 100) -> list:
        return list(self._activity)[:n]

    def get_positions(self) -> list:
        return [
            {
                **pos,
                "unrealized_pnl": round(
                    (pos["current_price"] - pos["entry_price"])
                    * (pos["size"] / pos["entry_price"]) if pos["entry_price"] > 0 else 0,
                    6,
                ),
            }
            for pos in self.positions.values()
        ]
