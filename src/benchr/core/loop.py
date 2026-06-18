"""benchmarking_loop: the core of a benchmark.

A benchmark, stripped of all mechanism, is a *feedback loop*: observe one
run's samples, let a stopping policy decide whether to keep going, and note
where warmup ends.

Protocol: the generator yields `in_warmup` — True while taking warmup
observations, False once measuring — and the caller `send()`s back each
`Observation`. The caller owns observation numbering; it sees warmup end when
`in_warmup` flips to False. The generator returns when the `runs` policy
has converged.
"""

from __future__ import annotations

from collections.abc import Generator

from benchr.core.policy import StoppingPolicy
from benchr.core.sample import Observation


def benchmarking_loop(
    warmup: StoppingPolicy,
    runs: StoppingPolicy,
) -> Generator[bool, Observation, None]:
    """Yield `in_warmup` per slot until both policies converge.

    Every observation — including a failed one (empty samples) — counts:
    the active policy observes it and decides.

    Note: the reason we yield in_warmup is that the controller who calls this
    generator has no way of knowing whether we have already passed the warmup
    or not (warmup can be a variable-length policy, e.g. CoV).
    """
    for policy, in_warmup in ((warmup, True), (runs, False)):
        state = policy.start()
        while not state.satisfied():
            observation = yield in_warmup
            state.observe(observation)
