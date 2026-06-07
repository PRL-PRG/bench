#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Matrix algebra cookbook: VM × input size.

One workload, ``regex``, parameterized by two axes:

  - ``vm``    ∈ {VM1, VM2}     — which virtual machine
  - ``size``  ∈ {100, 500}     — input size

The cartesian product yields four variants of the same benchmark; comparison
in the end-of-run Summary is *within* the benchmark. Four benchmarks in one
suite illustrate the four common shapes:

  1. ``full``      — full cartesian, 4 variants
  2. ``minus_one`` — full minus one cell (drop ``VM1 × 500``)
  3. ``slice_vm2`` — slice: keep only ``vm=VM2``
  4. ``slice_500`` — slice: keep only ``size=500``

The commands are fake (``sh -c "sleep …; echo …"``) but the sleep amount is
shaped so VM1 is roughly 2× slower than VM2 and bigger ``size`` is slightly
slower — enough that the Summary lines look meaningful.
"""

from benchr import FloatPerLine, max_rss, Path, bench, run, suite


def cmd(b, ctx):
    """Build a fake VM invocation.

    Axis values reach the callable as attributes on ``b`` — ``b.vm``,
    ``b.size`` — via the same ``__getattr__`` that powers ``b.path`` for
    file-discovered benchmarks. This is the only call site that needs to
    know about the axes; everything else is plumbing.
    """
    base_ms = 50 if b.vm == "VM2" else 100
    size_penalty_ms = b.size // 50
    total_ms = base_ms + size_penalty_ms
    return ["sh", "-c", f"sleep {total_ms / 1000.0}; echo {total_ms}"]


s = (
    suite("vm_matrix",
        # 1. Full cartesian — 4 variants ranked against each other.
        bench("full")
            .with_command(cmd)
            .with_matrix(vm=["VM1", "VM2"], size=[100, 500])
            .with_cwd(Path("/tmp"))
            .with_metric(FloatPerLine("ms").lower_is_better())
            .with_metric(max_rss())
            .runs(5),

        # 2. Cartesian minus one cell — 3 variants.
        bench("minus_one")
            .with_command(cmd)
            .with_matrix(vm=["VM1", "VM2"], size=[100, 500])
            .with_skip(vm="VM1", size=500)
            .with_cwd(Path("/tmp"))
            .with_metric(FloatPerLine("ms").lower_is_better())
            .runs(5),

        # 3. Slice — fix vm=VM2, vary size. 2 variants.
        bench("slice_vm2")
            .with_command(cmd)
            .with_matrix(vm=["VM1", "VM2"], size=[100, 500])
            .with_skip(lambda b: b.vm != "VM2")
            .with_cwd(Path("/tmp"))
            .with_metric(FloatPerLine("ms").lower_is_better())
            .runs(5),

        # 4. Slice — fix size=500, vary vm. 2 variants.
        bench("slice_500")
            .with_command(cmd)
            .with_matrix(vm=["VM1", "VM2"], size=[100, 500])
            .with_skip(lambda b: b.size != 500)
            .with_cwd(Path("/tmp"))
            .with_metric(FloatPerLine("ms").lower_is_better())
            .runs(5),
    )
)


if __name__ == "__main__":
    run(s)
