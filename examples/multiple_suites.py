#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Two suites with different processors; per-suite formatter via filter."""

from benchr import Compact, Mixed, P, Path, Summary, bench, run, suite


fast = (
    suite("fast")
    .add(bench("a").with_command(["sh", "-c", "sleep 0.01"]))
    .add(bench("b").with_command(["sh", "-c", "sleep 0.02"]))
    .with_cwd(Path("/tmp")).with_process(P.time()).runs(5)
)

slow = (
    suite("slow")
    .add(bench("x").with_command(["sh", "-c", "sleep 0.05"]))
    .add(bench("y").with_command(["sh", "-c", "sleep 0.08"]))
    .with_cwd(Path("/tmp")).with_process(P.time()).runs(5)
)


if __name__ == "__main__":
    run(
        [fast, slow],
        reporter=Mixed(
            Summary(formatter=Compact("elapsed", suite="fast")),
            Summary(formatter=Compact("elapsed", suite="slow")),
        ),
    )
