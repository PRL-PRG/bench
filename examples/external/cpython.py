#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""CPython performance benchmarks via pyperformance / pyperf.

``pyperformance run`` prints only progress to stdout and writes its results to
a JSON file. So each bench benchmark runs one pyperformance benchmark into a
temp JSON, then ``pyperf dump``s it to stdout, where every measured value
appears as::

    - value 1: 183 ms
    - value 2: 205 ms (+10%)

A Regex over those ``value`` lines yields the full distribution, one sample
per value (warmup/calibrate lines are skipped). pyperformance's own multi-run
machinery does the repetition, so bench runs each benchmark once.
pyperformance's chatter is redirected to stderr to keep stdout parseable.

Run this from inside the pyperformance virtualenv (so ``pyperformance`` and
``pyperf`` are on PATH), pointing ``--python`` at the interpreter under test.
"""

from dataclasses import dataclass
from pathlib import Path

from bench import Context, Regex, bench, run, suite


@dataclass
class CpythonParams:
    python: Path  # required: interpreter under test
    pyperformance: Path = Path("pyperformance")  # on PATH in the pyperformance venv
    pyperf: Path = Path("pyperf")


# A representative slice of the pyperformance default suite.
# Run `pyperformance list` for the full set (~100 benchmarks).
BENCHMARKS = [
    "2to3",
    "chaos",
    "deltablue",
    "fannkuch",
    "float",
    "go",
    "json_dumps",
    "nbody",
    "nqueens",
    "pickle",
    "pyflate",
    "raytrace",
    "regex_compile",
    "richards",
    "spectral_norm",
]


def _command(ctx: Context[CpythonParams]):
    p = ctx.params
    # mktemp -d: pyperformance refuses to overwrite an existing -o file, so we
    # hand it a fresh path inside a temp dir rather than a pre-created file.
    script = (
        'set -e; d="$(mktemp -d -t bench-pyperf.XXXXXX)"; f="$d/result.json"; '
        f'"{p.pyperformance}" run --python="{p.python}" '
        f'--benchmarks="{ctx.benchmark}" -o "$f" 1>&2; '
        f'"{p.pyperf}" dump "$f"; rm -rf "$d"'
    )
    return ["bash", "-c", script]


cpython = (
    suite("CPython pyperformance")
    .add_all(*(bench(name) for name in BENCHMARKS))
    .with_command(_command)
    .with_timeout(600)
    .with_metric(
        Regex(
            "runtime",
            r"(?m)^- value \d+:\s+([\d.]+)\s+(\w+)",
            unit_group=2,
        ).lower_is_better()
    )
)


if __name__ == "__main__":
    run(cpython, params=CpythonParams)
