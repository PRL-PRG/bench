#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "../..", editable = true }
# ///
"""

The renaissance benchmark for JVMs.

This is an example of a harness-based benchmark.
Each renaissance iteration prints a multi-line block:

    ====== mnemonics (functional) [default], iteration 0 started ======
    GC before operation: completed in 4.812 ms, heap usage 121.567 MB -> 3.935 MB.
    ====== mnemonics (functional) [default], iteration 0 completed (1575.265 ms) ======

To bring it into benchr, we need to create a benchmark monitor,
a component which finds individual runs so the run metrics can
extracts the measurements.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

from benchr import (
    BenchmarkBuilder,
    Context,
    HarnessHandle,
    Regex,
    Time,
    bench,
    line_monitor,
    max_rss,
    run,
    suite,
)


@dataclass
class Params:
    java: Path = Path("java")
    renaissance: Path = Path("renaissance-gpl-0.16.1.jar")


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


def renaissance_monitor(handle: HarnessHandle) -> Iterator[str]:
    """Group each iteration's lines (started -> completed) into one block.

    Reuses ``line_monitor`` for the file-tailing; JVM-startup lines before the
    first ``started`` fall through with an empty buffer and are discarded.
    """
    buf: list[str] = []
    for line in line_monitor(handle):
        if ", iteration" in line and "started ======" in line:
            buf = [line]  # new iteration begins
        elif ", iteration" in line and "completed (" in line:
            buf.append(line)
            yield "\n".join(buf)  # iteration done -> emit one block
            buf = []
        elif buf:
            buf.append(line)  # GC / heap lines inside the iteration


def make_benchmarks(ctx: Context[Params]) -> list[BenchmarkBuilder]:
    return [
        bench(rb.name)
        .with_command(
            lambda ctx, name=rb.name, reps=rb.reps: [
                ctx.params.java,
                "-jar",
                ctx.params.renaissance,
                name,
                "-r",
                str(reps),
            ]
        )
        .with_harness()
        .with_runs(rb.reps)
        for rb in list_benchmarks(ctx.params)
    ]


renaissance = (
    suite("Renaissance Benchmark Suite")
    .factory(make_benchmarks)
    .with_harness(monitor=renaissance_monitor)
    .with_metric(
        Regex(
            "runtime", r"iteration \d+ completed \(([\d.]+) ms\)", unit="ms"
        ).lower_is_better(),
        Regex(
            "gc_time", r"GC before operation: completed in ([\d.]+) ms", unit="ms"
        ).lower_is_better(),
        Regex("heap_before", r"heap usage ([\d.]+) MB", unit="MB"),
        Regex("heap_after", r"-> ([\d.]+) MB", unit="MB"),
        max_rss(),
        Time(user=True, system=True, elapsed=True),
    )
)

if __name__ == "__main__":
    run(renaissance, params=Params)
