#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""Declarative benchmark script demonstrating matrix execution and custom metrics."""

import sys
from pathlib import Path

from bench import Regex, bench, run, suite

SQLITE_BENCH = Path(__file__).resolve().parent / "sqlite_bench.py"


def sqlite_cmd(ctx):
    return [
        sys.executable,
        str(SQLITE_BENCH),
        ctx.matrix.journal,
        ctx.matrix.sync,
    ]


s = (
    suite(
        "sqlite_inserts",
        bench("insert")
        .with_command(sqlite_cmd)
        .with_matrix(
            journal=["DELETE", "WAL", "MEMORY"],
            sync=["FULL", "NORMAL", "OFF"],
        ),
    )
    .with_metric(
        Regex("throughput", r"throughput:\s*([\d.]+)", unit="inserts/sec").higher_is_better()
    )
    .with_runs(3)
)

if __name__ == "__main__":
    run(s)
