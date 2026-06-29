"""Formatters: composable callables that turn a `list[Stat]` into a string.

Each formatter selects a view from `bench.report.summary` and renders it — the
on-terminal ones with the rich `Renderer`, `Compact` with the plain one. The
views own all layout and the better/worse vocabulary; these classes only hold
configuration (which metric(s), which axis). Formatters compose with `&`:
`Results() & Summary()` builds one formatter whose output is the parts joined by
a blank line, so `SummaryReporter` takes a single `Formatter`.
"""

from __future__ import annotations

import abc

from bench.report.render import PLAIN, RICH
from bench.report.summary import (
    Stat,
    by_axis,
    compact,
    ranking,
    results,
)


class Formatter(abc.ABC):
    """A `list[Stat] -> str` renderer. Compose with `&`."""

    @abc.abstractmethod
    def __call__(self, stats: list[Stat]) -> str: ...

    def __and__(self, other: Formatter) -> Formatter:
        return _Composite(self, other)


class _Composite(Formatter):
    """Several formatters joined into one; their non-empty output is stitched
    together with a blank line, flattening nested `&` chains."""

    def __init__(self, *parts: Formatter) -> None:
        flat: list[Formatter] = []
        for p in parts:
            flat.extend(p.parts if isinstance(p, _Composite) else [p])
        self.parts = tuple(flat)

    def __call__(self, stats: list[Stat]) -> str:
        return "\n\n".join(o for p in self.parts if (o := p(stats)))


class _MetricFilter(Formatter):
    """Shared `metrics` filter for the stats formatters."""

    def __init__(self, metrics: set[str] | None = None) -> None:
        self.metrics = metrics


class Results(_MetricFilter):
    """Absolute `mean ± σ (min … max)` per benchmark variant."""

    def __call__(self, stats: list[Stat]) -> str:
        return "\n".join(results(stats, RICH, metrics=self.metrics))


class Summary(_MetricFilter):
    """Rank the variants within each benchmark, best first. With `axis`, fold the
    other (residual) variants within each benchmark and compare the values of that
    axis instead (e.g. `Summary(axis="vm")`); `ref` pins one value as baseline."""

    def __init__(
        self,
        metrics: set[str] | None = None,
        *,
        axis: str | None = None,
        ref: str | None = None,
    ) -> None:
        super().__init__(metrics)
        self.axis = axis
        self.ref = ref

    def __call__(self, stats: list[Stat]) -> str:
        return "\n".join(
            ranking(stats, RICH, metrics=self.metrics, axis=self.axis, ref=self.ref)
        )


class GroupedSummary(Formatter):
    """Rank the values of one matrix `axis` by the geometric mean over
    benchmarks. `ref` pins one axis value as the baseline reference (otherwise
    the best performer is used)."""

    def __init__(
        self,
        *,
        axis: str,
        metric: str | None = None,
        metrics: set[str] | None = None,
        ref: str | None = None,
    ) -> None:
        self.axis = axis
        self.metric = metric
        self.metrics = metrics
        self.ref = ref

    def __call__(self, stats: list[Stat]) -> str:
        return "\n".join(
            by_axis(
                stats,
                self.axis,
                RICH,
                metric=self.metric,
                metrics=self.metrics,
                ref=self.ref,
            )
        )


class DefaultSummary(Formatter):
    """The standard report: Results + Summary."""

    def __init__(self, metrics: set[str] | None = None) -> None:
        self._inner = Results(metrics) & Summary(metrics)

    def __call__(self, stats: list[Stat]) -> str:
        return self._inner(stats)


class Compact(Formatter):
    """One-line-per-benchmark plain-text format for commit messages / CI logs."""

    def __init__(
        self,
        metric: str | list[str],
        *,
        suite: str | None = None,
        precision: int = 2,
    ) -> None:
        self._metrics = {metric} if isinstance(metric, str) else set(metric)
        self._suite = suite
        self._precision = precision

    def __call__(self, stats: list[Stat]) -> str:
        scoped = (
            stats
            if self._suite is None
            else [s for s in stats if s.suite == self._suite]
        )
        return "\n".join(
            compact(scoped, PLAIN, metrics=self._metrics, precision=self._precision)
        )
