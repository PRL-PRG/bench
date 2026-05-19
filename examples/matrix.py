#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Compiler × optimization matrix.

Each (compiler, opt-level) cell is a separate variant; ``Sample.info`` records
the cell so reporters can split by axis.
"""

from benchr import P, Path, bench, run, suite


# Stand-in: no real compiler invocation, just illustrate the matrix shape.
def fake_cmd(b, ctx, value):
    compiler, opt = value
    return ["sh", "-c", f"echo {compiler}-{opt}: $((RANDOM%50+50))"]


s = (
    suite("compile_matrix")
    .add(bench("compute"))
    .with_cwd(Path("/tmp"))
    .with_process(P.regex("size", r"(\d+)\s*$", unit="lines"))
    .matrix(
        "config",
        [("gcc", "O0"), ("gcc", "O2"), ("clang", "O0"), ("clang", "O2")],
        command=fake_cmd,
        info=lambda v: {"compiler": v[0], "opt": v[1]},
    )
    .runs(3)
)


if __name__ == "__main__":
    run(s)
