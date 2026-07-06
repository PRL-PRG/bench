#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Writing a custom metric and a custom success policy."""

import re
from collections.abc import Iterable

from bench import (
    InvocationResult,
    IterationMetric,
    Sample,
    bench,
    run,
    suite,
)


class TaggedFloat(IterationMetric):
    """Custom IterationMetric: read 'TIME=<float>' from the iteration text."""

    _re = re.compile(r"TIME=([0-9.]+)")

    def extract(self, text: str) -> Iterable[Sample]:
        m = self._re.search(text)
        if m:
            yield Sample(metric="custom_time", value=float(m.group(1)), unit="s")


def succeeded(pr: InvocationResult) -> str | None:
    """Success policy: exit zero AND stdout contains 'OK'.

    Returns `None` on success, or a failure reason string otherwise.
    """
    if pr.returncode != 0:
        return f"exit code {pr.returncode}"
    if "OK" not in (pr.stdout or ""):
        return "missing OK marker"
    return None


s = suite(
    "custom",
    bench("with_stderr")
    .with_command(["sh", "-c", "echo OK; echo 'TIME=0.42' >&2"])
    .add_metric(TaggedFloat().lower_is_better(), "stderr")
    .with_success(succeeded)
    .with_runs(3),
)


if __name__ == "__main__":
    run(s)
