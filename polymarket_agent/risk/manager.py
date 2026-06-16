from typing import List, Optional, TYPE_CHECKING

import config
from logger import rejected_logger
from strategies.base import Opportunity

if TYPE_CHECKING:
    from agent.core import AgentCore

SOURCE = "risk_manager"


class RiskManager:
    def __init__(self, agent: "AgentCore"):
        self.agent = agent

    def filter(self, opportunities: List[Opportunity]) -> List[Opportunity]:
        passed = []
        for opp in opportunities:
            reason = self._rejection_reason(opp)
            if reason is None:
                passed.append(opp)
            else:
                rejected_logger.log(
                    source=SOURCE,
                    reason=reason,
                    market_question=opp.market_question,
                    token_id=opp.token_id,
                    value=opp.ev,
                    threshold=0.005,
                    extra={
                        "strategy": opp.strategy,
                        "price": opp.price,
                        "size": opp.size,
                    },
                )
        return passed

    def _rejection_reason(self, opp: Opportunity) -> Optional[str]:
        """Return the first failing reason, or None if the opp passes."""
        if self.agent.is_paused:
            return "agent_paused"

        if not opp.token_id:
            return "missing_token_id"

        if opp.price <= 0 or opp.price >= 1.0:
            return f"invalid_price:{opp.price:.4f}"

        if opp.size <= 0:
            return "invalid_size"

        if opp.ev < 0.005:
            return f"ev_too_low:{opp.ev:.5f}"

        if opp.token_id in self.agent.positions:
            return "already_holding"

        max_size = self.agent.balance * config.MAX_POSITION_PCT
        if opp.size > max_size * 1.1:
            return f"size_exceeds_cap:{opp.size:.2f}>{max_size:.2f}"

        if self.agent.daily_start_balance > 0:
            positions_value = sum(
                (p["current_price"] / p["entry_price"]) * p["size"]
                for p in self.agent.positions.values()
                if p.get("entry_price", 0) > 0
            )
            total_value = self.agent.balance + positions_value
            drawdown = (self.agent.daily_start_balance - total_value) / self.agent.daily_start_balance
            dd_limit = (
                config.PAPER_DAILY_DRAWDOWN_LIMIT
                if self.agent.mode == "paper"
                else config.DAILY_DRAWDOWN_LIMIT
            )
            if drawdown >= dd_limit:
                return f"daily_drawdown_limit:{drawdown:.2%}"

        return None

    def max_size_for(self, balance: float) -> float:
        return min(balance * config.MAX_POSITION_PCT, config.MAX_TOTAL_BUDGET * config.MAX_POSITION_PCT)
