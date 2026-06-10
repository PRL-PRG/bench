#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Factory: build benchmarks programmatically at materialization time.

``.factory(fn)`` registers a deferred ``(ctx) -> [Benchmark]`` producer. It runs
when the Runner materializes the suite, so the benchmark list can depend on
``ctx`` (CLI params) or anything computed at run time. Propagating defaults
(``.with_cwd`` / ``.with_metric`` / ``.runs``) still reach factory-produced
benchmarks. Run ``--dry`` to see what the factory expands to.
"""

from benchr import Time, bench, run, suite

WORKLOADS = {"tiny": 1_000, "small": 100_000, "large": 10_000_000}


def make_benchmarks(ctx):
    return [
        bench(name).with_command(["python3", "-c", f"sum(range({n}))"])
        for name, n in WORKLOADS.items()
    ]


s = (
    suite("factory_demo")
    .factory(make_benchmarks)
    .with_metric(Time())
    .runs(5)
)


if __name__ == "__main__":
    run(s)
