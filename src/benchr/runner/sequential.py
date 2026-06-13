"""Sequential runner: drives each Benchmark's coroutine in order."""

from __future__ import annotations

from typing import Any

from benchr.core.process import install_sigint_handler, interrupted
from benchr.core.sample import Report
from benchr.runner.base import PlannedBenchmark, Runner


class Sequential(Runner):
    """Run benchmarks one at a time, in suite-then-benchmark order."""

    def run(
        self, planned: list[PlannedBenchmark], params: Any = None
    ) -> Report:
        self.reporter.start([p.benchmark for p in planned])
        report = Report()
        try:
            with install_sigint_handler():
                for p in planned:
                    if interrupted():
                        break
                    self._run_benchmark(p, params, report)
                if interrupted():
                    raise KeyboardInterrupt
            return report
        finally:
            self.reporter.finalize()
