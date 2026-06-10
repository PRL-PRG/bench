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
consecutive runs. Demonstrates inheriting from ``PolicyState`` and using
``Custom`` to wrap a state factory.
"""

from benchr import Custom, PolicyState, Regex, bench, run, suite


class ConsecutiveReady(PolicyState):
    def __init__(self, n: int = 3):
        self.target = n
        self.cur = 0

    def observe(self, run, samples):
        for s in samples:
            if s.metric == "READY" and s.value == 1.0:
                self.cur += 1
                break
        else:
            self.cur = 0  # reset on a run without READY=1

    def converged(self) -> bool:
        return self.cur >= self.target


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
            .with_runs(Custom(lambda: ConsecutiveReady(n=3)).at_most(20))
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
