"""Sequential runner: drives each Benchmark's coroutine in order."""

from __future__ import annotations

from typing import Any

from benchr.grammar.suite import Suite
from benchr.report.sample import Report
from benchr.runner.base import Runner, _INTERRUPTED, install_sigint_handler, plan


class Sequential(Runner):
    """Run benchmarks one at a time, in suite-then-benchmark order."""

    def run(self, suites: Suite | list[Suite], ctx: Any = None) -> Report:
        planned = plan(suites, ctx)
        self.reporter.start([p.benchmark for p in planned])
        report = Report()
        try:
            with install_sigint_handler():
                for p in planned:
                    if _INTERRUPTED.is_set():
                        break
                    self._run_benchmark(p, ctx, report)
                if _INTERRUPTED.is_set():
                    raise KeyboardInterrupt
            return report
        finally:
            self.reporter.finalize()
