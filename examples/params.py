#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Typed CLI parameters: a @dataclass becomes --flags, passed to builders as ctx.

Run with defaults, or override:
    ./params.py
    ./params.py --n 1000000 --python python3.13
"""

from dataclasses import dataclass

from benchr import Time, bench, run, suite


@dataclass
class Params:
    n: int = 100_000          # --n INT       (default: 100000)
    python: str = "python3"   # --python STR  (default: python3)


def cmd(b, ctx: Params):
    return [ctx.python, "-c", f"sum(range({ctx.n}))"]


s = suite("params",
    bench("sum").with_command(cmd).with_metric(Time()).runs(3)
)


if __name__ == "__main__":
    run(s, params=Params)
