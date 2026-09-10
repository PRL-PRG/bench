"""Summaries: composable callables that turn `Statistics` into a renderable.

Each summary renders a view over some `bench.core.stats` model.
Composing with `&` joins the parts by a blank line.
"""

from __future__ import annotations

import abc
import itertools
from collections.abc import Sequence

from rich.console import Group

from bench.console.styling import (
    Cell,
    Span,
    Styling,
    join_blocks,
    num,
)
from bench.core.stats import (
    Counts,
    Scale,
    Stat,
    Statistics,
    scale_unit,
)
from bench.core.stats.base import BenchKey
from bench.model.benchmark import Variant, format_benchmark


class Summary(abc.ABC):
    """A `Statistics -> Group` renderer. Compose with `&`."""

    @abc.abstractmethod
    def __call__(self, stats: Statistics) -> Group: ...

    def __and__(self, other: Summary) -> Summary:
        return CompositeSummary(self, other)

    def on_metrics(self, metrics: list[str] | str) -> Summary:
        return MetricFilterSummary(self, metrics)

    def on_suite(self, suite: str) -> Summary:
        return SuiteFilterSummary(self, suite)


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


class CompositeSummary(Summary):
    """Several summaries joined into one. Their non-empty output is stitched
    together with a blank line, flattening nested `&` chains."""

    def __init__(self, *parts: Summary) -> None:
        flat: list[Summary] = []
        for p in parts:
            flat.extend(p.parts if isinstance(p, CompositeSummary) else [p])
        self.parts = tuple(flat)

    def __call__(self, stats: Statistics) -> Group:
        return join_blocks([p(stats) for p in self.parts])


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class SuiteFilterSummary(Summary):
    def __init__(self, inner: Summary, suite: str) -> None:
        super().__init__()
        self.inner = inner
        self.suite = suite

    def __call__(self, stats: Statistics) -> Group:
        return self.inner(
            Statistics(
                [s for s in stats if s.id.suite == self.suite],
                stats.counts,
            )
        )


class MetricFilterSummary(Summary):
    def __init__(self, inner: Summary, metrics: list[str] | str) -> None:
        super().__init__()
        self.inner = inner
        self.metrics = metrics

    def __call__(self, stats: Statistics) -> Group:
        return self.inner(
            Statistics(
                [s for s in stats if s.metric_key.metric in self.metrics],
                stats.counts,
            )
        )


# ---------------------------------------------------------------------------
# Formatting shared by the views
# ---------------------------------------------------------------------------


def bench_label(key: BenchKey) -> str:
    """`suite/benchmark`, collapsing the stutter when the two names match."""
    return format_benchmark(key.suite, key.benchmark, Variant())


MEAN_HEADER_LONG = [
    Cell(Span("mean")),
    Cell(Span("±")),
    Cell(Span("σ")),
    Cell(Span("")),  # Unit
]

MEAN_HEADER_SHORT = [
    Cell(Span("value")),
    Cell(Span("")),  # Unit
]

MIN_MAX_HEADER = [
    Cell(Span("min")),
    Cell(Span("…")),
    Cell(Span("max")),
]


def mean_cells_raw(mean: float, stdev: float, scale: Scale, p: int = 2) -> list[Cell]:
    return [
        Cell(Span(num(scale(mean), p), "value")),
        Cell(Span("±")),
        Cell(Span(num(scale(stdev), p), "adjustment")),
        Cell(Span(scale.unit)),
    ]


def mean_cells(
    stat: Stat, scale: Scale, p: int = 2, *, extend: bool = False
) -> list[Cell]:
    if stat.n < 2:
        return [
            Cell(Span(num(scale(stat.mean), p), "value")),
            *([Cell(Span(""))] * 2 if extend else []),
            Cell(Span(scale.unit)),
        ]

    return mean_cells_raw(stat.mean, stat.stdev, scale, p)


def _intersperse[T](xs: Sequence[T], sep: T) -> list[T]:
    return list(itertools.chain.from_iterable(zip(xs, itertools.repeat(sep))))[:-1]


def counts_cell(counts: Counts) -> Cell:
    """`(N runs, M failed)` count suffix for the by-benchmark and ranking lines.
    Warmup and failure counts appear only when nonzero."""
    parts: list[Span] = []
    if counts.warmups > 0:
        parts.append(Span(f"{counts.warmups} warmup"))

    if counts.runs > 0:
        parts.append(Span(f"{counts.runs} runs"))

    if counts.failures > 0:
        parts.append(Span(f"{counts.failures} failed", "failure"))

    if parts:
        return Cell(
            Span("("),
            *_intersperse(parts, Span(", ")),
            Span(")"),
        )
    else:
        return Cell()


def range_runs_cells(stat: Stat, scale: Scale) -> list[Cell]:
    """`(min … max)` - the range (dropped for fewer than 2
    samples) then the shared count suffix (see `counts_cell`)."""
    if stat.n >= 2:
        return [
            Cell(Span("("), Span(num(scale(stat.min)), "min")),
            Cell(Span("…")),
            Cell(Span(num(scale(stat.max)), "max"), Span(")")),
        ]
    return []


def stat_line(stats: Statistics, stat: Stat, styling: Styling = Styling.RICH) -> str:
    """One-line `mean ± σ unit (min … max) (N runs, M failed)` for a single Stat of
    `stats`, matching the by-benchmark table. Markup rather than a renderable, so
    the progress reporter can interpolate it into a line of its own."""

    scale = scale_unit(stat.mean, stat.metric_key.unit)
    return styling.collapse_cells(
        *mean_cells(stat, scale),
        *range_runs_cells(stat, scale),
        counts_cell(stats.counts.get(stat.id, Counts())),
    )
