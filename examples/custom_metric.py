#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Writing a custom Metric and a custom success policy.

A real benchmark prints a fixed string to stdout and a metric to stderr.
The Metric parses the value; the success policy (``.with_success``)
decides whether a run counts as successful. The Runner only calls
``process`` on a run it judged successful, so the Metric never has to
re-check the exit status.
"""

import re
from typing import Iterable

from benchr import (
    Execution, ExecutionResult, Metric, Sample, Time,
    bench, run, suite,
)


class StderrFloat(Metric):
    """Custom Metric: read 'TIME=<float>' from stderr."""

    _re = re.compile(r"TIME=([0-9.]+)")

    def process(self, result: ExecutionResult) -> Iterable[Sample]:
        m = self._re.search(result.stderr or "")
        if m:
            yield Sample(metric="custom_time", value=float(m.group(1)),
                         unit="s", lower_is_better=True)


def succeeded(execution: Execution, pr: ExecutionResult) -> str | None:
    """Success policy: exit zero AND stdout contains 'OK'.

    Returns ``None`` on success, or a failure reason string otherwise.
    """
    if pr.returncode != 0:
        return f"exit code {pr.returncode}"
    if "OK" not in (pr.stdout or ""):
        return "missing OK marker"
    return None


s = (
    suite("custom",
        bench("with_stderr")
            .with_command(["sh", "-c", "echo OK; echo 'TIME=0.42' >&2"])
            .with_metric(StderrFloat(), Time())
            .with_success(succeeded)
            .runs(3)
    )
)


if __name__ == "__main__":
    run(s)
