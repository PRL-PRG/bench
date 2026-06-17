#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Run a benchmark until the coefficient of variation stabilizes.

Here CoV governs the *measured* runs (``.with_runs``); for CoV as the *warmup*
policy followed by a fixed measured count, see ``jit_warmup.py``.

Stops as soon as the last 5 runs are within 2% CoV, but always runs at least 5
and at most 30 times. ``.at_least`` / ``.at_most`` are sugar over the raw
policy combinators:

    cov.at_least(5).at_most(30)
        == (cov & FixedRuns(5)) | FixedRuns(30)

(`&` = both must converge, `|` = either suffices; `&` binds tighter than `|`.)
"""

from benchr import CoefficientOfVariation, Time, bench, run, suite


cov = (
    CoefficientOfVariation("elapsed", threshold=0.02, window=5, min_runs=5)
    .at_least(5)
    .at_most(30)
)

s = suite("converge",
    bench("noisy")
        .with_command(["sh", "-c", "sleep 0.02"])
        .with_metric(Time())
        .with_runs(cov)
)


if __name__ == "__main__":
    run(s)
