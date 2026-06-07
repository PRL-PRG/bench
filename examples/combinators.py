#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Every stopping-policy combinator in one script.

Reads as: "at least 5 runs AND CoV stable, OR 30 runs reached" — exactly what
.at_least(5).at_most(30) on top of CoV would produce, but written out so the
combinators are visible.
"""

from benchr import CoefficientOfVariation, FixedRuns, Path, Time, bench, run, suite


policy = FixedRuns(5) & CoefficientOfVariation("elapsed", threshold=0.05) | FixedRuns(30)
# Operator precedence: `&` binds tighter than `|`, so this is
#    (FixedRuns(5) & CoV(...)) | FixedRuns(30)


s = (
    suite("combinators",
        bench("workload")
            .with_command(["sh", "-c", "sleep 0.02"])
            .with_cwd(Path("/tmp"))
            .with_metric(Time())
            .with_measure(policy)
    )
)


if __name__ == "__main__":
    run(s)
