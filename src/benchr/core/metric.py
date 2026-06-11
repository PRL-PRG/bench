"""Metric: ExecutionResult -> Iterable[Sample].

A Metric parses one or more measurements from an ExecutionResult. The Runner
calls ``extract_all(metrics, result)`` to get the per-execution Samples, which
the Reporter pairs with the ScheduledExecution identity. Built-in metric
builders are exported directly from this module — instantiate them as
``Time()``, ``Regex(...)``, ``FloatPerLine(...)``, etc.
"""

from __future__ import annotations

import abc
import dataclasses
import re
import sys
from collections.abc import Callable, Iterable, Iterator
from typing import Literal

from benchr.core.execution import ExecutionResult
from benchr.core.sample import Sample


def extract_all(metrics: Iterable[Metric], result: ExecutionResult) -> Iterator[Sample]:
    """Run each metric over one result, concatenating their Samples."""
    for m in metrics:
        yield from m.process(result)


# ---------------------------------------------------------------------------
# Metric base
# ---------------------------------------------------------------------------


class Metric(abc.ABC):
    """ExecutionResult -> Iterable[Sample]."""

    __slots__ = ()

    @abc.abstractmethod
    def process(self, result: ExecutionResult) -> Iterable[Sample]: ...

    def lower_is_better(self) -> Metric:
        return _Direction(self, True)

    def higher_is_better(self) -> Metric:
        return _Direction(self, False)

    def when(self, predicate: Callable[[ExecutionResult], bool]) -> Metric:
        """Run this metric only when ``predicate(result)`` is true."""
        return _When(self, predicate)


class _Direction(Metric):
    __slots__ = ("inner", "lower")

    def __init__(self, inner: Metric, lower_is_better: bool):
        self.inner = inner
        self.lower = lower_is_better

    def process(self, result: ExecutionResult) -> Iterable[Sample]:
        for s in self.inner.process(result):
            yield dataclasses.replace(s, lower_is_better=self.lower)


class _When(Metric):
    __slots__ = ("inner", "predicate")

    def __init__(self, inner: Metric, predicate: Callable[[ExecutionResult], bool]):
        self.inner = inner
        self.predicate = predicate

    def process(self, result: ExecutionResult) -> Iterable[Sample]:
        if self.predicate(result):
            yield from self.inner.process(result)


# ---------------------------------------------------------------------------
# Built-in metrics
# ---------------------------------------------------------------------------


class FloatPerLine(Metric):
    """Parse non-empty lines of stdout as floats, emit one sample per line.

    ``line`` selects a single 1-based non-empty line (negative counts from the
    end); ``None`` (the default) parses every non-empty line. A failed run, or a
    ``line`` index out of range, emits nothing.
    """

    __slots__ = ("unit", "metric", "line")

    def __init__(self, unit: str = "s", metric: str = "runtime", line: int | None = None):
        if line == 0:
            raise ValueError("line must be non-zero")
        self.unit = unit
        self.metric = metric
        self.line = line

    def process(self, result: ExecutionResult) -> Iterable[Sample]:
        if result.is_failure() or not result.stdout:
            return
        lines = [s for s in (ln.strip() for ln in result.stdout.split("\n")) if s]
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

    def last_line(self) -> "FloatPerLine":
        """Parse only the last non-empty line of stdout."""
        return FloatPerLine(self.unit, self.metric, line=-1)

    def first_line(self) -> "FloatPerLine":
        """Parse only the first non-empty line of stdout."""
        return FloatPerLine(self.unit, self.metric, line=1)

    def nth(self, i: int) -> "FloatPerLine":
        """Parse only the i-th non-empty line of stdout (1-based; negatives count from the end)."""
        return FloatPerLine(self.unit, self.metric, line=i)


class Regex(Metric):
    """Extract metric values via a regex against stdout/stderr."""

    type _MatchGroup = str | int
    type _Output = Literal["stdout", "stderr", "both"]

    __slots__ = ("metric", "regex", "output", "match_group", "transform",
                 "unit", "unit_group")

    def __init__(
        self,
        metric: str,
        regex: re.Pattern[str] | str,
        *,
        output: _Output = "stdout",
        match_group: _MatchGroup = 1,
        transform: Callable[[str], float] = float,
        unit: str = "",
        unit_group: _MatchGroup | None = None,
    ):
        self.metric = metric
        self.regex = re.compile(regex) if isinstance(regex, str) else regex
        self.output = output
        self.match_group = match_group
        self.transform = transform
        self.unit = unit
        self.unit_group = unit_group

    def process(self, result: ExecutionResult) -> Iterable[Sample]:
        if result.is_failure():
            return
        outs: list[str] = []
        if self.output in ("stdout", "both"):
            outs.append(result.stdout or "")
        if self.output in ("stderr", "both"):
            outs.append(result.stderr or "")
        for text in outs:
            for m in self.regex.finditer(text):
                value = self.transform(m.group(self.match_group))
                unit = m.group(self.unit_group) if self.unit_group is not None else self.unit
                yield Sample(metric=self.metric, value=value, unit=unit)


class Rebench(Metric):
    """ReBench log format adapter.

    ``optional_prefix: name optional_criterion: iterations=N runtime: V[ms|us]``
    or ``optional_prefix: name: criterion: V<unit>``
    Runtime emitted in ms; non-"total" runtime criteria are ignored.
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

    def process(self, result: ExecutionResult) -> Iterable[Sample]:
        if result.is_failure() or not result.stdout:
            return
        for line in result.stdout.split("\n"):
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


class RUsage(Metric):
    """Emit one sample from a single ``resource.struct_rusage`` field."""

    Field = Literal[
        "ru_utime", "ru_stime", "ru_maxrss", "ru_ixrss", "ru_idrss", "ru_isrss",
        "ru_minflt", "ru_majflt", "ru_nswap", "ru_inblock", "ru_oublock",
        "ru_msgsnd", "ru_msgrcv", "ru_nsignals", "ru_nvcsw", "ru_nivcsw",
    ]

    __slots__ = ("field", "metric", "unit")

    def __init__(self, field: Field, metric: str, unit: str = ""):
        self.field = field
        self.metric = metric
        self.unit = unit

    def process(self, result: ExecutionResult) -> Iterable[Sample]:
        if result.rusage is None:
            return
        value = float(getattr(result.rusage, self.field))
        # macOS reports ru_maxrss in bytes, not kB.
        if sys.platform == "darwin" and self.field == "ru_maxrss":
            value /= 1024.0
        yield Sample(metric=self.metric, value=value, unit=self.unit)


class Time(Metric):
    """Up to three time samples: ``elapsed`` (wall), ``user``, ``system`` (s)."""

    __slots__ = ("elapsed", "user", "system")

    def __init__(self, elapsed: bool = True, user: bool = False, system: bool = False):
        if not (elapsed or user or system):
            raise ValueError("Time() needs at least one of elapsed/user/system")
        self.elapsed = elapsed
        self.user = user
        self.system = system

    def process(self, result: ExecutionResult) -> Iterable[Sample]:
        if self.elapsed and result.runtime is not None:
            yield Sample(metric="elapsed", value=result.runtime, unit="s",
                         lower_is_better=True)
        if result.rusage is not None:
            if self.user:
                yield Sample(metric="user", value=result.rusage.ru_utime,
                             unit="s", lower_is_better=True)
            if self.system:
                yield Sample(metric="system", value=result.rusage.ru_stime,
                             unit="s", lower_is_better=True)


class Constant(Metric):
    """Always emit a fixed sample (e.g. tag every run with a constant marker)."""

    __slots__ = ("metric", "value", "unit", "lower")

    def __init__(self, metric: str, value: float, unit: str = "", lower_is_better: bool | None = None):
        self.metric = metric
        self.value = value
        self.unit = unit
        self.lower = lower_is_better

    def process(self, result: ExecutionResult) -> Iterable[Sample]:
        yield Sample(metric=self.metric, value=self.value, unit=self.unit,
                     lower_is_better=self.lower)


def max_rss() -> Metric:
    """RSS in kB, lower-is-better. macOS byte-vs-kB normalization handled."""
    return RUsage("ru_maxrss", "max_rss", "kB").lower_is_better()
