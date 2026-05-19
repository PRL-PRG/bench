#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Run a benchmark until coefficient of variation stabilizes.

Demonstrates: ``CoefficientOfVariation`` with ``.at_least(N).at_most(M)``.
Stops as soon as the last 5 runs are within 2% CoV, but always runs at least 5
and at most 30 times.
"""

from benchr import CoefficientOfVariation, P, Path, bench, run, suite


cov = (
    CoefficientOfVariation("elapsed", threshold=0.02, window=5, min_runs=5)
    .at_least(5)
    .at_most(30)
)

s = (
    suite("converge",
        bench("noisy")
            .with_command(["sh", "-c", "sleep 0.02"])
            .with_cwd(Path("/tmp"))
            .with_process(P.time())
            .with_measure(cov)
    )
)


if __name__ == "__main__":
    run(s)
