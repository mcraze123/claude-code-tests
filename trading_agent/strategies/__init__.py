from .base     import BaseStrategy
from .smc      import SMCStrategy
from .hmm_smc  import HMMSMCStrategy
from .hmm_ob   import HMMOBStrategy
from .logistic import LogisticStrategy
from .ny_open  import NYOpenStrategy

STRATEGIES = {
    "smc":      SMCStrategy,
    "hmm_smc":  HMMSMCStrategy,
    "hmm_ob":   HMMOBStrategy,
    "logistic": LogisticStrategy,
    "ny_open":  NYOpenStrategy,
}


def get_strategy(name: str) -> BaseStrategy:
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{name}'. Available: {list(STRATEGIES)}")
    return STRATEGIES[name]()
