#!/usr/bin/env -S uv run --script --quiet
# /// script
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
from __future__ import annotations

from bench import (
    Time,
    FloatPerLine,
    bench,
    run,
    suite,
)

s = (
    suite("custom_metric")
    .add(bench("fib"))
    .add(bench("hanoi"))
    .add(
        bench("zoo_batch").with_metric(
            FloatPerLine(metric="throughput", unit="iters", line=1).higher_is_better()
        )
    )
    .with_matrix(vm=["python3.9", "python3.14"])
    .with_command(lambda ctx: [ctx.data.vm, f"benchmarks/{ctx.benchmark}.py"])
    .with_process_metric(Time())
    .with_runs(3)
)

run(s)

# vim: ft=python
