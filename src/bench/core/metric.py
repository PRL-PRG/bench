"""Metric: extract Samples from a benchmark run.

Two kinds, distinguished by what they read:

  - `IterationMetric` parses one iteration's text into Samples.
  - `ProcessMetric` reads the whole `ExecutionResult`.

Both carry an optional `direction` and `predicate`.
"""

from __future__ import annotations

import abc
import dataclasses
import re
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Literal, Self

from bench.core.execution import ExecutionResult
from bench.core.sample import Sample

# None = no direction, True = lower is better, False = higher is better
type Direction = bool | None

# A MetricSource pulls the text an IterationMetric parses out of the
# ExecutionResult.
type MetricSource = Callable[[ExecutionResult], str]


def StdoutMetricSource(result: ExecutionResult) -> str:
    return result.stdout or ""


def StderrMetricSource(result: ExecutionResult) -> str:
    return result.stderr or ""


def as_metric_source(
    source: Literal["stdout", "stderr"] | MetricSource,
) -> MetricSource:
    """Coerce a builder-level source argument into a MetricSource callable."""
    if source == "stdout":
        return StdoutMetricSource
    if source == "stderr":
        return StderrMetricSource
    if callable(source):
        return source
    raise ValueError(f"unknown metric source: {source!r}")


# ---------------------------------------------------------------------------
# Metric bases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MetricBase[T](abc.ABC):
    """A metric reads input of type `T` and emits Samples.

    `extract` parses the input. `process` wraps it with the optional
    `predicate` gate and the `direction` override. `IterationMetric` and
    `ProcessMetric` fix `T` to the iteration text and the `ExecutionResult`
    respectively.
    """

    direction: Direction = field(default=None, kw_only=True)
    predicate: Callable[[T], bool] | None = field(default=None, kw_only=True)

    @abc.abstractmethod
    def extract(self, data: T, /) -> Iterable[Sample]: ...

    def process(self, data: T) -> Iterator[Sample]:
        if self.predicate is not None and not self.predicate(data):
            return
        yield from self._emit(self.extract(data))

    def _emit(self, samples: Iterable[Sample]) -> Iterator[Sample]:
        for s in samples:
            if self.direction is None:
                yield s
            else:
                yield dataclasses.replace(s, lower_is_better=self.direction)

    def lower_is_better(self) -> Self:
        return dataclasses.replace(self, direction=True)

    def higher_is_better(self) -> Self:
        return dataclasses.replace(self, direction=False)

    def when(self, predicate: Callable[[T], bool]) -> Self:
        """Run this metric only when `predicate(data)` is true."""
        return dataclasses.replace(self, predicate=predicate)


@dataclass(frozen=True)
class IterationMetric(_MetricBase[str]):
    """Parse one iteration's text into Samples."""


@dataclass(frozen=True)
class ProcessMetric(_MetricBase[ExecutionResult]):
    """Read whole-process Samples from an ExecutionResult."""


# ---------------------------------------------------------------------------
# Iteration metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FloatPerLine(IterationMetric):
    """Parse non-empty lines of the iteration text as floats, one sample each.

    `line` selects a single 1-based non-empty line (negative counts from the
    end). `None` (the default) parses every non-empty line. A `line` index out
    of range emits nothing.
    """

    unit: str = "s"
    metric: str = "runtime"
    line: int | None = None

    def __post_init__(self) -> None:
        if self.line == 0:
            raise ValueError("line must be non-zero")

    def extract(self, text: str) -> Iterable[Sample]:
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
                yield Sample(metric=self.metric, value=float(line), unit=self.unit)
            except ValueError:
                continue

    def last_line(self) -> Self:
        """Parse only the last non-empty line."""
        return dataclasses.replace(self, line=-1)

    def first_line(self) -> Self:
        """Parse only the first non-empty line."""
        return dataclasses.replace(self, line=1)

    def nth(self, i: int) -> Self:
        """Parse only the i-th non-empty line (1-based, negatives from the end)."""
        return dataclasses.replace(self, line=i)


@dataclass(frozen=True)
class Regex(IterationMetric):
    """Extract metric values via a regex against the iteration text."""

    metric: str
    regex: re.Pattern[str] | str
    match_group: str | int = field(default=1, kw_only=True)
    transform: Callable[[str], float] = field(default=float, kw_only=True)
    unit: str = field(default="", kw_only=True)
    unit_group: str | int | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if isinstance(self.regex, str):
            object.__setattr__(self, "regex", re.compile(self.regex))

    def extract(self, text: str) -> Iterable[Sample]:
        pattern = self.regex
        assert isinstance(pattern, re.Pattern)  # compiled in __post_init__
        for m in pattern.finditer(text):
            value = self.transform(m.group(self.match_group))
            unit = (
                m.group(self.unit_group) if self.unit_group is not None else self.unit
            )
            yield Sample(metric=self.metric, value=value, unit=unit)


@dataclass(frozen=True)
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

    def extract(self, text: str) -> Iterable[Sample]:
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
                yield Sample(metric="runtime", value=value, unit="ms")
                continue
            m = self._re_criterion.match(line)
            if m is not None:
                yield Sample(
                    metric=m.group("criterion"),
                    value=float(m.group("value")),
                    unit=m.group("unit"),
                )


# ---------------------------------------------------------------------------
# Process metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RUsage(ProcessMetric):
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

    field: Field
    metric: str
    unit: str = ""

    def extract(self, result: ExecutionResult) -> Iterable[Sample]:
        if result.rusage is None:
            return
        value = float(getattr(result.rusage, self.field))
        # macOS reports ru_maxrss in bytes, not kB.
        if sys.platform == "darwin" and self.field == "ru_maxrss":
            value /= 1024.0
        yield Sample(metric=self.metric, value=value, unit=self.unit)


@dataclass(frozen=True)
class Time(ProcessMetric):
    """Up to three time samples: `elapsed` (wall), `user`, `system` (s).

    All are lower-is-better by default, override with `.higher_is_better()`.
    """

    elapsed: bool = True
    user: bool = False
    system: bool = False
    direction: Direction = field(default=True, kw_only=True)

    def __post_init__(self) -> None:
        if not (self.elapsed or self.user or self.system):
            raise ValueError("Time() needs at least one of elapsed/user/system")

    def extract(self, result: ExecutionResult) -> Iterable[Sample]:
        if self.elapsed and result.runtime is not None:
            yield Sample(metric="elapsed", value=result.runtime, unit="s")
        if result.rusage is not None:
            if self.user:
                yield Sample(metric="user", value=result.rusage.ru_utime, unit="s")
            if self.system:
                yield Sample(metric="system", value=result.rusage.ru_stime, unit="s")


def max_rss() -> ProcessMetric:
    """RSS in kB, lower-is-better. macOS byte-vs-kB normalization handled."""
    return RUsage("ru_maxrss", "max_rss", "kB").lower_is_better()
