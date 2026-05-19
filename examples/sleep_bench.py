#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Minimal ad-hoc benchmark: ten random sleeps with a few intentional failures.

Demonstrates:
- The shortest possible Python script that calls ``benchr.run``.
- ``P.time()`` as the default processor for naive wall-clock benchmarking.
- Mixing successful and failing benchmarks (``false`` exits with non-zero).
"""

from random import choices, seed, uniform

from benchr import P, Path, bench, run, suite


seed(0)

suite_ = suite("Sleepy")
for i, succeed in enumerate(choices([True, False], k=10), 1):
    cmd = ["sh", "-c", f"sleep {uniform(0.01, 0.05):.3f}" if succeed else "false"]
    suite_ = suite_.add(
        bench(f"step_{i}")
        .with_command(cmd)
        .with_cwd(Path("/tmp"))
        .with_process(P.time().on_failure(P.constant("failed", 1.0)))
        .runs(2)
    )

if __name__ == "__main__":
    run(suite_)
