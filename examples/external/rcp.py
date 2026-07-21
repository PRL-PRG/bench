#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.12"
# dependencies = ["bench"]
#
# [tool.uv.sources]
# bench = { path = "../..", editable = true }
# ///
"""RCP: programmatic suite construction with ctx-driven `from_files` discovery."""

import tempfile
from dataclasses import dataclass
from pathlib import Path

from bench import (
    Context,
    Rebench,
    SharedBenchParams,
    bench_app,
    from_files,
    max_rss,
    suite,
)


@dataclass(frozen=True)
class RcpParams(SharedBenchParams):
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
        str(ctx.data.path.with_suffix("")),
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
    bench_app(params=RcpParams).add_all(rcp_suite).run()
