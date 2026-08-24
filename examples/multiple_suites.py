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

import os

from bench import (
    Compact,
    CompositeReporter,
    SummaryReporter,
    Time,
    bench,
    bench_app,
    suite,
)

fast = (
    suite("fast")
    .add(bench("a").with_command(["sh", "-c", "sleep 0.01"]))
    .add(bench("b").with_command(["sh", "-c", "sleep 0.02"]))
    .with_metric(Time())
    .with_runs(5)
    # A benchmark runs with exactly the environment it is given, so a command
    # that shells out needs PATH handed to it.
    .with_env({"PATH": os.environ["PATH"]})
)

slow = (
    suite("slow")
    .add(bench("x").with_command(["sh", "-c", "sleep 0.05"]))
    .add(bench("y").with_command(["sh", "-c", "sleep 0.08"]))
    .with_metric(Time())
    .with_runs(5)
    .with_env({"PATH": os.environ["PATH"]})
)


if __name__ == "__main__":
    bench_app(
        reporter=CompositeReporter(
            SummaryReporter(Compact("elapsed", suite="fast")),
            SummaryReporter(Compact("elapsed", suite="slow")),
        )
    ).add(fast, slow).run_cli()
