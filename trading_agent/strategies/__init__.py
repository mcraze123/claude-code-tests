from .smc      import SMCStrategy
from .hmm_smc  import HMMSMCStrategy
from .logistic import LogisticStrategy

STRATEGIES = {
    "smc":      SMCStrategy,
    "hmm_smc":  HMMSMCStrategy,
    "logistic": LogisticStrategy,
}


def get_strategy(name: str) -> "SMCStrategy | HMMSMCStrategy | LogisticStrategy":
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{name}'. Available: {list(STRATEGIES)}")
    return STRATEGIES[name]()
