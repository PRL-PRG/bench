#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""JIT-warmup pattern: warm up until CoV stabilizes, then measure 10 clean runs."""

from bench import CoefficientOfVariation, FixedRuns, Time, bench, run, suite


s = suite(
    "jit",
    bench("workload")
    .with_command(["sh", "-c", "sleep 0.05"])
    .with_process_metric(Time())
    .with_warmup(
        CoefficientOfVariation("elapsed", threshold=0.05, window=4, min_runs=4).at_most(
            20
        )
    )
    .with_runs(FixedRuns(10)),
)


if __name__ == "__main__":
    run(s)
