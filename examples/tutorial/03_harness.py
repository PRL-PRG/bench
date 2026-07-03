#!/usr/bin/env -S uv run --script --quiet
# /// script
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
from __future__ import annotations

from bench import FloatPerLine, bench, run, suite

WARMUP, RUNS = 5, 10

s = (
    suite("harness")
    .add(bench("fib"))
    .add(bench("hanoi"))
    .with_matrix(vm=["python3.9", "python3.14", "pypy3"], runs=range(2))
    .with_command(
        lambda ctx: [
            ctx.data.vm,
            f"benchmarks/{ctx.benchmark}.py",
            str(WARMUP + RUNS),
        ]
    )
    .with_harness()  # one process streams all iterations
    .with_metric(FloatPerLine("ms").lower_is_better())
    .with_warmup(WARMUP)
    .with_runs(RUNS)
)

run(s)

# vim: ft=python
