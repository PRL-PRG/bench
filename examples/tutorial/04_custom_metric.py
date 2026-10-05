#!/usr/bin/env -S uv run --script --quiet
# /// script
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
from __future__ import annotations

from bench import (
    FloatPerLine,
    StdoutMetricSource,
    Time,
    bench,
    bench_app,
    suite,
)

s1 = suite("example").add(bench("fib")).add(bench("hanoi"))

s2 = (
    suite("throughput")
    .add(bench("zoo_batch"))
    .with_metric(
        FloatPerLine(
            StdoutMetricSource, "throughput", line=1, unit="iters"
        ).higher_is_better()
    )
)

# Common settings live on the app and are applied to every suite. A setting a
# suite makes itself wins, except metrics, which combine: s1 is timed, and s2 is
# timed on top of its own throughput metric.
app = (
    bench_app("my benchmark")  # shown as the --help description
    .add(s1)
    .add(s2)
    .with_matrix(vm=["python3.9", "python3.14"])
    .with_command(lambda ctx: [ctx.data.vm, f"benchmarks/{ctx.benchmark}.py"])
    .with_metric(Time())
    .with_runs(3)
)

if __name__ == "__main__":
    app.main()


# vim: ft=python
