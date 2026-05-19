#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Failure handling: mixed success / failure runs.

The processor pipeline runs ``P.time()`` on success and emits a ``failed=1``
sample on failure. Failed runs do not advance the FixedRuns policy, so
``runs(3)`` means "three *successful* measurements."
"""

from benchr import P, Path, bench, run, suite


s = (
    suite("flaky",
        # Always succeeds:
        bench("ok")
            .with_command(["sh", "-c", "sleep 0.02"])
            .with_cwd(Path("/tmp"))
            .with_process(P.time().on_failure(P.constant("failed", 1.0)))
            .runs(3),

        # Always fails: aborted by max_consecutive_failures (default 5):
        bench("broken")
            .with_command(["sh", "-c", "exit 7"])
            .with_cwd(Path("/tmp"))
            .with_process(P.time().on_failure(P.constant("failed", 1.0)))
            .runs(3),
    )
)


if __name__ == "__main__":
    run(s)
