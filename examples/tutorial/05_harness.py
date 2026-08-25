#!/usr/bin/env -S uv run --script --quiet
# /// script
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""Harness workloads: one process streams all the iterations.

No switch turns this on. `FloatPerLine` indexes each parsed line, so a single
process's output becomes `Iteration` 0, 1, 2, ... `.with_runs(1)` means one
process per variant; the workload's own argument decides how many iterations it
does, and its second argument how many leading ones it discards as warmup
(bench's `.with_warmup()` counts whole processes, not iterations).
"""

from __future__ import annotations

from bench import FloatPerLine, StdoutMetricSource, bench, run, suite

WARMUP, RUNS = 5, 10

s = (
    suite("harness")
    .add(bench("fib"))
    .add(bench("hanoi"))
    .with_matrix(vm=["python3.9", "python3.14", "pypy3"], repeat=range(2))
    .with_command(
        lambda ctx: [
            ctx.data.vm,
            f"benchmarks/{ctx.benchmark}.py",
            str(RUNS),
            str(WARMUP),
        ]
    )
    .with_metric(
        FloatPerLine(StdoutMetricSource, "runtime", unit="ms").lower_is_better()
    )
    .with_runs(1)  # one process per variant; RUNS iterations come out of it
)

run(s)

# vim: ft=python
