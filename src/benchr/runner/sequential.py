"""Sequential runner: drives each Benchmark's coroutine in order."""

from __future__ import annotations

from typing import Any

from benchr.grammar.suite import Suite
from benchr.report.sample import Sample
from benchr.runner.base import Runner, plan


class Sequential(Runner):
    """Run benchmarks one at a time, in suite-then-benchmark order."""

    def run(self, suites: list[Suite], ctx: Any) -> list[Sample]:
        planned = plan(suites, ctx)
        self.reporter.start([p.benchmark for p in planned])
        try:
            all_samples: list[Sample] = []
            for p in planned:
                all_samples.extend(self._run_benchmark(p, ctx))
            return all_samples
        finally:
            self.reporter.finalize()
