#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Writing a custom Processor.

A real benchmark prints a fixed string to stdout and a metric to stderr.
Success means the fixed string appeared; the metric is parsed from a regex.
"""

import re
from typing import Iterable

from benchr import (
    PartialSample, Path, Processor, ExecutionResult, P,
    SuccessfulExecutionResult, bench, run, suite,
)


class StderrFloat(Processor):
    """Custom Processor: read 'TIME=<float>' from stderr.

    Success criterion: stdout contains 'OK' AND the process exited zero.
    """

    _re = re.compile(r"TIME=([0-9.]+)")

    def process(self, pr: ExecutionResult) -> Iterable[PartialSample]:
        if not isinstance(pr, SuccessfulExecutionResult):
            return
        m = self._re.search(pr.stderr or "")
        if m:
            yield PartialSample(metric="custom_time", value=float(m.group(1)),
                                unit="s", lower_is_better=True)

    def is_success(self, pr) -> bool:
        if not isinstance(pr, SuccessfulExecutionResult):
            return False
        return "OK" in (pr.stdout or "")


s = (
    suite("custom",
        bench("with_stderr")
            .with_command(["sh", "-c", "echo OK; echo 'TIME=0.42' >&2"])
            .with_cwd(Path("/tmp"))
            .with_process(StderrFloat() | P.time())
            .runs(3)
    )
)


if __name__ == "__main__":
    run(s)
