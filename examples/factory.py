#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Factory: build benchmarks programmatically at materialization time.

`.factory(fn)` registers a deferred `(ctx) -> [Benchmark]` producer. It runs
when the Runner materializes the suite, so the benchmark list can depend on
`ctx` (CLI params) or anything computed at run time. SuiteBuilder defaults
(`.with_cwd` / `.with_metric` / `.runs`) resolve at the same moment, so
they reach factory-produced benchmarks too. Run `--dry` to see what the
factory expands to.
"""

from bench import Time, bench, run, suite

WORKLOADS = {"tiny": 1_000, "small": 100_000, "large": 10_000_000}


def make_benchmarks(ctx):
    return [
        bench(name).with_command(["python3", "-c", f"sum(range({n}))"])
        for name, n in WORKLOADS.items()
    ]


s = (
    suite("factory_demo")
    .factory(make_benchmarks)
    .with_process_metric(Time())
    .with_runs(5)
)


if __name__ == "__main__":
    run(s)
