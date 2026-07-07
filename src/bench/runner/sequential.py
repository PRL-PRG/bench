"""Sequential runner: drives a Controller per benchmark, in order."""

from __future__ import annotations

from typing import Any

from bench.core.process import interrupted
from bench.core.results import Report
from bench.builder.benchmark import Benchmark
from bench.runner.base import Runner
from bench.runner.controller import Controller


class Sequential(Runner):
    """Run benchmarks one at a time, in suite-then-benchmark order."""

    def run(self, planned: list[Benchmark], params: Any = None) -> Report:
        with self._session(planned) as report:
            controller = Controller(self.reporter, verbose=self.verbose)
            for p in planned:
                if interrupted():
                    break
                controller.run_benchmark(p, report)
        return report
