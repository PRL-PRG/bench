"""Metric: extract Samples from a benchmark run.

Two kinds, distinguished by what they read:

  - `IterationMetric` parses one iteration's text into Samples.
  - `Metric` reads the whole `InvocationResult`.

Both carry an optional `direction`.
"""

from __future__ import annotations

import abc
import re
import sys
import copy
from collections.abc import Callable, Iterable
from typing import Any, Literal, Mapping, Self

from bench.core.invocation import InvocationResult
from bench.core.results import Sample

# None = no direction, True = lower is better, False = higher is better
type Direction = bool | None

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
        self, metric: str, unit: str = "", direction: Direction = None
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
            lower_is_better=self.direction,
            iteration=iteration,
            extra=extra,
        )


class BuildableMetric(Metric):
    """Mixin for Metric enabling a builder syntax"""

    def lower_is_better(self) -> Self:
        o = copy.copy(self)
        o.direction = True
        return o

    def higher_is_better(self) -> Self:
        o = copy.copy(self)
        o.direction = False
        return o


class IterationMetric(Metric):
    """Parse one iteration's text into Samples."""

    source: MetricSource

    def __init__(
        self,
        source: MetricSource,
        metric: str,
        unit: str = "",
        direction: Direction = None,
    ) -> None:
        super().__init__(metric, unit, direction)
        self.source = source

    @abc.abstractmethod
    def process_text(self, text: str) -> Iterable[Sample]: ...

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        text = self.source(data)
        yield from self.process_text(text)


class MonotonicIterationMetric(IterationMetric):
    iteration: int

    def __init__(
        self,
        source: MetricSource,
        metric: str,
        unit: str = "",
        direction: Direction = None,
    ) -> None:
        super().__init__(source, metric, unit, direction)
        self.iteration = 0

    def get_sample(
        self,
        value: float,
        metric: str | None = None,
        unit: str | None = None,
        iteration: int | None = None,
        extra: Mapping[str, Any] = {},
    ) -> Sample:
        assert iteration is None, (
            "Iteration should not be provided in MonotonicIterationMetric to get_sample"
        )

        i = self.iteration
        self.iteration += 1
        return super().get_sample(
            value=value,
            metric=metric,
            unit=unit,
            iteration=i,
            extra=extra,
        )


# ---------------------------------------------------------------------------
# Iteration metrics
# ---------------------------------------------------------------------------


class FloatPerLine(IterationMetric, BuildableMetric):
    """Parse non-empty lines of the iteration text as floats, one sample each.

    `line` selects a single 1-based non-empty line (negative counts from the
    end). `None` (the default) parses every non-empty line. A `line` index out
    of range emits nothing.
    """

    unit: str
    line: int | None

    def __init__(
        self,
        source: MetricSource,
        metric: str,
        line: int | None = None,
        unit: str = "",
        direction: Direction = None,
    ) -> None:
        super().__init__(source, metric, unit, direction)
        self.unit = unit
        self.line = line

    def process_text(self, text: str) -> Iterable[Sample]:
        if not text:
            return
        lines = [s for s in (ln.strip() for ln in text.split("\n")) if s]
        if self.line is not None:
            idx = self.line - 1 if self.line > 0 else self.line
            try:
                lines = [lines[idx]]
            except IndexError:
                return
        for line in lines:
            try:
                yield self.get_sample(value=float(line))
            except ValueError:
                continue

    @staticmethod
    def last_line(
        source: MetricSource,
        metric: str,
        unit: str = "",
        direction: Direction = None,
    ) -> FloatPerLine:
        """Parse only the last non-empty line."""
        return FloatPerLine(
            source=source, metric=metric, line=-1, unit=unit, direction=direction
        )


class Regex(IterationMetric, BuildableMetric):
    """Extract metric values via a regex against the iteration text."""

    def __init__(
        self,
        metric: str,
        regex: re.Pattern[str] | str,
        source: MetricSource,
        *,
        unit: str = "",
        direction: Direction = None,
        match_group: str | int = 1,
        transform: Callable[[str], float] = float,
        unit_group: str | int | None = None,
    ):
        super().__init__(source, metric, unit, direction)

        if isinstance(regex, str):
            self.regex = re.compile(regex)
        else:
            self.regex = regex

        self.match_group = match_group
        self.transform = transform
        self.unit_group = unit_group

    def process_text(self, text: str) -> Iterable[Sample]:
        pattern = self.regex

        for m in pattern.finditer(text):
            value = self.transform(m.group(self.match_group))
            unit = (
                m.group(self.unit_group) if self.unit_group is not None else self.unit
            )
            yield self.get_sample(value=value, unit=unit)


# TODO: criterions
# TODO: Make sure it is correct
class Rebench(IterationMetric):
    """ReBench log format adapter.

    `optional_prefix: name optional_criterion: iterations=N runtime: V[ms|us]`
    or `optional_prefix: name: criterion: V<unit>`
    Runtime emitted in ms. Non-"total" runtime criteria are ignored.
    """

    _re_runtime = re.compile(
        r"^(?:.*: )?([^\s]+)( [\w\.]+)?: iterations=([0-9]+) "
        r"runtime: (?P<runtime>(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?)"
        r"(?P<unit>[mu])s"
    )
    _re_criterion = re.compile(
        r"^(?:.*: )?([^\s]+): (?P<criterion>[^:]{1,30}):\s*"
        r"(?P<value>(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?)"
        r"(?P<unit>[a-zA-Z]+)"
    )

    iteration: int

    def __init__(
        self,
        source: MetricSource,
    ) -> None:
        super().__init__(source, "runtime", "ms", True)
        self.iteration = 0

    def process_text(self, text: str) -> Iterable[Sample]:
        if not text:
            return
        for line in text.split("\n"):
            m = self._re_runtime.match(line)
            if m is not None:
                criterion = m.group(2)
                if criterion is not None and criterion.strip() != "total":
                    continue
                value = float(m.group("runtime"))
                if m.group("unit") == "u":
                    value /= 1000.0
                yield self.get_sample(value=value)

            # m = self._re_criterion.match(line)
            # if m is not None:
            #     yield Sample(
            #         metric=m.group("criterion"),
            #         value=float(m.group("value")),
            #         unit=m.group("unit"),
            #     )


# ---------------------------------------------------------------------------
# Process metrics
# ---------------------------------------------------------------------------


class RUsage(BuildableMetric):
    """Emit one sample from a single `resource.struct_rusage` field."""

    Field = Literal[
        "ru_utime",
        "ru_stime",
        "ru_maxrss",
        "ru_ixrss",
        "ru_idrss",
        "ru_isrss",
        "ru_minflt",
        "ru_majflt",
        "ru_nswap",
        "ru_inblock",
        "ru_oublock",
        "ru_msgsnd",
        "ru_msgrcv",
        "ru_nsignals",
        "ru_nvcsw",
        "ru_nivcsw",
    ]

    def __init__(
        self, field: Field, metric: str, unit: str = "", direction: Direction = None
    ) -> None:
        super().__init__(metric, unit, direction)
        self.field = field

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        if data.rusage is None:
            return
        value = float(getattr(data.rusage, self.field))
        # macOS reports ru_maxrss in bytes, not kB.
        if sys.platform == "darwin" and self.field == "ru_maxrss":
            value /= 1024.0
        yield self.get_sample(value=value, unit=self.unit)


class Time(Metric):
    """Up to three time samples: `elapsed` (wall), `user`, `system` (s).

    All are lower-is-better by default, override with `.higher_is_better()`.
    """

    def __init__(self) -> None:
        super().__init__("elapsed", "s", True)

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        if data.runtime is not None:
            yield self.get_sample(value=data.runtime)


class UserTime(Metric):
    def __init__(self) -> None:
        super().__init__("user", "s", True)

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        if data.rusage is not None:
            yield self.get_sample(value=data.rusage.ru_utime)


class SystemTime(Metric):
    def __init__(self) -> None:
        super().__init__("system", "s", True)

    def process(self, data: InvocationResult) -> Iterable[Sample]:
        if data.rusage is not None:
            yield self.get_sample(value=data.rusage.ru_stime)


def max_rss() -> Metric:
    """RSS in kB, lower-is-better. macOS byte-vs-kB normalization handled."""
    return RUsage("ru_maxrss", "max_rss", "kB").lower_is_better()
