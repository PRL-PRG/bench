"""Sequential runner: drives each Benchmark's coroutine in order."""

from __future__ import annotations

from typing import Any

from benchr.grammar.suite import Suite
from benchr.report.sample import Report
from benchr.runner.base import Runner, plan


class Sequential(Runner):
    """Run benchmarks one at a time, in suite-then-benchmark order."""

    def run(self, suites: list[Suite], ctx: Any) -> Report:
        planned = plan(suites, ctx)
        self.reporter.start([p.benchmark for p in planned])
        report = Report()
        try:
            for p in planned:
                self._run_benchmark(p, ctx, report)
            return report
        finally:
            self.reporter.finalize()
