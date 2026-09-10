#!/usr/bin/env -S uv run --script --quiet
# /// script
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
from __future__ import annotations

from bench import (
    ByBenchmarkMetricSummary,
    ComparisonSummary,
    GeomeanComparisonSummary,
    Time,
    bench,
    bench_app,
    max_rss,
    suite,
)

s = (
    suite("simple")
    .add(bench("fib"))
    .add(bench("hanoi"))
    .with_matrix(vm=["python3.9", "python3.14"], a=[1, 2])
    .with_command(lambda ctx: [ctx.data.vm, f"benchmarks/{ctx.benchmark}.py"])
    .with_metric(Time(), max_rss())
    .with_runs(5)
)


# run(s)

bench_app(
    summary=(
        ByBenchmarkMetricSummary()
        & ComparisonSummary()
        & GeomeanComparisonSummary(axis="vm").on_metrics("elapsed")
    )
).add(s).run_cli()

# vim: ft=python
