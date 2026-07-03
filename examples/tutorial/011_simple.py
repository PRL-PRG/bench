#!/usr/bin/env -S uv run --script --quiet
# /// script
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
from __future__ import annotations

from bench import Time, bench, run, suite, max_rss

s = (
    suite("simple")
    .add(bench("fib"))
    .add(bench("hanoi"))
    .with_matrix(vm=["python3.9", "python3.14"], experiment=[1, 2])
    .with_command(lambda ctx: [ctx.data.vm, f"benchmarks/{ctx.benchmark}.py"])
    .with_process_metric(Time(elapsed=True), max_rss())
    .with_runs(5)
)


run(s)

# a better comparison across experiments
# from bench import GroupedSummary, Results, Summary, SummaryReporter
# run(
#     s,
#     reporter=SummaryReporter(
#         Results() & Summary() & GroupedSummary(axis="vm", metric="elapsed"),
#     ),
# )

# vim: ft=python
