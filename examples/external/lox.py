#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""Lox: two file-discovered suites, each with its own metrics and a per-suite
Compact summary (via `CompositeReporter`)."""

from dataclasses import dataclass
from pathlib import Path

from bench import (
    Compact,
    CompositeReporter,
    Context,
    FloatPerLine,
    SummaryReporter,
    Time,
    bench,
    from_files,
    max_rss,
    run,
    suite,
)


HERE = Path(__file__).resolve().parent


@dataclass
class LoxParams:
    lox: Path  # required: path to the lox binary
    cwd: Path = HERE  # script's base dir


def lox_cmd(ctx: Context[LoxParams]):
    return [str(ctx.params.lox), str(ctx.data.path)]


def _bench_root(ctx: LoxParams) -> Path:
    return (ctx.cwd / "benchmarks").resolve()


lox_suite = (
    suite("LoxSuite")
    .factory(
        lambda ctx: from_files(
            _bench_root(ctx.params), pattern=r"\.lox$", exclude={"zoo_batch"}
        )
    )
    .with_cwd(lambda ctx: _bench_root(ctx.params))
    .with_command(lox_cmd)
    .with_timeout(20)
    .with_runs(10)
    .with_metric(
        FloatPerLine("s").last_line().lower_is_better(),
    )
    .with_process_metric(
        max_rss(),
        Time(user=True, system=True),
    )
)

zoo_suite = (
    suite("ZooBatch")
    .add(bench("zoo_batch", path=Path("zoo_batch.lox")))
    .with_cwd(lambda ctx: _bench_root(ctx.params))
    .with_command(
        lambda ctx: [
            str(ctx.params.lox),
            str((_bench_root(ctx.params) / ctx.data.path).name),
        ]
    )
    .with_timeout(12)
    .with_runs(5)
    .with_metric(FloatPerLine("iter", metric="throughput").nth(2).higher_is_better())
)


if __name__ == "__main__":
    run(
        [lox_suite, zoo_suite],
        params=LoxParams,
        reporter=CompositeReporter(
            SummaryReporter(Compact("runtime", suite="LoxSuite")),
            SummaryReporter(Compact("throughput", suite="ZooBatch")),
        ),
    )
