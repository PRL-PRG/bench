#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Failure handling: mixed success / failure runs.

Failed runs emit no metrics. bench records each as a structured failure that
the summary lists in a ``Failures:`` block. ``.with_runs(N)`` counts every attempt,
so ``broken`` runs exactly 3 times and reports 3 failures (no fake timings).
"""

from bench import Time, bench, run, suite


s = (
    suite("flaky",
        # Always succeeds:
        bench("ok")
            .with_command(["sh", "-c", "sleep 0.02"])
            .with_metric(Time())
            .with_runs(3),

        # Always fails: 3 runs, 3 recorded failures (exit 7):
        bench("broken")
            .with_command(["sh", "-c", "exit 7"])
            .with_metric(Time())
            .with_runs(3),
    )
)


if __name__ == "__main__":
    run(s)
