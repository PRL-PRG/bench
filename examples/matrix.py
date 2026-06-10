#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Compiler × optimization matrix.

One benchmark with two matrix axes — ``.with_matrix(compiler=..., opt=...)``.
Each (compiler, opt) cell is a variant; reports compare variants within the
benchmark.
"""

from benchr import Regex, bench, run, suite


def fake_cmd(b, ctx):
    # Variant values reach the callable as attributes on ``b``.
    return ["sh", "-c", f"echo {b.compiler}-{b.opt}: $((RANDOM%50+50))"]


s = (
    suite("compile_matrix")
    .add(
        bench("compute")
        .with_command(fake_cmd)
        .with_matrix(compiler=["gcc", "clang"], opt=["O0", "O2"])
    )
    .with_metric(Regex("size", r"(\d+)\s*$", unit="lines"))
    .with_runs(3)
)


if __name__ == "__main__":
    run(s)
