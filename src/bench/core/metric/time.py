from __future__ import annotations

from collections.abc import Iterable

from bench.core.metric.base import (
    Metric,
)
from bench.model.invocation import InvocationResult
from bench.model.results import Sample


class Time(Metric):
    """Up to three time samples: `elapsed` (wall), `user`, `system` (s).

    All are lower-is-better by default, override with `.higher_is_better()`.
    """

    def __init__(self) -> None:
        super().__init__("elapsed", "s", "lower better")

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        yield self.get_sample(value=data.runtime)


class UserTime(Metric):
    def __init__(self) -> None:
        super().__init__("user", "s", "lower better")

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        if data.rusage is not None:
            yield self.get_sample(value=data.rusage.ru_utime)


class SystemTime(Metric):
    def __init__(self) -> None:
        super().__init__("system", "s", "lower better")

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        if data.rusage is not None:
            yield self.get_sample(value=data.rusage.ru_stime)
