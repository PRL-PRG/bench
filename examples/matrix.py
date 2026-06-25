#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "..", editable = true }
# ///
"""Compiler / optimization matrix."""

from bench import Regex, bench, run, suite


def fake_cmd(ctx):
    # Variant values reach the callable via `ctx.matrix`.
    return [
        "sh",
        "-c",
        f"echo {ctx.matrix.compiler}-{ctx.matrix.opt}: $((RANDOM%50+50))",
    ]


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
