#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench", "pyperformance"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""CPython benchmarks via pyperformance.

Each pyperformance benchmark is one bench execution: the command runs
`pyperformance run` once, writing its JSON to a known path. Nothing is
observable while pyperformance works - it only writes that JSON once fully
done - so the old harness monitor did nothing but wait for the exit and then
read the file.

That is exactly what a `MetricSource` is: a `(InvocationResult) -> str` called
after the process exits. `json_values_source` reads the result JSON and returns
one measured value per line, and `FloatPerLine` turns each of those lines into
its own `Iteration`. No monitor, no streaming, no harness switch.

Wanting more independent measurements is a `--runs` concern: it spawns that many
separate pyperformance processes (matrix variants), each contributing its own
execution and its own set of iterations.

The default suite is discovered from `pyperformance list`, so this tracks
whatever pyperformance ships; subset it with bench's --include/--exclude.

pyperformance is this script's own dependency (see the PEP 723 header above), so
`uv run` installs it into the same environment that runs this script - no
separate pyperformance install or venv to manage, and no stale console-script
shebang to hit. sys.executable only *hosts* pyperformance's orchestration;
--python is the interpreter UNDER TEST, entirely independent of it -
pyperformance builds each benchmark's venv using --python itself. Benchmark
venvs, per-benchmark JSON, and bench's own bench.json all land under --output.
"""

import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import field
from pathlib import Path

from bench import (
    CompositeReporter,
    Context,
    FloatPerLine,
    InvocationResult,
    JsonReporter,
    SharedBenchParams,
    bench,
    bench_app,
    default_reporter,
    suite,
)


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
            "(each its own process, contributing its own iterations)."
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


def _raw_json(ctx: Context[CpythonParams]) -> Path:
    return ctx.params.output.resolve() / "raw" / f"{ctx.benchmark}.{ctx.data.runs}.json"


def command(ctx: Context[CpythonParams]) -> list[str]:
    p = ctx.params
    out = p.output.resolve()
    name = ctx.benchmark
    raw = _raw_json(ctx)
    log = out / "raw" / f"{name}.{ctx.data.runs}.log"

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


def json_values_source(raw: Path) -> Callable[[InvocationResult], str]:
    """A MetricSource: read pyperformance's result JSON once the process is
    done and lay every measured value out one per line, for FloatPerLine."""

    def read(_result: InvocationResult) -> str:
        data = json.loads(raw.read_text())
        return "\n".join(
            str(value)
            for run in data["benchmarks"][0]["runs"]
            for value in run.get("values", [])
        )

    return read


def runtime_metric(ctx: Context[CpythonParams]) -> FloatPerLine:
    return FloatPerLine(
        json_values_source(_raw_json(ctx)), "runtime", unit="s"
    ).lower_is_better()


def reporter(params: CpythonParams):
    default = default_reporter(params)
    json = JsonReporter(params.output.resolve() / "bench.json")

    return CompositeReporter(default, json) if default is not None else json


cpython = (
    suite("CPython pyperformance")
    .generator(lambda ctx: [bench(n) for n in list_benchmarks()])
    .with_command(command)
    .with_matrix(runs=lambda ctx: range(ctx.params.runs))
    .with_metric(runtime_metric)
    .with_runs(1)  # one pyperformance process per variant
)


if __name__ == "__main__":
    bench_app(params=CpythonParams, reporter=reporter).add(cpython).run_cli()
