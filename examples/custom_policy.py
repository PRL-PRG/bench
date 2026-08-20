#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Writing a custom StoppingPolicy.

Stop as soon as we've seen the value '1' on the `READY` metric for three
consecutive runs. Demonstrates inheriting from `StoppingPolicy` (the frozen
config that returns a fresh `PolicyState` from `start()`) and
`PolicyState` (the per-run observer: `observe` records one finished
`Execution`, `satisfied` reports whether the policy has converged).
"""

import os
from dataclasses import dataclass

from bench import PolicyState, Regex, StoppingPolicy, bench, run, suite
from bench.core.metric import StdoutMetricSource


class _ConsecutiveReadyState(PolicyState):
    def __init__(self, n: int):
        self.target = n
        self.cur = 0

    def observe(self, execution):
        samples = [s for it in execution.iterations for s in it.samples]
        for s in samples + list(execution.process_samples):
            if s.metric == "READY" and s.value == 1.0:
                self.cur += 1
                break
        else:
            self.cur = 0  # reset on a run without READY=1

    def satisfied(self):
        return self.cur >= self.target


@dataclass(frozen=True)
class ConsecutiveReady(StoppingPolicy):
    n: int = 3

    def start(self) -> _ConsecutiveReadyState:
        return _ConsecutiveReadyState(self.n)


# Script "warms up" for a few runs (printing READY=0), then becomes READY.
COUNTER = "/tmp/_bench_demo/cnt"

SCRIPT = """
mkdir -p /tmp/_bench_demo
counter=/tmp/_bench_demo/cnt
[ -f "$counter" ] || echo 0 > "$counter"
n=$(cat "$counter")
echo $((n + 1)) > "$counter"
if [ $n -lt 2 ]; then echo "READY 0"; else echo "READY 1"; fi
"""


s = suite(
    "ready_loop",
    bench("p")
    .with_command(["bash", "-c", SCRIPT])
    .with_metric(Regex("READY", r"READY\s+(\d)", StdoutMetricSource, iterate=True))
    .with_runs(ConsecutiveReady(n=3).at_most(20)),
    # A benchmark runs with exactly the environment it is given, so the script
    # needs PATH handed to it to find `mkdir`/`cat`.
).with_env({"PATH": os.environ["PATH"]})


if __name__ == "__main__":
    # Reset the demo counter so the example is deterministic.
    try:
        os.remove(COUNTER)
    except FileNotFoundError:
        pass
    run(s)
