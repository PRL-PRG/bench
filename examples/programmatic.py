#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Programmatic usage: run a Suite *without* the CLI and iterate Samples.

This is the path to take when you want to embed benchr in a larger Python
pipeline (e.g. a Jupyter notebook or a CI script that does follow-up
analysis on the raw samples).
"""

from benchr import Time, Sequential, bench, plan, suite


s = (
    suite("prog",
        bench("a").with_command(["sh", "-c", "sleep 0.02"]),
        bench("b").with_command(["sh", "-c", "sleep 0.05"]),
    )
    .with_metric(Time())
    .with_runs(3)
)


if __name__ == "__main__":
    report = Sequential().run(plan([s], None), None)
    total = sum(len(o.samples) for r in report.runs for o in r.observations)
    print(f"Got {total} samples, {len(report.failures)} failures.")
    for r in report.runs:
        for o in r.observations:
            for sample in o.samples:
                print(f"  {r.benchmark}#{r.run}: "
                      f"{sample.metric}={sample.value:.4f}{sample.unit}")
