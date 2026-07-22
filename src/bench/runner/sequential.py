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
        reporter: Reporter | None = None,
        *,
        verbose: bool = False,
    ) -> None:
        super().__init__(reporter, verbose=verbose)

    def run_with_report(self, planned: list[Benchmark], report: Report) -> None:
        for p in planned:
            if interrupted():
                break
            p.controller.run_benchmark(p, report, self.reporter, self.verbose)
