#!/usr/bin/env -S uv run --script --quiet
# /// script
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from bench import (
    Context,
    FixedRuns,
    FloatPerLine,
    GeomeanSummary,
    HarnessHandle,
    Results,
    SharedBenchParams,
    Summary,
    SummaryReporter,
    bench,
    bench_app,
    line_monitor,
    max_rss,
    suite,
)


@dataclass(frozen=True)
class Params(SharedBenchParams):
    awfy: Path = Path("are-we-fast-yet/benchmarks/Python")
    runs: int = 10
    warmup: int = 5


def awfy_monitor(handle: HarnessHandle) -> Iterator[str]:
    """Frame each `... runtime: <us>us` line as one iteration, drop the rest."""
    for line in line_monitor(handle):
        match = re.search(r"runtime: (\d+)us", line)
        if match:
            yield match.group(1)


def command(ctx: Context[Params]) -> list[str]:
    name = str(ctx.benchmark)
    n = ctx.params.warmup + ctx.params.runs
    harness = ctx.params.awfy / "harness.py"
    return [ctx.data.vm, str(harness), name, str(n), str(ctx.data.arg)]


awfy = (
    suite("AreWeFastYet")
    .add(bench("Bounce", arg=1500))
    .add(bench("CD", arg=250))
    .add(bench("DeltaBlue", arg=12000))
    .add(bench("Havlak", arg=1500))
    .add(bench("Json", arg=100))
    .add(bench("List", arg=1500))
    .add(bench("Mandelbrot", arg=500))
    .add(bench("NBody", arg=250000))
    .add(bench("Permute", arg=1000))
    .add(bench("Queens", arg=1000))
    .add(bench("Richards", arg=100))
    .add(bench("Sieve", arg=3000))
    .add(bench("Storage", arg=1000))
    .add(bench("Towers", arg=600))
    .with_matrix(vm=["python3.9", "python3.14", "pypy3"])
    .with_command(command)
    .with_cwd(lambda ctx: ctx.params.awfy)  # so AWFY's `import <bench>` resolves
    .with_harness(monitor=awfy_monitor)
    .with_metric(FloatPerLine(metric="runtime", unit="us").lower_is_better())
    .with_process_metric(max_rss())
    .with_timeout(600)
    .with_warmup(lambda ctx: FixedRuns(ctx.params.warmup))
    .with_runs(lambda ctx: FixedRuns(ctx.params.runs))
)


grouped = GeomeanSummary(axis="vm", metrics={"runtime", "max_rss"})
summary = SummaryReporter(Results() & Summary() & grouped)

bench_app("AWFY", params=Params, summary=summary).add_all(awfy).run()

# vim: ft=python
