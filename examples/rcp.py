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
- ``from_files`` discovery driven by ctx (root depends on ``ctx.RSH_HOME``).
- Mixing ``Rebench`` + ``max_rss`` in a pipeline.
- A typed ``RcpParams`` dataclass for CLI flags.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from benchr import P, Path as P_Path, run, suite


@dataclass
class RcpParams:
    RSH_HOME: Path                        # path to RSH client
    R_HOME: Path                          # R installation root
    output: Path = P_Path(tempfile.gettempdir()) / "rcp"
    path_filter: str = ""
    runs: int = 1


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


rcp_suite = (
    suite("RCPSuite")
    .from_files(_bench_root, pattern=r"\.R$")
    .with_cwd(P_Path.cwd())
    .with_command(_cmd)
    .with_process(P.rebench() | P.max_rss())
)


if __name__ == "__main__":
    run(rcp_suite, params=RcpParams)
