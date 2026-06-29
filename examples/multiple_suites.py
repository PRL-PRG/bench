#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Two suites with different metrics, a per-suite Compact summary each.

Each suite gets its own `SummaryReporter` whose `Compact` formatter is
scoped with `suite=...`. A `CompositeReporter` fans the run out to both.
"""

from bench import Compact, CompositeReporter, SummaryReporter, Time, bench, run, suite


fast = (
    suite("fast")
    .add(bench("a").with_command(["sh", "-c", "sleep 0.01"]))
    .add(bench("b").with_command(["sh", "-c", "sleep 0.02"]))
    .with_process_metric(Time())
    .with_runs(5)
)

slow = (
    suite("slow")
    .add(bench("x").with_command(["sh", "-c", "sleep 0.05"]))
    .add(bench("y").with_command(["sh", "-c", "sleep 0.08"]))
    .with_process_metric(Time())
    .with_runs(5)
)


if __name__ == "__main__":
    run(
        [fast, slow],
        reporter=CompositeReporter(
            SummaryReporter(Compact("elapsed", suite="fast")),
            SummaryReporter(Compact("elapsed", suite="slow")),
        ),
    )
