"""Streaming reporter sinks."""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Iterator

from bench.model.results import Execution, Report

if TYPE_CHECKING:
    from bench.model.benchmark import Benchmark


class Reporter(abc.ABC):
    """Streaming sink for benchmark progress and results."""

    def start(self, plan: list[Benchmark]) -> None:
        pass

    def benchmark_start(self, b: Benchmark) -> None:
        pass

    def execution_done(self, execution: Execution) -> None:
        pass

    def benchmark_done(self, b: Benchmark, executions: list[Execution]) -> None:
        pass

    def finalize(self, report: Report) -> None:
        pass


class CompositeReporter(Reporter):
    """Fan out events to multiple Reporters in registration order."""

    def __init__(self, *reporters: Reporter) -> None:
        # Flatten the reporters
        def iterate(r: Reporter) -> Iterator[Reporter]:
            if isinstance(r, CompositeReporter):
                for subr in r.reporters:
                    yield from iterate(subr)
                return

            yield r

        self.reporters = list(flat_r for r in reporters for flat_r in iterate(r))

    def start(self, plan: list[Benchmark]) -> None:
        for r in self.reporters:
            r.start(plan)

    def benchmark_start(self, b: Benchmark) -> None:
        for r in self.reporters:
            r.benchmark_start(b)

    def execution_done(self, execution: Execution) -> None:
        for r in self.reporters:
            r.execution_done(execution)

    def benchmark_done(self, b: Benchmark, executions: list[Execution]) -> None:
        for r in self.reporters:
            r.benchmark_done(b, executions)

    def finalize(self, report: Report) -> None:
        for r in self.reporters:
            r.finalize(report)
