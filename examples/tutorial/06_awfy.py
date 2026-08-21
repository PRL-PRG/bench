#!/usr/bin/env -S uv run --script --quiet
# /// script
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""Are-We-Fast-Yet: a real harness, driven with nothing but a metric.

AWFY's `harness.py` runs one benchmark N times in a single process and prints a
line per iteration:

    Bounce: iterations=1 runtime: 1234us

The old `awfy_monitor` framed those lines into iterations by hand. A
`Regex(..., iterate=True)` does the same job: it walks its matches in order and
stamps each one with its iteration index, so match N lands in `Iteration` N.
Lines that do not match (AWFY's totals and startup noise) are simply skipped.

AWFY's harness has no warmup flag, so the JIT curve is *in* the data - which is
the point of keeping per-iteration samples. Feed it `warmup + runs` iterations
and read the curve, or post-filter on `Sample.iteration`.
"""

from __future__ import annotations

from pathlib import Path

from bench import (
    Context,
    GeomeanSummary,
    Regex,
    Results,
    SharedBenchParams,
    Summary,
    SummaryReporter,
    bench,
    bench_app,
    max_rss,
    suite,
)
from bench.core.metric import StdoutMetricSource


class Params(SharedBenchParams):
    awfy: Path = Path("are-we-fast-yet/benchmarks/Python")
    runs: int = 10
    warmup: int = 5


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
    .with_metric(
        # One Iteration per `runtime: <us>us` line - this replaces the monitor.
        Regex(
            "runtime",
            r"runtime: (\d+)us",
            StdoutMetricSource,
            unit="us",
            iterate=True,
        ).lower_is_better(),
        max_rss(),  # whole-process, so it stays out of the iterations
    )
    .with_timeout(600)
    .with_runs(1)  # one harness process per variant
)


grouped = GeomeanSummary(axis="vm", metrics={"runtime", "max_rss"})
summary = SummaryReporter(Results() & Summary() & grouped)

bench_app("AWFY", params=Params, summary=summary).add(awfy).run_cli()

# vim: ft=python
