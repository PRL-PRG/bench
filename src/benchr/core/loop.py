"""benchmarking_loop: the essential core of a benchmark.

A benchmark, stripped of all mechanism, is a *feedback loop*: observe one
run's samples, let a stopping policy decide whether to keep going, and note
where warmup ends. ``benchmarking_loop`` is that loop and nothing else — it
knows nothing about processes or where observations come from.

Protocol: the generator yields ``(run, in_warmup)`` — "give me observation
number ``run``; it is (not) a warmup run" — and the caller ``send()``s back
the parsed Samples (``None`` is treated as an empty observation). Run numbers
are continuous across warmup and measurement (warmup 1..W, measured W+1..N);
the caller sees warmup end when ``in_warmup`` flips to False. The generator
returns when the ``runs`` policy has converged.
"""

from __future__ import annotations

from collections.abc import Generator

from benchr.core.policy import StoppingPolicy
from benchr.core.sample import Sample


def benchmarking_loop(
    warmup: StoppingPolicy,
    runs: StoppingPolicy,
) -> Generator[tuple[int, bool], list[Sample] | None, None]:
    """Yield ``(run, in_warmup)`` slots until both policies converge.

    Every observation — including an empty one for a failed run — counts:
    the active policy observes it and decides.
    """
    run = 0
    for policy, in_warmup in ((warmup, True), (runs, False)):
        state = policy.start()
        while not state.converged():
            run += 1
            samples = yield (run, in_warmup)
            state.observe(run, samples or ())
