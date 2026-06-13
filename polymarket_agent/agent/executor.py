import asyncio
from datetime import datetime, timezone
from typing import Optional

import config
from strategies.base import Opportunity


class OrderExecutor:
    def __init__(self):
        self._paper_count = 0

    async def execute(
        self, opp: Opportunity, mode: str, balance: float
    ) -> Optional[dict]:
        if mode == "paper":
            return self._paper_execute(opp, balance)
        return await self._live_execute(opp, balance)

    def _paper_execute(self, opp: Opportunity, balance: float) -> Optional[dict]:
        size_usdc = min(opp.size, balance * config.MAX_POSITION_PCT)
        if size_usdc < 0.50:
            return None

        self._paper_count += 1
        size_contracts = size_usdc / opp.price if opp.price > 0 else 0
        ts = datetime.now(timezone.utc).isoformat()

        result = {
            "order_id": f"PAPER-{self._paper_count:06d}",
            "timestamp": ts,
            "strategy": opp.strategy,
            "market_id": opp.condition_id,
            "token_id": opp.token_id,
            "market_question": opp.market_question[:100],
            "side": opp.side,
            "price": opp.price,
            "size": round(size_usdc, 4),
            "size_contracts": round(size_contracts, 2),
            "ev": round(opp.ev, 4),
            "reasoning": opp.reasoning,
            "status": "filled",
            "mode": "paper",
            "pnl": None,
            "entry_price": opp.price,
        }

        print(
            f"[PAPER] #{self._paper_count:04d} | {opp.strategy:<20} | "
            f"{opp.market_question[:40]:<40} | "
            f"{opp.side} @ {opp.price:.4f} | ${size_usdc:.2f} | EV={opp.ev:.3f}"
        )
        return result

    async def _live_execute(self, opp: Opportunity, balance: float) -> Optional[dict]:
        try:
            from agent.auth import get_client
            from py_clob_client.clob_types import OrderArgs, OrderType
            from py_clob_client.order_builder.constants import BUY, SELL

            client = get_client()
            size_usdc = min(opp.size, balance * config.MAX_POSITION_PCT)
            size_contracts = round(size_usdc / opp.price, 2)

            order_args = OrderArgs(
                token_id=opp.token_id,
                price=round(opp.price, 4),
                size=size_contracts,
                side=BUY if opp.side == "BUY" else SELL,
            )
            signed = client.create_order(order_args)
            resp = client.post_order(signed, OrderType.GTC)
            order_id = resp.get("orderID", "unknown")

            # Auto-cancel after 10 seconds if unfilled
            asyncio.create_task(self._cancel_after(client, order_id, config.ORDER_CANCEL_DELAY))

            return {
                "order_id": order_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "strategy": opp.strategy,
                "market_id": opp.condition_id,
                "token_id": opp.token_id,
                "market_question": opp.market_question[:100],
                "side": opp.side,
                "price": opp.price,
                "size": round(size_usdc, 4),
                "size_contracts": size_contracts,
                "ev": round(opp.ev, 4),
                "reasoning": opp.reasoning,
                "status": "open",
                "mode": "live",
                "pnl": None,
                "entry_price": opp.price,
            }
        except Exception as exc:
            print(f"[EXECUTOR] Live order failed: {exc}")
            return None

    @staticmethod
    async def _cancel_after(client, order_id: str, delay: int):
        await asyncio.sleep(delay)
        try:
            client.cancel_order(order_id)
            print(f"[EXECUTOR] Auto-cancelled {order_id} after {delay}s")
        except Exception as exc:
            print(f"[EXECUTOR] Cancel failed for {order_id}: {exc}")
