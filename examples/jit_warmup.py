#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""JIT-warmup pattern: warm up until CoV stabilizes, then measure 10 clean runs.

Demonstrates: ``CoV`` as the warmup policy with ``FixedRuns`` as the measure
policy. Warmup samples are reported (with ``phase="warmup"``) so you can
inspect the warmup curve in the JSON/CSV outputs, but the summary stats only
include the measurement runs.
"""

from benchr import CoefficientOfVariation, FixedRuns, Time, bench, run, suite


s = (
    suite("jit",
        bench("workload")
            .with_command(["sh", "-c", "sleep 0.05"])
            .with_metric(Time())
            .with_warmup(
                CoefficientOfVariation("elapsed", threshold=0.05, window=4, min_runs=4)
                .at_most(20)
            )
            .with_measure(FixedRuns(10))
    )
)


if __name__ == "__main__":
    run(s)
