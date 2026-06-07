#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Failure handling: mixed success / failure runs.

Failed runs emit no metrics — benchr records each as a structured failure that
the summary lists in a ``Failures:`` block. ``.runs(N)`` counts every attempt,
so ``broken`` runs exactly 3 times and reports 3 failures (no fake timings).
"""

from benchr import Time, Path, bench, run, suite


s = (
    suite("flaky",
        # Always succeeds:
        bench("ok")
            .with_command(["sh", "-c", "sleep 0.02"])
            .with_cwd(Path("/tmp"))
            .with_metric(Time())
            .runs(3),

        # Always fails: 3 runs, 3 recorded failures (exit 7):
        bench("broken")
            .with_command(["sh", "-c", "exit 7"])
            .with_cwd(Path("/tmp"))
            .with_metric(Time())
            .runs(3),
    )
)


if __name__ == "__main__":
    run(s)
