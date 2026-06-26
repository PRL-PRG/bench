#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Skipping and slicing matrix cells.

One workload parameterized by `vm` by `size`. Three benchmarks show the
common skip shapes:

  1. `minus_one`: full cartesian minus one cell (drop `VM1` x `500`)
  2. `slice_vm2`: keep only `vm=VM2` (predicate skip)
  3. `slice_500`: keep only `size=500` (predicate skip)

Commands are fake `sh -c` snippets (a sleep then an echo) shaped so VM1 is roughly 2x slower
than VM2 and bigger `size` slightly slower.
"""

from bench import FloatPerLine, bench, run, suite


def cmd(ctx):
    # Axis values reach the callable via `ctx.matrix` (ctx.matrix.vm, ...).
    base_ms = 50 if ctx.matrix.vm == "VM2" else 100
    total_ms = base_ms + ctx.matrix.size // 50
    return ["sh", "-c", f"sleep {total_ms / 1000.0}; echo {total_ms}"]


s = (
    suite(
        "matrix_skips",
        # 1. Cartesian minus one cell - 3 variants.
        bench("minus_one")
        .with_command(cmd)
        .with_matrix(vm=["VM1", "VM2"], size=[100, 500])
        .add_matrix_skip(vm="VM1", size=500),
        # 2. Slice - fix vm=VM2, vary size. 2 variants.
        bench("slice_vm2")
        .with_command(cmd)
        .with_matrix(vm=["VM1", "VM2"], size=[100, 500])
        .add_matrix_skip(lambda b: b.vm != "VM2"),
        # 3. Slice - fix size=500, vary vm. 2 variants.
        bench("slice_500")
        .with_command(cmd)
        .with_matrix(vm=["VM1", "VM2"], size=[100, 500])
        .add_matrix_skip(lambda b: b.size != 500),
    )
    .with_metric(FloatPerLine("ms").lower_is_better())
    .with_runs(5)
)


if __name__ == "__main__":
    run(s)
