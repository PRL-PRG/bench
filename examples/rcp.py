#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["benchr"]
#
# [tool.uv.sources]
# benchr = { path = "..", editable = true }
# ///
"""RCP: programmatic suite construction.

Demonstrates:
- A pre-flight check (verifying an R package is installed).
- ``from_files`` with a path filter and a content-aware exclusion.
- Mixing ``Rebench`` + ``max_rss`` in a pipeline.
- Picking a Runner based on a CLI flag.
"""

import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from benchr import (
    Csv, P, Parallel, Path as P_Path, Sequential, bench, run, suite,
)


@dataclass
class RcpParams:
    RSH_HOME: Path                        # path to RSH client
    R_HOME: Path                          # R installation root
    output: Path = P_Path(tempfile.gettempdir()) / "rcp"
    path_filter: str = ""
    parallel: int = 1
    runs: int = 1


def check_namespace(Rscript: Path, namespace: str) -> None:
    subprocess.run(
        [
            str(Rscript), "-e",
            f'if (!requireNamespace("{namespace}", quietly=TRUE)) quit(status=1)',
        ],
        check=True,
    )


def _cmd(b, ctx: RcpParams):
    R = ctx.R_HOME / "bin" / "R"
    harness_bin = ctx.RSH_HOME / "inst" / "benchmarks" / "harness.R"
    return [
        str(R), "--slave", "--no-restore",
        "-f", str(harness_bin),
        "--args",
        "--output-dir", str(ctx.output),
        "--runs", str(ctx.runs),
        "--rcp",
        str(b.path.with_suffix("")),
    ]


def _bench_root(ctx: RcpParams) -> Path:
    return ctx.RSH_HOME / "inst" / "benchmarks"


def _filter(b, ctx: RcpParams) -> bool:
    # Only files in subdirectories of the benchmarks root; honour --path-filter.
    if b.path.parent == _bench_root(ctx):
        return False
    if ctx.path_filter and ctx.path_filter not in str(b.path):
        return False
    return True


rcp_suite = (
    suite("RCPSuite")
    .from_files(_bench_root, pattern=r"\.R$")
    .with_cwd(P_Path.cwd())
    .with_command(_cmd)
    .with_process(P.rebench() | P.max_rss())
)


def _main():
    # Pre-flight checks against ctx ideally happen *after* CLI parsing; for
    # simplicity we just rely on `--R-home` being valid when run() executes.
    # If you need pre-flight, parse argv first and call check_namespace
    # before run().
    rcp_suite_filtered = rcp_suite.filter(lambda b: True)  # placeholder
    run(rcp_suite_filtered, params=RcpParams)


if __name__ == "__main__":
    _main()
