#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""Programmatic usage: run a Suite *without* the CLI and iterate Samples.

This is the path to take when you want to embed benchr in a larger Python
pipeline (e.g. a Jupyter notebook or a CI script that does follow-up
analysis on the raw samples).
"""

from benchr import P, Path, Sequential, bench, suite


s = (
    suite("prog",
        bench("a").with_command(["sh", "-c", "sleep 0.02"]),
        bench("b").with_command(["sh", "-c", "sleep 0.05"]),
    )
    .with_cwd(Path("/tmp"))
    .with_process(P.time())
    .runs(3)
)


if __name__ == "__main__":
    samples = Sequential().run([s], ctx=None)
    print(f"Got {len(samples)} samples.")
    for sample in samples:
        print(f"  {sample.benchmark}#{sample.run}/{sample.phase}: "
              f"{sample.metric}={sample.value:.4f}{sample.unit}")
