"""Metric: extract Samples from a benchmark run.

Two kinds, distinguished by what they read:

  - `Metric` reads the whole `InvocationResult`.
  - `IterationMetric` parses the text one `MetricSource` pulls out of it
    (stdout by default) into per-iteration Samples.

Both carry a `direction` saying which way is better.
"""

from __future__ import annotations

import abc
import copy
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal, Self

from bench.model.invocation import InvocationResult
from bench.model.results import Direction, Sample

# ---------------------------------------------------------------------------
# Metric bases
# ---------------------------------------------------------------------------


class Metric(abc.ABC):
    """Reads one run's `InvocationResult` and emits Samples.

    Implement `process`; build the Samples with `get_sample`, which fills in the
    metric name, unit and direction configured here.
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
    """A Metric whose direction can be set fluently, returning a copy."""

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
