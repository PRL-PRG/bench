#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Run a benchmark until the coefficient of variation stabilizes."""

from bench import CoefficientOfVariation, Time, bench, run, suite


cov = (
    CoefficientOfVariation("elapsed", threshold=0.02, window=5, min_runs=5)
    .at_least(5)
    .at_most(30)
)

s = suite(
    "converge",
    bench("noisy")
    .with_command(["sh", "-c", "sleep 0.02"])
    .with_process_metric(Time())
    .with_runs(cov),
)


if __name__ == "__main__":
    run(s)
