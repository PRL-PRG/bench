#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Two suites with different metrics; per-suite formatter via filter."""

from benchr import Compact, CompositeReporter, SummaryReporter, Time, bench, run, suite


fast = (
    suite("fast")
    .add(bench("a").with_command(["sh", "-c", "sleep 0.01"]))
    .add(bench("b").with_command(["sh", "-c", "sleep 0.02"]))
    .with_metric(Time()).with_runs(5)
)

slow = (
    suite("slow")
    .add(bench("x").with_command(["sh", "-c", "sleep 0.05"]))
    .add(bench("y").with_command(["sh", "-c", "sleep 0.08"]))
    .with_metric(Time()).with_runs(5)
)


if __name__ == "__main__":
    run(
        [fast, slow],
        reporter=CompositeReporter(
            SummaryReporter(formatter=Compact("elapsed", suite="fast")),
            SummaryReporter(formatter=Compact("elapsed", suite="slow")),
        ),
    )
