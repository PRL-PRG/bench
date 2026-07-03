#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""CPython benchmarks via pyperformance / pyperf.

Each pyperformance benchmark becomes one bench benchmark. bench runs
`pyperformance run` once for it (pyperformance does its own repetition), writes
the pyperf JSON under <output>/raw/, then `pyperf dump`s it; a regex turns each
`- value N: X ms` line into one sample.

The default suite is discovered from `pyperformance list`, so this tracks
whatever pyperformance ships; subset it with bench's --include/--exclude.

pyperformance is invoked directly via `<project>/.venv/bin/python -m
pyperformance` rather than the `pyperformance`/`pyperf` console scripts, which
can have a stale shebang if the venv directory was ever moved. --project
points at that venv; --python is the interpreter under test. Benchmark venvs,
per-benchmark JSON, and bench's own bench.json all land under --output.
"""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from bench import (
    CompositeReporter,
    Context,
    JsonReporter,
    Regex,
    bench,
    bench_app,
    suite,
)
from bench.run import default_reporter


@dataclass
class CpythonParams:
    python: Path = field(
        metadata={
            "flags": ("-p",),
            "help": "Interpreter under test (pyperformance --python).",
        }
    )
    project: Path = field(
        default=Path("."),
        metadata={
            "help": "Directory containing pyperformance's .venv "
            "(pyperformance + pyperf installed)."
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


def _driver(p: CpythonParams) -> Path:
    """Python executable in pyperformance's venv (has pyperformance + pyperf)."""
    return p.project.resolve() / ".venv" / "bin" / "python"


def _list_benchmarks(p: CpythonParams) -> list[str]:
    python = _driver(p)
    r = subprocess.run(
        [str(python), "-m", "pyperformance", "list"], capture_output=True, text=True
    )
    if r.returncode != 0:
        raise SystemExit(f"`pyperformance list` failed:\n{r.stderr}")
    names = re.findall(r"(?m)^- (\S+)$", r.stdout)
    if not names:
        raise SystemExit(f"no benchmarks parsed from `pyperformance list`:\n{r.stdout}")
    return names


def _command(ctx: Context[CpythonParams]):
    p = ctx.params
    python = _driver(p)
    out = p.output.resolve()
    name = ctx.benchmark
    raw = out / "raw" / f"{name}.json"
    log = out / "raw" / f"{name}.log"

    opts = ""
    if p.rigorous:
        opts += " --rigorous"
    if p.fast:
        opts += " --fast"
    if p.track_memory:
        opts += " --track-memory"
    for h in p.hook:
        opts += f" --hook={h}"

    # cd into <output> so pyperformance's ./venv/ lands there; tee run output to
    # a log and stderr so only `pyperf dump` reaches stdout for the metric.
    script = (
        f'set -eo pipefail; mkdir -p "{out}/raw"; cd "{out}"; rm -f "{raw}"; '
        f'"{python}" -m pyperformance run --python="{p.python}" '
        f'--benchmarks="{name}"{opts} -o "{raw}" 2>&1 | tee "{log}" >&2; '
        f'"{python}" -m pyperf dump "{raw}"'
    )
    return ["bash", "-c", script]


def _reporter(ctx: Context[CpythonParams]):
    return CompositeReporter(
        default_reporter(ctx),
        JsonReporter(ctx.params.output.resolve() / "bench.json"),
    )


cpython = (
    suite("CPython pyperformance")
    .factory(lambda ctx: [bench(n) for n in _list_benchmarks(ctx.params)])
    .with_command(_command)
    .with_timeout(1800)
    .with_metric(
        Regex(
            "runtime",
            r"(?m)^- value \d+:\s+([\d.]+)\s+(\w+)",
            unit_group=2,
        ).lower_is_better()
    )
)


if __name__ == "__main__":
    bench_app(params=CpythonParams, reporter=_reporter).add(cpython).run()
