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
    bench,
    bench_app,
    suite,
)

s1 = suite("example").add(bench("fib")).add(bench("hanoi"))

s2 = (
    suite("custom_metric")
    .add(bench("zoo_batch"))
    .with_metric(
        FloatPerLine(metric="throughput", unit="iters", line=1).higher_is_better()
    )
)

# Common settings live on the app and are applied to every suite. Each suite still
# keeps whatever it sets itself (s2 keeps its throughput metric).
(
    bench_app("my benchmark")  # shown as the --help description
    .add(s1)
    .add(s2)
    .with_matrix(vm=["python3.9", "python3.14"])
    .with_command(lambda ctx: [ctx.data.vm, f"benchmarks/{ctx.benchmark}.py"])
    .with_runs(3)
    .run()
)


# vim: ft=python
