"""Metric: extract Samples from a benchmark run.

Two kinds, distinguished by what they read:

  - `IterationMetric` parses one iteration's text into Samples.
  - `Metric` reads the whole `InvocationResult`.

Both carry an optional `direction`.
"""

from __future__ import annotations

import abc
import copy
from collections.abc import Callable, Iterable
from typing import Any, Literal, Mapping, Self

from bench.model.invocation import InvocationResult
from bench.model.results import Direction, Sample

# ---------------------------------------------------------------------------
# Metric bases
# ---------------------------------------------------------------------------


class Metric(abc.ABC):
    """A metric reads input of type `T` and emits Samples.

    `extract` parses the input. `process` applies the optional `direction`
    override. `IterationMetric` and `Metric` fix `T` to the iteration
    text and the `InvocationResult` respectively.
    """

    metric: str
    unit: str
    direction: Direction

    def __init__(
        self, metric: str, unit: str = "", direction: Direction = "uncomparable"
    ) -> None:
        self.unit = unit
        self.metric = metric
        self.direction = direction

    @abc.abstractmethod
    def process(self, data: InvocationResult) -> Iterable[Sample]: ...

    def get_sample(
        self,
        value: float,
        metric: str | None = None,
        unit: str | None = None,
        iteration: int | None = None,
        extra: Mapping[str, Any] = {},
    ) -> Sample:
        return Sample(
            metric=metric if metric is not None else self.metric,
            value=value,
            unit=unit if unit is not None else self.unit,
            direction=self.direction,
            iteration=iteration,
            extra=extra,
        )


class BuildableMetric(Metric):
    """Mixin for Metric enabling a builder syntax"""

    def lower_is_better(self) -> Self:
        o = copy.copy(self)
        o.direction = "lower better"
        return o

    def higher_is_better(self) -> Self:
        o = copy.copy(self)
        o.direction = "higher better"
        return o


# ---------------------------------------------------------------------------
# Iteration metric
# ---------------------------------------------------------------------------

# A MetricSource pulls the text an IterationMetric parses out of the
# InvocationResult.
type MetricSource = Callable[[InvocationResult], str]


def StdoutMetricSource(result: InvocationResult) -> str:
    return result.stdout or ""


def StderrMetricSource(result: InvocationResult) -> str:
    return result.stderr or ""


def as_metric_source(
    source: Literal["stdout", "stderr"] | MetricSource,
) -> MetricSource:
    """Coerce a builder-level source argument into a MetricSource callable."""
    if callable(source):
        return source

    match source:
        case "stdout":
            return StdoutMetricSource
        case "stderr":
            return StderrMetricSource
        case _:
            raise ValueError(f"unknown metric source: {source!r}")


class IterationMetric(Metric):
    """Parse one iteration's text into Samples."""

    source: MetricSource

    def __init__(
        self,
        source: MetricSource,
        metric: str,
        unit: str = "",
        direction: Direction = "uncomparable",
    ) -> None:
        super().__init__(metric, unit, direction)
        self.source = source

    @abc.abstractmethod
    def process_text(self, text: str) -> Iterable[Sample]: ...

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        text = self.source(data)
        yield from self.process_text(text)
