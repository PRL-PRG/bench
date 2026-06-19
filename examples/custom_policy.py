#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Writing a custom StoppingPolicy.

Stop as soon as we've seen the value '1' on the ``READY`` metric for three
consecutive runs. Demonstrates inheriting from ``StoppingPolicy`` (the frozen
config that returns a fresh ``PolicyState`` from ``start()``) and
``PolicyState`` (the per-observation observer: ``observe`` records each
observation, ``satisfied`` reports whether the policy has converged).
"""

from dataclasses import dataclass

from benchr import PolicyState, Regex, StoppingPolicy, bench, run, suite


class _ConsecutiveReadyState(PolicyState):
    def __init__(self, n: int):
        self.target = n
        self.cur = 0

    def observe(self, observation):
        for s in observation.samples:
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
SCRIPT = """
mkdir -p /tmp/_benchr_demo
counter=/tmp/_benchr_demo/cnt
[ -f "$counter" ] || echo 0 > "$counter"
n=$(cat "$counter")
echo $((n + 1)) > "$counter"
if [ $n -lt 2 ]; then echo "READY 0"; else echo "READY 1"; fi
"""


s = (
    suite("ready_loop",
        bench("p")
            .with_command(["bash", "-c", SCRIPT])
            .with_metric(Regex("READY", r"READY\s+(\d)", unit=""))
            .with_runs(ConsecutiveReady(n=3).at_most(20))
    )
)


if __name__ == "__main__":
    import os
    # Reset the demo counter so the example is deterministic.
    try:
        os.remove("/tmp/_benchr_demo/cnt")
    except FileNotFoundError:
        pass
    run(s)
