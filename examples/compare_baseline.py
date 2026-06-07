#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Run with --json out.json to save, then --compare out.json to diff.

Typical workflow:
    ./compare_baseline.py --json baseline.json
    ./compare_baseline.py --compare baseline.json
"""

from benchr import Time, Path, bench, run, suite


s = (
    suite("cmp",
        bench("fast").with_command(["sh", "-c", "sleep 0.02"]),
        bench("slow").with_command(["sh", "-c", "sleep 0.05"]),
    )
    .with_cwd(Path("/tmp"))
    .with_metric(Time())
    .runs(5)
)


if __name__ == "__main__":
    run(s)
