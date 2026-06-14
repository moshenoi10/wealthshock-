import asyncio
from typing import List

from strategies.base import Opportunity
from strategies import (
    # near_resolution,    # DISABLED — testing copy_trading only
    # pure_arbitrage,     # DISABLED — testing copy_trading only
    # directional_arb,    # DISABLED — testing copy_trading only
    # repricing,          # DISABLED — testing copy_trading only
    # cross_timeframe,    # DISABLED — testing copy_trading only
    copy_trading,
    # exploit_new_market,    # DISABLED — logic flaw, losing money
    # exploit_liquidity_trap, # DISABLED — logic flaw, losing money
)

_STRATEGIES = [
    copy_trading,
]


async def run_all_strategies(
    markets: List[dict], positions: dict, balance: float
) -> List[Opportunity]:
    tasks = [s.run(markets, positions, balance) for s in _STRATEGIES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_opps: List[Opportunity] = []
    for strat, result in zip(_STRATEGIES, results):
        if isinstance(result, Exception):
            print(f"[STRATEGY ERROR] {strat.__name__.split('.')[-1]}: {result}")
            continue
        all_opps.extend(result)

    return all_opps
