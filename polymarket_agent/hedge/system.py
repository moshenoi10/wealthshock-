"""
Hedge System
- Every directional trade gets a 15% hedge on the opposite side (tracked in position).
- If a position loses >8%, full hedge is activated (hedge_size = full position size).
"""
from typing import TYPE_CHECKING

import config
from strategies.base import Opportunity

if TYPE_CHECKING:
    from agent.core import AgentCore

NO_HEDGE_STRATEGIES = {"pure_arbitrage"}


class HedgeSystem:
    def __init__(self, agent: "AgentCore"):
        self.agent = agent

    async def apply(self, opp: Opportunity, trade_result: dict):
        if opp.strategy in NO_HEDGE_STRATEGIES:
            return

        trade_size = trade_result.get("size", 0)
        hedge_size = round(trade_size * config.HEDGE_PCT, 4)

        if hedge_size < 0.25:
            return

        token_id = opp.token_id
        pos = self.agent.positions.get(token_id)
        if pos:
            pos["hedge_size"] = hedge_size
            print(f"[HEDGE] ${hedge_size:.2f} hedge on {token_id[:10]}… ({opp.strategy})")

    async def check_and_escalate(self):
        """Called each tick: escalate to full hedge if loss > 8%."""
        for token_id, pos in self.agent.positions.items():
            if pos.get("full_hedge_active"):
                continue

            entry = pos.get("entry_price", 0)
            current = pos.get("current_price", entry)
            if entry <= 0:
                continue

            loss_pct = (entry - current) / entry
            if loss_pct >= config.FULL_HEDGE_TRIGGER_PCT:
                pos["hedge_size"] = pos.get("size", 0)
                pos["full_hedge_active"] = True
                print(
                    f"[HEDGE] Full hedge activated on {token_id[:10]}… "
                    f"(loss={loss_pct:.1%})"
                )
