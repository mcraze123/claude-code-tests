from .smc      import SMCStrategy
from .hmm_smc  import HMMSMCStrategy
from .hmm_ob   import HMMOBStrategy
from .logistic import LogisticStrategy

STRATEGIES = {
    "smc":      SMCStrategy,
    "hmm_smc":  HMMSMCStrategy,
    "hmm_ob":   HMMOBStrategy,
    "logistic": LogisticStrategy,
}


def get_strategy(name: str) -> "SMCStrategy | HMMSMCStrategy | HMMOBStrategy | LogisticStrategy":
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{name}'. Available: {list(STRATEGIES)}")
    return STRATEGIES[name]()
