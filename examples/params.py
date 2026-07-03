#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Typed CLI parameters: a @dataclass becomes --flags, passed to builders as ctx.

Run with defaults, or override:
    ./params.py
    ./params.py --n 1000000 --python python3.13
"""

from dataclasses import dataclass

from bench import Context, SharedBenchParams, Time, bench, bench_app, suite


@dataclass(frozen=True)
class Params(SharedBenchParams):
    # Inherit SharedBenchParams to keep the builtin flags (-j/--json/--include/
    # ...); a plain @dataclass would expose only the fields declared here.
    n: int = 100_000  # --n INT       (default: 100000)
    python: str = "python3"  # --python STR  (default: python3)


def cmd(ctx: Context[Params]):
    return [ctx.params.python, "-c", f"sum(range({ctx.params.n}))"]


s = suite(
    "params", bench("sum").with_command(cmd).with_process_metric(Time()).with_runs(3)
)


if __name__ == "__main__":
    bench_app(params=Params).add_all(s).run()
