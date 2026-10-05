#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Factory: build benchmarks programmatically at materialization time.

`.generator(fn)` registers a deferred `(ctx) -> [BenchmarkBuilder]` producer. It runs
when the app plans the suites, so the benchmark list can depend on `ctx.params`
or anything computed at run time. Suite defaults (`.with_cwd` / `.with_metric` /
`.with_runs`) reach generated benchmarks too. Run with `--dry` or `--list` to
see what the generator expands to.
"""

from bench import Time, bench, run, suite

WORKLOADS = {"tiny": 1_000, "small": 100_000, "large": 10_000_000}


def make_benchmarks(ctx):
    return [
        bench(name).with_command(["python3", "-c", f"sum(range({n}))"])
        for name, n in WORKLOADS.items()
    ]


s = suite("factory_demo").generator(make_benchmarks).with_metric(Time()).with_runs(5)


if __name__ == "__main__":
    run(s)
