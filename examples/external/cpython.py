#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench", "pyperformance"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""CPython benchmarks via pyperformance.

Each pyperformance benchmark becomes one bench *harness* benchmark: the
command runs `pyperformance run` once, writing its JSON to a known path.
Nothing is observable while pyperformance runs - it only writes that JSON
once fully done - so the monitor just waits for the process to exit, then
reads the JSON directly and yields each measured value as its own bench
Iteration. bench's own warmup policy marks the first --warmup of those as
warmup (excluded from stats, still visible in the report/--json); the
framework default of keeping exactly one more after that is what's kept as
the measurement - a harness process runs once, so it contributes once.

Wanting more independent measurements is a --runs concern, not --warmup or
runs: --runs spawns that many separate pyperformance processes (matrix
variants), each contributing its own kept sample and its own report row.

The default suite is discovered from `pyperformance list`, so this tracks
whatever pyperformance ships; subset it with bench's --include/--exclude.

pyperformance is this script's own dependency (see the PEP 723 header
above), so `uv run` installs it into the same environment that runs this
script - no separate pyperformance install or venv to manage, and no stale
console-script shebang to hit. sys.executable only *hosts* pyperformance's
orchestration; --python is the interpreter UNDER TEST, entirely independent
of it - pyperformance builds each benchmark's venv using --python itself.
Benchmark venvs, per-benchmark JSON, and bench's own bench.json all land
under --output.
"""

import json
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from bench import (
    CompositeReporter,
    Context,
    FixedRuns,
    FloatPerLine,
    HarnessHandle,
    HarnessMonitor,
    JsonReporter,
    SharedBenchParams,
    bench,
    bench_app,
    suite,
)
from bench.run import default_reporter


@dataclass(frozen=True)
class CpythonParams(SharedBenchParams):
    python: Path = field(
        default=Path(sys.executable),
        metadata={
            "flags": ("-p",),
            "help": "Interpreter under test (pyperformance --python). "
            "Defaults to the interpreter running this script.",
        },
    )
    output: Path = field(
        default=Path("results"),
        metadata={
            "flags": ("-o",),
            "help": "Output dir: benchmark venvs, per-benchmark JSON, bench.json.",
        },
    )
    rigorous: bool = field(
        default=False,
        metadata={
            "flags": ("-r",),
            "action": "store_true",
            "help": "pyperformance --rigorous.",
        },
    )
    fast: bool = field(
        default=False,
        metadata={
            "flags": ("-f",),
            "action": "store_true",
            "help": "pyperformance --fast.",
        },
    )
    track_memory: bool = field(
        default=False,
        metadata={
            "flags": ("-m",),
            "action": "store_true",
            "help": "pyperformance --track-memory (Linux only).",
        },
    )
    hook: list[str] = field(
        default_factory=list,
        metadata={"help": "pyperformance --hook (repeatable): perf_record, pystats."},
    )
    runs: int = field(
        default=1,
        metadata={
            "help": "How many independent times to run this benchmark "
            "(each its own process, contributing one measured sample)."
        },
    )
    warmup: int = field(
        default=0,
        metadata={
            "help": "How many of each runs's leading measured values "
            "to discard as warmup before keeping the next one."
        },
    )


def list_benchmarks() -> list[str]:
    r = subprocess.run(
        [sys.executable, "-m", "pyperformance", "list"], capture_output=True, text=True
    )
    if r.returncode != 0:
        raise SystemExit(f"`pyperformance list` failed:\n{r.stderr}")
    names = re.findall(r"(?m)^- (\S+)$", r.stdout)
    if not names:
        raise SystemExit(f"no benchmarks parsed from `pyperformance list`:\n{r.stdout}")
    return names


def command(ctx: Context[CpythonParams]) -> list[str]:
    p = ctx.params
    out = p.output.resolve()
    name = ctx.benchmark
    runs = ctx.data.runs
    raw = out / "raw" / f"{name}.{runs}.json"
    log = out / "raw" / f"{name}.{runs}.log"

    opts = ""
    if p.rigorous:
        opts += " --rigorous"
    if p.fast:
        opts += " --fast"
    if p.track_memory:
        opts += " --track-memory"
    for h in p.hook:
        opts += f" --hook={h}"

    script = (
        f'set -eo pipefail; mkdir -p "{out}/raw"; cd "{out}"; rm -f "{raw}"; '
        f'"{sys.executable}" -m pyperformance run --python="{p.python}" '
        f'--benchmarks="{name}"{opts} -o "{raw}" > "{log}" 2>&1'
    )
    return ["bash", "-c", script]


def monitor(ctx: Context[CpythonParams]) -> HarnessMonitor:
    p = ctx.params
    raw = p.output.resolve() / "raw" / f"{ctx.benchmark}.{ctx.data.runs}.json"

    def read(handle: HarnessHandle) -> Iterator[str]:
        while handle.is_alive():
            time.sleep(0.05)
        data = json.loads(raw.read_text())
        for run in data["benchmarks"][0]["runs"]:
            for value in run.get("values", []):
                yield str(value)

    return read


def reporter(ctx: Context[CpythonParams]):
    return CompositeReporter(
        default_reporter(ctx),
        JsonReporter(ctx.params.output.resolve() / "bench.json"),
    )


cpython = (
    suite("CPython pyperformance")
    .factory(lambda ctx: [bench(n) for n in list_benchmarks()])
    .with_command(command)
    .with_monitor_fn(monitor)
    .with_matrix(runs=lambda ctx: range(ctx.params.runs))
    .with_warmup(lambda ctx: FixedRuns(ctx.params.warmup))
    .with_metric(FloatPerLine("s", metric="runtime").lower_is_better())
)


if __name__ == "__main__":
    bench_app(params=CpythonParams, reporter=reporter).add(cpython).run()
