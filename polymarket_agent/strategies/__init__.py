import asyncio
from typing import List

from strategies.base import Opportunity
from strategies import (
    near_resolution,
    # pure_arbitrage,        # DISABLED
    # directional_arb,       # DISABLED
    # repricing,             # DISABLED
    # cross_timeframe,       # DISABLED
    # copy_trading,          # DISABLED — no whale signals
    # exploit_new_market,    # DISABLED — logic flaw
    # exploit_liquidity_trap, # DISABLED — logic flaw
)

_STRATEGIES = [
    near_resolution,
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
