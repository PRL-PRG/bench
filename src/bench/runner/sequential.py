"""Sequential runner: drives a Controller per benchmark, in order."""

from __future__ import annotations

from typing import Any

from bench.core.process import install_sigint_handler, interrupted
from bench.core.sample import Report
from bench.grammar.benchmark import Benchmark
from bench.runner.base import Runner
from bench.runner.controller import Controller


class Sequential(Runner):
    """Run benchmarks one at a time, in suite-then-benchmark order."""

    def run(self, planned: list[Benchmark], params: Any = None) -> Report:
        self.reporter.start(planned)
        report = Report()
        controller = Controller(
            self.reporter,
            max_runs_per_policy=self.max_runs_per_policy,
            max_consecutive_failures=self.max_consecutive_failures,
            verbose=self.verbose,
        )
        try:
            with install_sigint_handler():
                for p in planned:
                    if interrupted():
                        break
                    controller.run_benchmark(p, report)
                if interrupted():
                    raise KeyboardInterrupt
            return report
        finally:
            self.reporter.finalize()
