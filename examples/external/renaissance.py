#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""Renaissance JVM suite: a harness driven entirely by iterating metrics.

Each Renaissance iteration prints a multi-line block:

    ====== mnemonics (functional) [default], iteration 0 started ======
    GC before operation: completed in 4.812 ms, heap usage 121.567 MB -> 3.935 MB.
    ====== mnemonics (functional) [default], iteration 0 completed (1575.265 ms) ======

`renaissance_monitor` used to reassemble those blocks so the metrics could parse
one iteration at a time. That framing is no longer needed: each `Regex` carries
`iterate=True`, walks its own matches across the whole output in order, and
stamps match N with iteration index N. The Controller then collects match N of
every metric into `Iteration` N, which reproduces the block grouping.

Caveat worth knowing: the pairing is positional per metric. If a run emits a
different number of `GC before operation` lines than `iteration ... completed`
lines, `gc_time` drifts out of step with `runtime`. Renaissance is regular
enough in practice; a harness that is not wants one Regex matching a whole block
(with `(?s)` and several capture groups) instead of one Regex per field.

`-r N` sets the iteration count, and Renaissance owns its warmup: bench's
`.with_warmup()` counts whole JVM launches, not iterations inside one.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bench import (
    BenchmarkBuilder,
    RegexMetric,
    SharedBenchParams,
    Time,
    bench,
    bench_app,
    max_rss,
    suite,
)
from bench.builder.suite import SuiteContext
from bench.core.metric import StdoutMetricSource, SystemTime, UserTime


class RenaissanceParams(SharedBenchParams):
    java: Path = Path("java")
    renaissance: Path = Path("renaissance-gpl-0.16.1.jar")
    runs: int | None = None


@dataclass
class RenaissanceBenchmark:
    name: str
    description: str
    reps: int


def list_benchmarks(params: RenaissanceParams) -> list[RenaissanceBenchmark]:
    cmd = [params.java, "-jar", params.renaissance, "--list"]
    res = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    res.check_returncode()
    benchmarks = []
    for b in re.split(r"\n\s*\n", res.stdout.strip()):
        lines = [line.strip() for line in b.splitlines() if line.strip()]
        if len(lines) < 3:
            print(f"Unexpected block:\n{b}")
            continue
        name = lines[0]
        desc = lines[1]
        reps = 0

        for line in lines[2:]:
            if line.startswith("Default repetitions:"):
                reps = int(line.split(":")[1].strip())

        benchmarks.append(RenaissanceBenchmark(name, desc, reps))

    return benchmarks


def make_benchmarks(ctx: SuiteContext[RenaissanceParams]) -> list[BenchmarkBuilder]:
    runs = ctx.params.runs
    return [
        bench(rb.name).with_command(
            lambda ctx, name=rb.name, reps=(runs if runs is not None else rb.reps): [
                ctx.params.java,
                "-jar",
                ctx.params.renaissance,
                name,
                "-r",
                str(reps),
            ]
        )
        for rb in list_benchmarks(ctx.params)
    ]


def _iterating(metric: str, pattern: str, unit: str) -> RegexMetric:
    """One sample per match, indexed in match order - the replacement for the
    old block-framing monitor."""
    return RegexMetric(metric, pattern, StdoutMetricSource, unit=unit, iterate=True)


renaissance = (
    suite("Renaissance Benchmark Suite")
    .generator(make_benchmarks)
    .with_metric(
        _iterating(
            "runtime", r"iteration \d+ completed \(([\d.]+) ms\)", "ms"
        ).lower_is_better(),
        _iterating(
            "gc_time", r"GC before operation: completed in ([\d.]+) ms", "ms"
        ).lower_is_better(),
        _iterating("heap_before", r"heap usage ([\d.]+) MB", "MB"),
        _iterating("heap_after", r"-> ([\d.]+) MB", "MB"),
        # Whole-process metrics: no iteration index, so they stay out of the
        # per-iteration records.
        max_rss(),
        UserTime(),
        SystemTime(),
        Time(),
    )
    .with_runs(1)  # one JVM launch; -r decides the iterations inside it
)

if __name__ == "__main__":
    bench_app(params=RenaissanceParams).add(renaissance).run_cli()
