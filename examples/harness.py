#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Harness benchmarks: one process runs all iterations — the VM pattern.

A *harness* benchmark (``.with_harness()``) executes its command once; the
harness itself runs the iterations and prints one measurement per iteration
(here ``workloads/fakevm.py``, a fake JIT whose early iterations are slow).
The command fn derives the iteration count from the ``WARMUP``/``RUNS``
constants that also configure the policies, the metrics parse the complete
output, and each iteration becomes one run record — the first ``warmup`` of
them discarded by the summary stats.

Real-world harnesses fit the same shape: Renaissance (``-r N`` + a Regex on
its ``iteration N completed (… ms)`` lines), LevelDB's db_bench (a Regex on
``micros/op``), or any ReBench-format harness (the ``Rebench()`` metric).
"""

import sys
from pathlib import Path

from benchr import FloatPerLine, bench, run, suite

FAKEVM = Path(__file__).parent / "workloads" / "fakevm.py"


WARMUP, RUNS = 5, 10


def vm_command(ctx):
    return [sys.executable, str(FAKEVM), str(ctx.benchmark), "-n", str(WARMUP + RUNS)]


s = (
    suite("fakevm", bench("fib"), bench("sort"))
    .with_command(vm_command)
    .with_metric(FloatPerLine("ms").lower_is_better())
    .with_warmup(WARMUP)
    .with_runs(RUNS)
    .with_harness()
)


if __name__ == "__main__":
    run(s)
