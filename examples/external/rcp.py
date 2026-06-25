#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""RCP: programmatic suite construction.

Demonstrates:
- `from_files` discovery driven by ctx (root depends on `ctx.RSH_HOME`).
- Mixing `Rebench` + `max_rss` in a pipeline.
- A typed `RcpParams` dataclass for CLI flags.
"""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from bench import Context, Rebench, from_files, max_rss, run, suite


@dataclass
class RcpParams:
    RSH_HOME: Path  # path to RSH client
    R_HOME: Path  # R installation root
    output: Path = Path(tempfile.gettempdir()) / "rcp"
    path_filter: str = ""
    iterations: int = 1  # harness --runs (avoids bench's reserved --runs)


def _cmd(ctx: Context[RcpParams]):
    p = ctx.params
    R = p.R_HOME / "bin" / "R"
    harness_bin = p.RSH_HOME / "inst" / "benchmarks" / "harness.R"
    return [
        str(R),
        "--slave",
        "--no-restore",
        "-f",
        str(harness_bin),
        "--args",
        "--output-dir",
        str(p.output),
        "--runs",
        str(p.iterations),
        "--rcp",
        str(ctx.matrix.path.with_suffix("")),
    ]


def _bench_root(ctx: RcpParams) -> Path:
    return ctx.RSH_HOME / "inst" / "benchmarks"


rcp_suite = (
    suite("RCPSuite")
    .factory(lambda ctx: from_files(_bench_root(ctx.params), pattern=r"\.R$"))
    .with_cwd(Path.cwd())
    .with_command(_cmd)
    .with_metric(Rebench())
    .with_process_metric(max_rss())
)


if __name__ == "__main__":
    run(rcp_suite, params=RcpParams)
