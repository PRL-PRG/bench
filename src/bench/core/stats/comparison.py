""" "<subject> was N× better than <target>", shared by both rankings"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from bench.core.stats.base import (
    BenchKey,
    BenchmarkId,
    Counts,
    Delta,
    Stat,
    Statistics,
    geomean_ratio,
    ratio,
)
from bench.error import BenchError
from bench.model.benchmark import Variant, format_variant_pairs

type AxisIssueReason = Literal["absent", "incomplete", "never_combined", "bad_ref"]


@dataclass(frozen=True, slots=True)
class AxisIssue:
    """Why an axis view could not do what was asked of it."""

    axes: tuple[str, ...]
    reason: AxisIssueReason
    missing: tuple[str, ...] = ()
    ref: str | None = None


@dataclass(frozen=True, slots=True)
class ComparisonEntry:
    """One `N× better than <target>` line. `counts` is omitted where a folded
    geomean makes per-variant run counts meaningless."""

    delta: Delta
    target: str
    counts: Counts | None = None


@dataclass(frozen=True, slots=True)
class ComparisonBlock:
    """`subject` compared against every entry, over one metric. `scope` is the
    benchmark the ranking is within, or the suite an axis was folded over;
    `axes` is empty for a plain within-benchmark ranking."""

    scope: BenchKey | str
    axes: tuple[str, ...]
    metric: str
    subject: str
    entries: list[ComparisonEntry]


@dataclass(frozen=True, slots=True)
class ComparisonStats:
    blocks: list[ComparisonBlock] = field(default_factory=list[ComparisonBlock])
    issues: list[AxisIssue] = field(default_factory=list[AxisIssue])


def compute_ranking(
    stats: Statistics,
    *,
    axis: str | Sequence[str] | None = None,
    ref: str | None = None,
) -> ComparisonStats:
    """Per benchmark: rank the variants best-first. With `axis`, instead fold the
    other (residual) variants by geomean and compare the values of that axis
    (e.g. python3.14 vs python3.9). Several axis names make one composite axis
    (see `axes_of`), and `ref` names one of its values."""
    if axis is not None:
        return _axis_summary(
            stats,
            _axes_of(axis),
            key=lambda s: (s.id.suite, s.id.benchmark, s.metric_key),
            scope=lambda s: s.id.bench,
            ref=ref,
        )
    blocks: list[ComparisonBlock] = []
    for _bench, grp in stats.by_benchmark().items():
        for mk, rows in grp.by_metric().items():
            ranked = list(s for s in rows if s.metric_key.direction != "uncomparable")
            if len(ranked) < 2:
                continue
            lib = ranked[0].metric_key.direction == "lower better"
            ranked.sort(key=lambda s: s.median, reverse=not lib)
            best = ranked[0]
            entries: list[ComparisonEntry] = []
            for s in ranked[1:]:
                rr = ratio(s, best)  # best relative to s -> reads "better"
                if rr is None:
                    continue
                entries.append(
                    ComparisonEntry(
                        Delta.of(*rr),
                        s.id.variant_label,
                        # `grp` came out of a grouping, which does not carry the
                        # counters - they belong to the whole Statistics.
                        stats.counts.get(s.id, Counts()),
                    )
                )
            if entries:
                blocks.append(
                    ComparisonBlock(
                        scope=best.id.bench,
                        axes=(),
                        metric=mk.metric,
                        subject=best.id.variant_label,
                        entries=entries,
                    )
                )
    return ComparisonStats(blocks)


def _axes_of(axis: str | Sequence[str]) -> tuple[str, ...]:
    """One axis name or several, normalised. Several are treated as one composite
    axis whose values are their combinations (`version` + `mode`)."""
    axes = (axis,) if isinstance(axis, str) else tuple(axis)
    if not axes:
        raise BenchError("axis must name at least one matrix dimension")
    return axes


def _axis_label(key: Variant) -> str:
    """One axis value on its own (the header already names the axis), a composite
    coordinate as the usual `name=value` list."""
    if len(key) == 1:
        return key.pairs[0][1]
    return format_variant_pairs(key)


def _ref_key(ref: str, axes: tuple[str, ...]) -> Variant | None:
    """`ref` as a coordinate on `axes`: for a single axis a bare value (so a value
    that itself contains `=` still works), for a composite one a `name=value` list
    naming every axis, in any order. None when it names no cell of `axes`."""
    if len(axes) == 1:
        return Variant(((axes[0], ref.removeprefix(f"{axes[0]}=")),))
    parts = [p.split("=", 1) for p in ref.split(",") if "=" in p]
    given = {k.strip(): v.strip() for k, v in parts}
    if set(given) != set(axes):
        return None
    return Variant(tuple((a, given[a]) for a in axes))


def compute_by_axis(
    stats: Statistics,
    *,
    axis: str | Sequence[str],
    ref: str | None = None,
) -> ComparisonStats:
    """Per suite: rank the values of `axis` by the geomean over its benchmarks.

    Several axis names make one composite axis, so `["version", "mode"]` ranks
    each `version=…, mode=…` combination on its own instead of averaging the
    modes into each version's number. `ref` names one of those combinations."""
    return _axis_summary(
        stats,
        _axes_of(axis),
        key=lambda s: (s.id.suite, s.metric_key),
        scope=lambda s: s.id.suite,
        ref=ref,
    )


def _axis_key(id: BenchmarkId, axes: tuple[str, ...]) -> Variant | None:
    """This variant projected onto `axes`, or None unless it has a value for every
    one of them."""
    key: list[tuple[str, str]] = []
    for a in axes:
        v = id.variant.get(a)
        if v is None:
            return None
        key.append((a, v))
    return Variant(tuple(key))


def _axis_summary(
    stats: Statistics,
    axes: tuple[str, ...],
    *,
    key: Callable[[Stat], Hashable],
    scope: Callable[[Stat], BenchKey | str],
    ref: str | None,
) -> ComparisonStats:
    """Shared engine for the axis views: group the axial stats by `key`, then fold
    each group over `axes`. Drives both the per-benchmark ranking-by-axis and the
    per-suite `compute_by_axis`."""
    axial = Statistics(
        [s for s in stats if _axis_key(s.id, axes) is not None], stats.counts
    )
    if not axial:
        return ComparisonStats(issues=[_axis_missing(axes, stats)])
    issues: list[AxisIssue] = []
    rkey = _ref_key(ref, axes) if ref is not None else None
    # A `ref` no group can match is a typo; a group merely lacking it falls back to
    # its own best performer, as always.
    if ref is not None and all(_axis_key(s.id, axes) != rkey for s in axial):
        issues.append(AxisIssue(axes, "bad_ref", ref=ref))
    blocks: list[ComparisonBlock] = []
    for grp in axial.group_by(key).values():
        block = _axis_block(grp, axes, scope=scope(grp.first), ref=rkey)
        if block is not None:
            blocks.append(block)
    return ComparisonStats(blocks, issues)


def _axis_missing(axes: tuple[str, ...], stats: Statistics) -> AxisIssue:
    """Tell apart an axis whose names are absent everywhere from one whose names
    all occur but never together in one variant."""
    present = {k for s in stats for k, _ in s.id.variant}
    absent = tuple(a for a in axes if a not in present)
    if not absent:
        return AxisIssue(axes, "never_combined")
    if len(absent) == len(axes):
        return AxisIssue(axes, "absent", absent)
    return AxisIssue(axes, "incomplete", absent)


def _axis_block(
    grp: Statistics,
    axes: tuple[str, ...],
    *,
    scope: BenchKey | str,
    ref: Variant | None,
) -> ComparisonBlock | None:
    """Fold `grp` by `axes` - geomean over the residual variants, matched pairwise -
    and compare the axis values best-first, or against `ref` if that value is
    present. None when fewer than two axis values line up."""
    # axis coordinate -> {(benchmark, residual variant): Stat}
    byval: dict[Variant, dict[tuple[str, Variant], Stat]] = {}
    for s in grp:
        v = _axis_key(s.id, axes)
        assert v is not None
        byval.setdefault(v, {})[(s.id.benchmark, s.id.residual(axes))] = s
    if len(byval) < 2:
        return None
    lib = grp.first.metric_key.direction == "lower better"
    scores = {
        v: statistics.geometric_mean([st.median for st in m.values()])
        for v, m in byval.items()
    }
    if ref is not None and ref in byval:
        ref_val = ref
    else:
        ref_val = (min if lib else max)(scores, key=lambda v: scores[v])
    ref_map = byval[ref_val]

    scored: list[tuple[float, ComparisonEntry]] = []
    for v, m in byval.items():
        if v == ref_val:
            continue
        pairs = [
            p for k, st in m.items() if k in ref_map and (p := ratio(st, ref_map[k]))
        ]
        if not pairs:
            continue
        geo, sig = geomean_ratio(pairs)
        scored.append((geo, ComparisonEntry(Delta.of(geo, sig), _axis_label(v))))
    scored.sort(key=lambda e: e[0])  # closest to the reference first
    if not scored:
        return None
    return ComparisonBlock(
        scope=scope,
        axes=axes,
        metric=grp.first.metric_key.metric,
        subject=_axis_label(ref_val),
        entries=[e for _, e in scored],
    )
