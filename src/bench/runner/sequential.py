"""Sequential runner: drives a Controller per benchmark, in order."""

from __future__ import annotations

from bench.core.process import interrupted
from bench.core.results import Report
from bench.builder.benchmark import Benchmark
from bench.report.reporter import Reporter
from bench.runner.base import Runner


class Sequential(Runner):
    """Run benchmarks one at a time, in suite-then-benchmark order."""

    def __init__(
        self,
        *,
        verbose: bool = False,
    ) -> None:
        super().__init__(verbose=verbose)

    def run_with_report(
        self, planned: list[Benchmark], reporter: Reporter, report: Report
    ) -> None:
        for p in planned:
            if interrupted():
                break
            p.controller.run_benchmark(p, report, reporter, self.verbose)
