#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "../..", editable = true }
# ///
"""Lox: two suites sharing a base; per-suite formatter.

Demonstrates:
- ``.factory(lambda ctx: from_files(...))`` discovery + ``.filter``
- Multiple suites, each with its own metrics.
- Per-suite Compact summary via CompositeReporter reporters.
- Typed ``LoxParams`` dataclass for CLI flags.
"""

from dataclasses import dataclass
from pathlib import Path

from benchr import (
    Compact, CompositeReporter, FloatPerLine, SummaryReporter, Time,
    bench, from_files, max_rss, run, suite,
)


HERE = Path(__file__).resolve().parent


@dataclass
class LoxParams:
    lox: Path                            # required: path to the lox binary
    cwd: Path = HERE                     # script's base dir


def lox_cmd(b, ctx: LoxParams):
    return [str(ctx.lox), str(b.path)]


def _bench_root(ctx: LoxParams) -> Path:
    return (ctx.cwd / "benchmarks").resolve()


lox_suite = (
    suite("LoxSuite")
    .factory(lambda ctx: from_files(_bench_root(ctx), pattern=r"\.lox$", exclude={"zoo_batch"}))
    .with_cwd(lambda _, ctx: _bench_root(ctx))
    .with_command(lox_cmd)
    .with_timeout(20)
    .with_runs(10)
    .with_metric(
        FloatPerLine("s").last_line().lower_is_better(),
        max_rss(),
        Time(user=True, system=True),
    )
)

zoo_suite = (
    suite("ZooBatch")
    .add(bench("zoo_batch", path=Path("zoo_batch.lox")))
    .with_cwd(lambda _, ctx: _bench_root(ctx))
    .with_command(lambda b, ctx: [str(ctx.lox), str((_bench_root(ctx) / b.path).name)])
    .with_timeout(12)
    .with_runs(5)
    .with_metric(
        FloatPerLine("iter", metric="throughput").nth(2).higher_is_better()
    )
)


if __name__ == "__main__":
    run(
        [lox_suite, zoo_suite],
        params=LoxParams,
        reporter=CompositeReporter(
            SummaryReporter(formatter=Compact("runtime", suite="LoxSuite")),
            SummaryReporter(formatter=Compact("throughput", suite="ZooBatch")),
        ),
    )
