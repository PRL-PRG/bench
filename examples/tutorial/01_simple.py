#!/usr/bin/env -S uv run --script --quiet
# /// script
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
from __future__ import annotations

from bench import GroupedSummary, Results, Summary, Time, bench, bench_app, suite
from bench.core.metric import max_rss
from bench.report.reporter import SummaryReporter

s = (
    suite("simple")
    .add(bench("fib"))
    .add(bench("hanoi"))
    .with_matrix(vm=["python3.9", "python3.14"], a=[1, 2])
    .with_command(lambda ctx: [ctx.data.vm, f"benchmarks/{ctx.benchmark}.py"])
    .with_process_metric(Time(elapsed=True), max_rss())
    .with_runs(5)
)


# run(s)

bench_app(
    summary=SummaryReporter(
        Results() & Summary() & GroupedSummary(axis="vm", metric="elapsed"),
    )
).add_all(s).run()

# vim: ft=python
