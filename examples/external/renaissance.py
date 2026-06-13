#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "../..", editable = true }
# ///
"""

The renaissance benchmark compating multiple JVMs

"""

from dataclasses import dataclass
from pathlib import Path
import re

from benchr import (
    Benchmark,
    Context,
    Time,
    bench,
    max_rss,
    run,
    suite,
)

import subprocess


@dataclass
class Params:
    java: Path
    renaissance: Path


@dataclass
class RenaissanceBenchmark:
    name: str
    description: str
    reps: int


def list_benchmarks(params: Params) -> list[RenaissanceBenchmark]:
    cmd = [params.java, "-jar", params.renaissance, "--list"]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    res.check_returncode()
    benchmarks = []
    for b in re.split(r"\n\s*\n", str(res.stdout).strip()):
        lines = [line.strip() for line in b.splitlines() if line.strip()]
        if len(lines) < 3:
            print(f"Unexpected block:\n{b}")
            continue
        name = lines[0]
        desc = lines[1]
        reps = 0

        for l in lines[2:]:
            if l.startswith("Default repetitions:"):
                reps = int(l.split(":")[1].strip())

        benchmarks.append(RenaissanceBenchmark(name, desc, reps))

    return benchmarks


def make_benchmarks(ctx: Context[Params]) -> list[Benchmark]:
    # Harness: the JVM runs every iteration itself, so the command's ``-r``
    # must be the *total* the harness should produce — warmup + measured runs.
    # Both come from the (override-aware) suite-level Context, so a static
    # command here stays in sync with --warmup/--runs given on the CLI. The
    # warmup/runs policies stay separate (R, not W+R) so the harness expects
    # exactly the count the command produces. Path params need no str().
    total = (ctx.warmup.max_runs() or 0) + (ctx.runs.max_runs() or 0)
    return [
        bench(rb.name).with_command(
            [
                ctx.params.java,
                "-jar",
                ctx.params.renaissance,
                rb.name,
                "-r",
                str(total),
            ]
        )
        for rb in list_benchmarks(ctx.params)
    ]


renaissance = (
    suite("Renaissance Benchmark Suite")
    .factory(make_benchmarks)
    .with_harness()
    .with_warmup(3)
    .with_runs(10)
    .with_metric(
        max_rss(),
        Time(user=True, system=True, elapsed=True),
    )
)

run(renaissance, params=Params)
