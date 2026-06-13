#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""File-discovered benchmarks: every script in workloads/ becomes a benchmark.

``from_files`` names each benchmark by its path relative to the root (without
extension) and stamps the file onto ``b.path``.
"""

from pathlib import Path

from benchr import Time, from_files, run, suite


HERE = Path(__file__).resolve().parent

s = (
    suite("discovered", *from_files(HERE / "workloads", pattern=r"\.py$"))
    .with_command(lambda ctx: ["python3", str(ctx.matrix.path)])
    .with_metric(Time())
    .with_runs(3)
)


if __name__ == "__main__":
    run(s)
