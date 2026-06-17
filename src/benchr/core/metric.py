"""Metric: ExecutionResult -> Iterable[Sample].

Every metric is a frozen dataclass carrying an optional ``direction``
(lower / higher / none) and an optional ``predicate`` (run only when it holds).
Concrete metrics implement ``extract``; the base ``process`` applies the
predicate (skip when false) and stamps the direction onto each Sample.
``.lower_is_better()`` / ``.higher_is_better()`` / ``.when(pred)`` return a copy
with that field set.

Each metric sets ``per_process``: ``False`` (one sample-set per run/iteration)
or ``True`` (one per whole process). ``extract_run`` / ``extract_process``
select by it; ``partition_metrics`` splits a flat list. Built-in metric
builders are exported directly from this module — instantiate them as
``Time()``, ``Regex(...)``, ``FloatPerLine(...)``, etc.
"""

from __future__ import annotations

import abc
import dataclasses
import re
import sys
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import ClassVar, Literal, Self

from benchr.core.execution import ExecutionResult
from benchr.core.sample import Sample

# None = no direction; True = lower is better; False = higher is better
# (mirrors Sample.lower_is_better).
type Direction = bool | None
type Predicate = Callable[[ExecutionResult], bool]


# ---------------------------------------------------------------------------
# Metric base
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Metric(abc.ABC):
    """ExecutionResult -> Samples, plus an optional direction and predicate.

    ``per_process`` (set by each concrete metric) decides whether it is fed once
    per run (``False``) or once per whole process (``True``).
    """

    per_process: ClassVar[bool]

    direction: Direction = dataclasses.field(default=None, kw_only=True)
    predicate: Predicate | None = dataclasses.field(default=None, kw_only=True)

    @abc.abstractmethod
    def extract(self, result: ExecutionResult) -> Iterable[Sample]:
        """Parse the raw samples; ``process`` applies direction + predicate."""

    def process(self, result: ExecutionResult) -> Iterator[Sample]:
        if self.predicate is not None and not self.predicate(result):
            return
        for s in self.extract(result):
            if self.direction is None:
                yield s
            else:
                yield dataclasses.replace(s, lower_is_better=self.direction)

    def lower_is_better(self) -> Self:
        return dataclasses.replace(self, direction=True)

    def higher_is_better(self) -> Self:
        return dataclasses.replace(self, direction=False)

    def when(self, predicate: Predicate) -> Self:
        """Run this metric only when ``predicate(result)`` is true."""
        return dataclasses.replace(self, predicate=predicate)


def extract_run(metrics: Iterable[Metric], result: ExecutionResult) -> Iterator[Sample]:
    """Run only per-run (``per_process == False``) metrics over one result."""
    for m in metrics:
        if not m.per_process:
            yield from m.process(result)


def extract_process(metrics: Iterable[Metric], result: ExecutionResult) -> Iterator[Sample]:
    """Run only per-process (``per_process == True``) metrics over one result."""
    for m in metrics:
        if m.per_process:
            yield from m.process(result)


def partition_metrics(metrics: Iterable[Metric]) -> tuple[list[Metric], list[Metric]]:
    """Split a flat list of metrics into (run_metrics, process_metrics)."""
    run = [m for m in metrics if not m.per_process]
    proc = [m for m in metrics if m.per_process]
    return run, proc


# ---------------------------------------------------------------------------
# Built-in metrics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FloatPerLine(Metric):
    """Parse non-empty lines of stdout as floats, one sample per line.

    ``line`` selects a single 1-based non-empty line (negative counts from the
    end); ``None`` (the default) parses every non-empty line. A failed run, or a
    ``line`` index out of range, emits nothing.
    """

    per_process = False

    unit: str = "s"
    metric: str = "runtime"
    line: int | None = None

    def __post_init__(self) -> None:
        if self.line == 0:
            raise ValueError("line must be non-zero")

    def extract(self, result: ExecutionResult) -> Iterable[Sample]:
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

    def last_line(self) -> Self:
        """Parse only the last non-empty line of stdout."""
        return dataclasses.replace(self, line=-1)

    def first_line(self) -> Self:
        """Parse only the first non-empty line of stdout."""
        return dataclasses.replace(self, line=1)

    def nth(self, i: int) -> Self:
        """Parse only the i-th non-empty line (1-based; negatives from the end)."""
        return dataclasses.replace(self, line=i)


@dataclass(frozen=True)
class Regex(Metric):
    """Extract metric values via a regex against stdout/stderr."""

    per_process = False

    metric: str
    regex: re.Pattern[str] | str
    output: Literal["stdout", "stderr", "both"] = dataclasses.field(
        default="stdout", kw_only=True
    )
    match_group: str | int = dataclasses.field(default=1, kw_only=True)
    transform: Callable[[str], float] = dataclasses.field(default=float, kw_only=True)
    unit: str = dataclasses.field(default="", kw_only=True)
    unit_group: str | int | None = dataclasses.field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if isinstance(self.regex, str):
            object.__setattr__(self, "regex", re.compile(self.regex))

    def extract(self, result: ExecutionResult) -> Iterable[Sample]:
        if result.is_failure():
            return
        pattern = self.regex
        assert isinstance(pattern, re.Pattern)  # compiled in __post_init__
        outs: list[str] = []
        if self.output in ("stdout", "both"):
            outs.append(result.stdout or "")
        if self.output in ("stderr", "both"):
            outs.append(result.stderr or "")
        for text in outs:
            for m in pattern.finditer(text):
                value = self.transform(m.group(self.match_group))
                unit = (
                    m.group(self.unit_group)
                    if self.unit_group is not None
                    else self.unit
                )
                yield Sample(metric=self.metric, value=value, unit=unit)


@dataclass(frozen=True)
class Rebench(Metric):
    """ReBench log format adapter.

    ``optional_prefix: name optional_criterion: iterations=N runtime: V[ms|us]``
    or ``optional_prefix: name: criterion: V<unit>``
    Runtime emitted in ms; non-"total" runtime criteria are ignored.
    """

    per_process = False

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

    def extract(self, result: ExecutionResult) -> Iterable[Sample]:
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


@dataclass(frozen=True)
class RUsage(Metric):
    """Emit one sample from a single ``resource.struct_rusage`` field."""

    per_process = True

    Field = Literal[
        "ru_utime", "ru_stime", "ru_maxrss", "ru_ixrss", "ru_idrss", "ru_isrss",
        "ru_minflt", "ru_majflt", "ru_nswap", "ru_inblock", "ru_oublock",
        "ru_msgsnd", "ru_msgrcv", "ru_nsignals", "ru_nvcsw", "ru_nivcsw",
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
class Time(Metric):
    """Up to three time samples: ``elapsed`` (wall), ``user``, ``system`` (s).

    All are lower-is-better by default; override with ``.higher_is_better()``.
    """

    per_process = True

    elapsed: bool = True
    user: bool = False
    system: bool = False
    direction: Direction = dataclasses.field(default=True, kw_only=True)

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


def max_rss() -> Metric:
    """RSS in kB, lower-is-better. macOS byte-vs-kB normalization handled."""
    return RUsage("ru_maxrss", "max_rss", "kB").lower_is_better()
