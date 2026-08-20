#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Harness-style benchmarks: one process runs all the iterations, the VM pattern.

A *harness* executes its command once and prints one measurement per iteration
(here `workloads/fakevm.py`, a fake JIT whose early iterations are slow). There
is no `.with_harness()` switch any more, and none is needed: a metric that
indexes its samples turns one process's output into many `Iteration`s.
`FloatPerLine` does that by default, `Regex` does it with `iterate=True`.

So the whole wiring is:

  * `.with_runs(1)` - one process. `runs` counts *processes*, not iterations.
  * the harness's own flag (`-n`) decides how many iterations that process does.
  * `FloatPerLine` turns each printed line into `Iteration` 0, 1, 2, ...

Warmup is the harness's job (`-w` here), because bench's `.with_warmup()`
counts whole processes - it cannot discard the leading iterations *inside* one
process. Real harnesses all work this way: Renaissance takes `-r`, AWFY takes an
iteration count, ReBench harnesses print only what they want measured.

Real-world harnesses fit the same shape: Renaissance (`-r N` plus a Regex on its
`iteration N completed (... ms)` lines), LevelDB's db_bench (a Regex on
`micros/op`), or any ReBench-format harness (the `Rebench()` metric).
"""

import sys
from pathlib import Path

from bench import FloatPerLine, bench, run, suite
from bench.core.metric import StdoutMetricSource

FAKEVM = Path(__file__).parent / "workloads" / "fakevm.py"


WARMUP, RUNS = 5, 10


def vm_command(ctx):
    return [
        sys.executable,
        str(FAKEVM),
        str(ctx.benchmark),
        "-w",
        str(WARMUP),
        "-n",
        str(RUNS),
    ]


s = (
    suite("fakevm", bench("fib"), bench("sort"))
    .with_command(vm_command)
    .with_metric(
        FloatPerLine(StdoutMetricSource, "runtime", unit="ms").lower_is_better()
    )
    .with_runs(1)  # one process; RUNS iterations come out of it
)


if __name__ == "__main__":
    run(s)
