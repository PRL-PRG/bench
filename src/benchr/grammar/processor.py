"""Processor: ProcessResult -> Iterable[Sample].

Processors are composable with ``|`` (pipeline — both run, samples concatenated)
and have a ``is_success(pr)`` hook that decides whether a run is treated as a
success. Three decorator-style modifiers:

  ``.lower_is_better()``  /  ``.higher_is_better()``   tag emitted samples
  ``.on_failure(fn)``                                  reroute failed runs
  ``.when(predicate)``                                 conditional emission

Built-ins are exposed through the ``P`` namespace at the bottom of the module.
"""

from __future__ import annotations

import abc
import dataclasses
import re
import resource
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Literal

from benchr.grammar.execution import (
    Execution,
    FailedProcessResult,
    Phase,
    ProcessResult,
    ScheduledExecution,
    SuccessfulProcessResult,
)
from benchr.report.sample import Sample


# ---------------------------------------------------------------------------
# Tagging context — the Runner sets this before calling .process() so that
# Processors can yield "bare" partial Samples without knowing the benchmark
# identity. In practice the Runner stamps the identity onto the emitted
# Samples; processors only need to provide (metric, value, unit, lib).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PartialSample:
    """A measurement produced by a Processor before the Runner stamps identity."""

    metric: str
    value: float
    unit: str = ""
    lower_is_better: bool | None = None


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
    """ProcessResult -> Iterable[PartialSample].

    Override ``process``; optionally override ``is_success``. Compose with ``|``.
    """

    # --- core hooks -----------------------------------------------------

    @abc.abstractmethod
    def process(self, pr: ProcessResult) -> Iterable[PartialSample]: ...

    def is_success(self, pr: ProcessResult) -> bool:
        """Default: success iff the process exited 0 (no separate FailedProcessResult)."""
        return isinstance(pr, SuccessfulProcessResult)

    # --- composition ----------------------------------------------------

    def __or__(self, other: "Processor") -> "Processor":
        return _Pipeline(self, other)

    def lower_is_better(self) -> "Processor":
        return _Direction(self, True)

    def higher_is_better(self) -> "Processor":
        return _Direction(self, False)

    def on_failure(self, handler: "Processor") -> "Processor":
        """Use ``handler`` for failed runs; this processor for successful ones."""
        return _OnFailure(self, handler)

    def when(self, predicate: Callable[[ProcessResult], bool]) -> "Processor":
        """Run this processor only when ``predicate(pr)`` is true."""
        return _When(self, predicate)


# ---------------------------------------------------------------------------
# Combinator implementations
# ---------------------------------------------------------------------------


class _Pipeline(Processor):
    __slots__ = ("parts",)

    def __init__(self, *parts: Processor):
        flat: list[Processor] = []
        for p in parts:
            if isinstance(p, _Pipeline):
                flat.extend(p.parts)
            else:
                flat.append(p)
        self.parts = tuple(flat)

    def process(self, pr: ProcessResult) -> Iterable[PartialSample]:
        for p in self.parts:
            yield from p.process(pr)

    def is_success(self, pr: ProcessResult) -> bool:
        return all(p.is_success(pr) for p in self.parts)


class _Direction(Processor):
    __slots__ = ("inner", "lib")

    def __init__(self, inner: Processor, lower_is_better: bool):
        self.inner = inner
        self.lib = lower_is_better

    def process(self, pr: ProcessResult) -> Iterable[PartialSample]:
        for s in self.inner.process(pr):
            yield dataclasses.replace(s, lower_is_better=self.lib)

    def is_success(self, pr: ProcessResult) -> bool:
        return self.inner.is_success(pr)


class _OnFailure(Processor):
    __slots__ = ("ok", "fail")

    def __init__(self, ok: Processor, fail: Processor):
        self.ok = ok
        self.fail = fail

    def process(self, pr: ProcessResult) -> Iterable[PartialSample]:
        if self.ok.is_success(pr):
            yield from self.ok.process(pr)
        else:
            yield from self.fail.process(pr)

    def is_success(self, pr: ProcessResult) -> bool:
        # On-failure handler doesn't gate success; that's the OK branch's job.
        return self.ok.is_success(pr)


class _When(Processor):
    __slots__ = ("inner", "predicate")

    def __init__(self, inner: Processor, predicate: Callable[[ProcessResult], bool]):
        self.inner = inner
        self.predicate = predicate

    def process(self, pr: ProcessResult) -> Iterable[PartialSample]:
        if self.predicate(pr):
            yield from self.inner.process(pr)

    def is_success(self, pr: ProcessResult) -> bool:
        return self.inner.is_success(pr)


# ---------------------------------------------------------------------------
# Built-in processors
# ---------------------------------------------------------------------------


class FloatPerLine(Processor):
    """Parse each non-empty line of stdout as a float, emit one sample per line.

    On a failed run, emits nothing (the default success gate handles the rest).
    """

    __slots__ = ("unit", "metric")

    def __init__(self, unit: str = "s", metric: str = "runtime"):
        self.unit = unit
        self.metric = metric

    def process(self, pr: ProcessResult) -> Iterable[PartialSample]:
        if isinstance(pr, FailedProcessResult) or pr.stdout is None:
            return
        for line in pr.stdout.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                yield PartialSample(metric=self.metric, value=float(line), unit=self.unit)
            except ValueError:
                continue


class _LineSelect(Processor):
    """Run an inner Processor against one line of stdout/stderr.

    1-based positive indices (1 = first non-empty line), negative from the end.
    """

    __slots__ = ("inner", "line")

    def __init__(self, inner: Processor, line: int):
        if line == 0:
            raise ValueError("line must be non-zero")
        self.inner = inner
        self.line = line

    @staticmethod
    def _pick(text: str | None, line: int) -> str:
        if not text:
            return ""
        lines = [l for l in text.split("\n") if l.strip()]
        idx = line - 1 if line > 0 else line
        try:
            return lines[idx]
        except IndexError:
            return ""

    def process(self, pr: ProcessResult) -> Iterable[PartialSample]:
        if isinstance(pr, FailedProcessResult):
            return
        sub = SuccessfulProcessResult(
            execution=pr.execution,
            runtime=pr.runtime,
            stdout=self._pick(pr.stdout, self.line),
            stderr=self._pick(pr.stderr, self.line),
            rusage=pr.rusage,
        )
        yield from self.inner.process(sub)

    def is_success(self, pr: ProcessResult) -> bool:
        return self.inner.is_success(pr)


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

    def process(self, pr: ProcessResult) -> Iterable[PartialSample]:
        if isinstance(pr, FailedProcessResult):
            return
        outs: list[str] = []
        if self.output in ("stdout", "both"):
            outs.append(pr.stdout or "")
        if self.output in ("stderr", "both"):
            outs.append(pr.stderr or "")
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

    def process(self, pr: ProcessResult) -> Iterable[PartialSample]:
        if not isinstance(pr, SuccessfulProcessResult) or pr.stdout is None:
            return
        for line in pr.stdout.split("\n"):
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

    def process(self, pr: ProcessResult) -> Iterable[PartialSample]:
        if pr.rusage is None:
            return
        value = float(getattr(pr.rusage, self.field))
        # macOS reports ru_maxrss in bytes, not kB.
        if sys.platform == "darwin" and self.field == "ru_maxrss":
            value /= 1024.0
        yield PartialSample(metric=self.metric, value=value, unit=self.unit)


def _max_rss() -> Processor:
    return RUsage("ru_maxrss", "max_rss", "kB").lower_is_better()


class Time(Processor):
    """Up to three time samples: ``elapsed`` (wall), ``user``, ``system`` (s)."""

    __slots__ = ("elapsed", "user", "system")

    def __init__(self, elapsed: bool = True, user: bool = False, system: bool = False):
        if not (elapsed or user or system):
            raise ValueError("Time() needs at least one of elapsed/user/system")
        self.elapsed = elapsed
        self.user = user
        self.system = system

    def process(self, pr: ProcessResult) -> Iterable[PartialSample]:
        if self.elapsed and pr.runtime is not None:
            yield PartialSample(metric="elapsed", value=pr.runtime, unit="s",
                                lower_is_better=True)
        if pr.rusage is not None:
            if self.user:
                yield PartialSample(metric="user", value=pr.rusage.ru_utime,
                                    unit="s", lower_is_better=True)
            if self.system:
                yield PartialSample(metric="system", value=pr.rusage.ru_stime,
                                    unit="s", lower_is_better=True)


class Constant(Processor):
    """Always emit a fixed sample (useful in on_failure chains)."""

    __slots__ = ("metric", "value", "unit", "lib")

    def __init__(self, metric: str, value: float, unit: str = "", lower_is_better: bool | None = None):
        self.metric = metric
        self.value = value
        self.unit = unit
        self.lib = lower_is_better

    def process(self, pr: ProcessResult) -> Iterable[PartialSample]:
        yield PartialSample(metric=self.metric, value=self.value, unit=self.unit,
                            lower_is_better=self.lib)


class Fail(Processor):
    """Emit a ``failed`` flag sample for failed runs only.

    Idiomatic usage: ``processor.on_failure(Fail())`` if you want a 0/1 flag in
    the report; the default success gate already excludes failed runs from stats.
    """

    def process(self, pr: ProcessResult) -> Iterable[PartialSample]:
        if isinstance(pr, FailedProcessResult):
            yield PartialSample(metric="failed", value=1.0)

    def is_success(self, pr: ProcessResult) -> bool:
        # Always "succeed" — Fail() doesn't itself flag the run as failed.
        return True


# ---------------------------------------------------------------------------
# `P` namespace — short builder names for users.
#
#   P.float_per_line("s")                      → FloatPerLine
#   P.float_per_line("s").last_line()          → _LineSelect(...)
#   P.float_per_line("iter", "throughput").nth(2)
#   P.regex("rt", r"runtime: (\d+) ms", unit="ms")
#   P.rebench(), P.time(), P.max_rss(), P.rusage(...)
#   P.constant("custom", 1.0)
#   P.fail()
# ---------------------------------------------------------------------------


class _FloatPerLineBuilder(FloatPerLine):
    """FloatPerLine + .last_line()/.nth(i) convenience selectors."""

    def last_line(self) -> Processor:
        return _LineSelect(self, line=-1)

    def first_line(self) -> Processor:
        return _LineSelect(self, line=1)

    def nth(self, i: int) -> Processor:
        return _LineSelect(self, line=i)


class P:
    """Namespace of built-in processors. Use as ``P.time()``, ``P.regex(...)``."""

    @staticmethod
    def float_per_line(unit: str = "s", metric: str = "runtime") -> _FloatPerLineBuilder:
        return _FloatPerLineBuilder(unit=unit, metric=metric)

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
        return _max_rss()

    @staticmethod
    def time(elapsed: bool = True, user: bool = False, system: bool = False) -> Processor:
        return Time(elapsed=elapsed, user=user, system=system).lower_is_better()

    @staticmethod
    def constant(metric: str, value: float, unit: str = "",
                 lower_is_better: bool | None = None) -> Constant:
        return Constant(metric, value, unit, lower_is_better)

    @staticmethod
    def fail() -> Fail:
        return Fail()
