from dataclasses import dataclass, field


@dataclass
class Opportunity:
    strategy: str
    market_id: str
    condition_id: str
    token_id: str
    side: str           # "BUY"
    price: float        # limit price
    size: float         # desired size in USDC
    ev: float           # expected value edge (higher = better)
    reasoning: str
    market_question: str = ""
