"""Processor: ExecutionResult -> Iterable[PartialSample].

A Processor parses one metric kind (or a few, like ``Time``). Attach several to
a Benchmark with ``with_process(p1, p2, ...)``; the Runner runs each over the
result and concatenates their PartialSamples (see ``process_all``). Whether a run
*succeeded* is decided by the Runner (see ``default_success`` /
``Benchmark.with_success``). Failed runs emit no metrics — the Runner records
every run as a structured ``RunRecord`` instead. Decorator-style modifiers:

  ``.lower_is_better()``  /  ``.higher_is_better()``   tag emitted samples
  ``.when(predicate)``                                 conditional emission

The Runner calls ``stamp()`` to lift a Processor's PartialSamples into fully-
identified ``Sample``s. Built-ins are exposed through the ``P`` namespace at
the bottom of the module.
"""

from __future__ import annotations

import abc
import dataclasses
import re
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Literal

from benchr.grammar.execution import (
    ExecutionResult,
    ScheduledExecution,
)
from benchr.report.sample import Sample


# ---------------------------------------------------------------------------
# PartialSample + stamp: a Processor yields identity-free PartialSamples
# (metric, value, unit, lower_is_better); the Runner calls stamp() to lift
# them into fully-identified Samples using the ScheduledExecution's
# (suite, benchmark, run, phase, info).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartialSample:
    """A measurement produced by a Processor before the Runner stamps identity."""

    metric: str
    value: float
    unit: str = ""
    lower_is_better: bool | None = None


def process_all(
    processors: Iterable[Processor], result: ExecutionResult
) -> Iterator[PartialSample]:
    """Run each processor over one result, concatenating their PartialSamples."""
    for proc in processors:
        yield from proc.process(result)


def stamp(partials: Iterable[PartialSample], sched: ScheduledExecution) -> Iterator[Sample]:
    """Lift PartialSamples to fully-identified Samples."""
    for p in partials:
        yield Sample(
            suite=sched.suite,
            benchmark=sched.benchmark,
            info=sched.info,
            run=sched.run,
            phase=sched.phase,
            metric=p.metric,
            value=p.value,
            unit=p.unit,
            lower_is_better=p.lower_is_better,
        )


# ---------------------------------------------------------------------------
# Processor base
# ---------------------------------------------------------------------------


class Processor(abc.ABC):
    """ExecutionResult -> Iterable[PartialSample].

    Override ``process``. Attach several to a Benchmark with
    ``.with_process(p1, p2, ...)``; the Runner runs each over a result and
    concatenates their samples (see ``process_all``).
    """

    # --- core hooks -----------------------------------------------------

    @abc.abstractmethod
    def process(self, result: ExecutionResult) -> Iterable[PartialSample]: ...

    # --- modifiers ------------------------------------------------------

    def lower_is_better(self) -> Processor:
        return _Direction(self, True)

    def higher_is_better(self) -> Processor:
        return _Direction(self, False)

    def when(self, predicate: Callable[[ExecutionResult], bool]) -> Processor:
        """Run this processor only when ``predicate(result)`` is true."""
        return _When(self, predicate)


# ---------------------------------------------------------------------------
# Modifier implementations
# ---------------------------------------------------------------------------


class _Direction(Processor):
    __slots__ = ("inner", "lower")

    def __init__(self, inner: Processor, lower_is_better: bool):
        self.inner = inner
        self.lower = lower_is_better

    def process(self, result: ExecutionResult) -> Iterable[PartialSample]:
        for s in self.inner.process(result):
            yield dataclasses.replace(s, lower_is_better=self.lower)


class _When(Processor):
    __slots__ = ("inner", "predicate")

    def __init__(self, inner: Processor, predicate: Callable[[ExecutionResult], bool]):
        self.inner = inner
        self.predicate = predicate

    def process(self, result: ExecutionResult) -> Iterable[PartialSample]:
        if self.predicate(result):
            yield from self.inner.process(result)


# ---------------------------------------------------------------------------
# Built-in processors
# ---------------------------------------------------------------------------


class FloatPerLine(Processor):
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

    def process(self, result: ExecutionResult) -> Iterable[PartialSample]:
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
                yield PartialSample(metric=self.metric, value=float(line), unit=self.unit)
            except ValueError:
                continue

    # Selectors return a reconfigured FloatPerLine that parses one line.

    def last_line(self) -> "FloatPerLine":
        """Parse only the last non-empty line of stdout."""
        return FloatPerLine(self.unit, self.metric, line=-1)

    def first_line(self) -> "FloatPerLine":
        """Parse only the first non-empty line of stdout."""
        return FloatPerLine(self.unit, self.metric, line=1)

    def nth(self, i: int) -> "FloatPerLine":
        """Parse only the i-th non-empty line of stdout (1-based; negatives count from the end)."""
        return FloatPerLine(self.unit, self.metric, line=i)


class Regex(Processor):
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
        unit: str | None = None,
        unit_group: _MatchGroup | None = None,
    ):
        if unit is None and unit_group is None:
            raise ValueError("Regex needs either `unit` or `unit_group`")
        self.metric = metric
        self.regex = re.compile(regex) if isinstance(regex, str) else regex
        self.output = output
        self.match_group = match_group
        self.transform = transform
        self.unit = unit
        self.unit_group = unit_group

    def process(self, result: ExecutionResult) -> Iterable[PartialSample]:
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
                unit = m.group(self.unit_group) if self.unit_group is not None else (self.unit or "")
                yield PartialSample(metric=self.metric, value=value, unit=unit)


class Rebench(Processor):
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

    def process(self, result: ExecutionResult) -> Iterable[PartialSample]:
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
                yield PartialSample(metric="runtime", value=value, unit="ms")
                continue
            m = self._re_criterion.match(line)
            if m is not None:
                yield PartialSample(
                    metric=m.group("criterion"),
                    value=float(m.group("value")),
                    unit=m.group("unit"),
                )


class RUsage(Processor):
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

    def process(self, result: ExecutionResult) -> Iterable[PartialSample]:
        if result.rusage is None:
            return
        value = float(getattr(result.rusage, self.field))
        # macOS reports ru_maxrss in bytes, not kB.
        if sys.platform == "darwin" and self.field == "ru_maxrss":
            value /= 1024.0
        yield PartialSample(metric=self.metric, value=value, unit=self.unit)


class Time(Processor):
    """Up to three time samples: ``elapsed`` (wall), ``user``, ``system`` (s)."""

    __slots__ = ("elapsed", "user", "system")

    def __init__(self, elapsed: bool = True, user: bool = False, system: bool = False):
        if not (elapsed or user or system):
            raise ValueError("Time() needs at least one of elapsed/user/system")
        self.elapsed = elapsed
        self.user = user
        self.system = system

    def process(self, result: ExecutionResult) -> Iterable[PartialSample]:
        if self.elapsed and result.runtime is not None:
            yield PartialSample(metric="elapsed", value=result.runtime, unit="s",
                                lower_is_better=True)
        if result.rusage is not None:
            if self.user:
                yield PartialSample(metric="user", value=result.rusage.ru_utime,
                                    unit="s", lower_is_better=True)
            if self.system:
                yield PartialSample(metric="system", value=result.rusage.ru_stime,
                                    unit="s", lower_is_better=True)


class Constant(Processor):
    """Always emit a fixed sample (e.g. tag every run with a constant marker)."""

    __slots__ = ("metric", "value", "unit", "lower")

    def __init__(self, metric: str, value: float, unit: str = "", lower_is_better: bool | None = None):
        self.metric = metric
        self.value = value
        self.unit = unit
        self.lower = lower_is_better

    def process(self, result: ExecutionResult) -> Iterable[PartialSample]:
        yield PartialSample(metric=self.metric, value=self.value, unit=self.unit,
                            lower_is_better=self.lower)


# ---------------------------------------------------------------------------
# `P` namespace — short builder names for users.
#
#   P.float_per_line("s")                      → FloatPerLine
#   P.float_per_line("s").last_line()          → FloatPerLine(line=-1)
#   P.float_per_line("iter", "throughput").nth(2)
#   P.regex("rt", r"runtime: (\d+) ms", unit="ms")
#   P.rebench(), P.time(), P.max_rss(), P.rusage(...)
#   P.constant("custom", 1.0)
# ---------------------------------------------------------------------------


class P:
    """Namespace of built-in processors. Use as ``P.time()``, ``P.regex(...)``."""

    @staticmethod
    def float_per_line(unit: str = "s", metric: str = "runtime") -> FloatPerLine:
        return FloatPerLine(unit=unit, metric=metric)

    @staticmethod
    def regex(
        metric: str,
        pattern: re.Pattern[str] | str,
        *,
        output: Regex._Output = "stdout",
        match_group: Regex._MatchGroup = 1,
        transform: Callable[[str], float] = float,
        unit: str | None = None,
        unit_group: Regex._MatchGroup | None = None,
    ) -> Regex:
        return Regex(metric, pattern, output=output, match_group=match_group,
                     transform=transform, unit=unit, unit_group=unit_group)

    @staticmethod
    def rebench() -> Rebench:
        return Rebench()

    @staticmethod
    def rusage(field: RUsage.Field, metric: str, unit: str = "") -> RUsage:
        return RUsage(field, metric, unit)

    @staticmethod
    def max_rss() -> Processor:
        return RUsage("ru_maxrss", "max_rss", "kB").lower_is_better()

    @staticmethod
    def time(elapsed: bool = True, user: bool = False, system: bool = False) -> Processor:
        # Time already tags its samples lower_is_better=True.
        return Time(elapsed=elapsed, user=user, system=system)

    @staticmethod
    def constant(metric: str, value: float, unit: str = "",
                 lower_is_better: bool | None = None) -> Constant:
        return Constant(metric, value, unit, lower_is_better)
