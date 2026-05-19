#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Lox: two suites sharing a base; per-suite formatter.

Demonstrates:
- ``suite(...).from_files(...)`` discovery + ``.matrix`` / ``.filter``
- Multiple suites, each with its own processor.
- Per-suite Compact summary via Mixed reporters.
- Typed ``LoxParams`` dataclass for CLI flags.
"""

from dataclasses import dataclass

from benchr import (
    Compact, Mixed, P, Path, Summary, bench, run, suite,
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
    .from_files(_bench_root, pattern=r"\.lox$", exclude={"zoo_batch"})
    .with_cwd(lambda _, ctx: _bench_root(ctx))
    .with_command(lox_cmd)
    .with_timeout(20)
    .runs(10)
    .with_process(
        P.float_per_line("s").last_line().lower_is_better()
        | P.max_rss()
        | P.time(user=True, system=True)
    )
)

zoo_suite = (
    suite("ZooBatch")
    .add(bench("zoo_batch", path=Path("zoo_batch.lox")))
    .with_cwd(lambda _, ctx: _bench_root(ctx))
    .with_command(lambda b, ctx: [str(ctx.lox), str((_bench_root(ctx) / b.path).name)])
    .with_timeout(12)
    .runs(5)
    .with_process(
        P.float_per_line("iter", metric="throughput").nth(2).higher_is_better()
    )
)


if __name__ == "__main__":
    run(
        [lox_suite, zoo_suite],
        params=LoxParams,
        reporter=Mixed(
            Summary(formatter=Compact("runtime", suite="LoxSuite")),
            Summary(formatter=Compact("throughput", suite="ZooBatch")),
        ),
    )
