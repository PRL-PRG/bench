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
    java: Path = Path("/usr/bin/java")
    renaissance: Path = Path("./renaissance-gpl-0.16.1.jar")


@dataclass
class RenaissanceBenchmark:
    name: str
    description: str
    reps: int


def list_benchmarks(params: Params) -> list[RenaissanceBenchmark]:
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

        for l in lines[2:]:
            if l.startswith("Default repetitions:"):
                reps = int(l.split(":")[1].strip())

        benchmarks.append(RenaissanceBenchmark(name, desc, reps))

    return benchmarks


def make_benchmarks(ctx: Context[Params]) -> list[Benchmark]:
    return [
        bench(rb.name)
        .with_command(
            lambda ctx: (
                [
                    ctx.params.java,
                    "-jar",
                    ctx.params.renaissance,
                    rb.name,
                    "-r",
                    str((ctx.warmup.max_runs() or 0) + (ctx.runs.max_runs() or 0)),
                ]
            )
        )
        .with_runs(rb.reps)
        for rb in list_benchmarks(ctx.params)
    ]


renaissance = (
    suite("Renaissance Benchmark Suite")
    .factory(make_benchmarks)
    .with_harness()
    .with_metric(
        max_rss(),
        Time(user=True, system=True, elapsed=True),
    )
)

run(renaissance, params=Params)
