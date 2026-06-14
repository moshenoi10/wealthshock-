import asyncio
from typing import List

from strategies.base import Opportunity
from strategies import (
    near_resolution,
    pure_arbitrage,
    directional_arb,
    repricing,
    cross_timeframe,
    copy_trading,
    # exploit_new_market,    # DISABLED — losing money, logic flaw (see analysis)
    # exploit_liquidity_trap, # DISABLED — losing money, logic flaw (see analysis)
)

_STRATEGIES = [
    near_resolution,
    pure_arbitrage,
    directional_arb,
    repricing,
    cross_timeframe,
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
