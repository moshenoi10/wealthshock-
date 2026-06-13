from typing import List, TYPE_CHECKING

import config
from strategies.base import Opportunity

if TYPE_CHECKING:
    from agent.core import AgentCore


class RiskManager:
    def __init__(self, agent: "AgentCore"):
        self.agent = agent

    def filter(self, opportunities: List[Opportunity]) -> List[Opportunity]:
        return [o for o in opportunities if self._passes(o)]

    def _passes(self, opp: Opportunity) -> bool:
        if self.agent.is_paused:
            return False

        # Basic sanity
        if not opp.token_id:
            return False
        if opp.price <= 0 or opp.price >= 1.0:
            return False
        if opp.size <= 0:
            return False
        if opp.ev < 0.005:
            return False

        # Don't stack onto same token
        if opp.token_id in self.agent.positions:
            return False

        # Position size cap (belt + suspenders on top of executor cap)
        max_size = self.agent.balance * config.MAX_POSITION_PCT
        if opp.size > max_size * 1.1:
            return False

        # Daily drawdown guard
        if self.agent.daily_start_balance > 0:
            drawdown = (self.agent.daily_start_balance - self.agent.balance) / self.agent.daily_start_balance
            if drawdown >= config.DAILY_DRAWDOWN_LIMIT:
                return False

        return True

    def max_size_for(self, balance: float) -> float:
        return min(balance * config.MAX_POSITION_PCT, config.MAX_TOTAL_BUDGET * config.MAX_POSITION_PCT)
