#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Save runs with --json, then show or compare them.

Typical workflow:
    ./compare_baseline.py --json a.json      # save a run
    ./compare_baseline.py --show a.json       # re-render it with this reporter
    # ... change something, save b.json, then:
    bench compare a.json b.json               # diff the two, first is baseline
"""

from bench import Time, bench, run, suite


s = (
    suite(
        "cmp",
        bench("fast").with_command(["sh", "-c", "sleep 0.02"]),
        bench("slow").with_command(["sh", "-c", "sleep 0.05"]),
    )
    .with_process_metric(Time())
    .with_runs(5)
)


if __name__ == "__main__":
    run(s)
