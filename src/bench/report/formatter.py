"""Formatters: turn a Report (or pre-computed SummaryData) into a string.

The Reporter sinks (CsvReporter/JsonReporter/DirReporter) handle raw output. Formatters are for
human-readable summaries. They are pure: `format(report, baseline=...) -> str`.
"""

from __future__ import annotations

import abc
import math
import statistics
from pathlib import Path

from bench.core.execution import format_variant
from bench.core.sample import Report
from bench.report.stats import (
    BenchKey,
    BenchmarkGroup,
    BenchmarkId,
    GeoMeanRatio,
    GroupStats,
    MetricKey,
    MetricRatio,
    MetricStats,
    RunCounts,
    SummaryData,
    build_summary,
    geomean,
    geomean_with_sigma,
    metric_ratio,
    scale_unit,
)


class Formatter(abc.ABC):
    """Pure: render a Report -> text."""

    def __call__(self, report: Report, *, baseline: list[Path] | None = None) -> str:
        data = build_summary(report, baseline)
        return self.format(data)

    @abc.abstractmethod
    def format(self, data: SummaryData) -> str: ...


# ---------------------------------------------------------------------------
# DefaultSummary
# ---------------------------------------------------------------------------


def _orient(display_ratio: float, sigma: float) -> tuple[float, float, str]:
    if display_ratio >= 1:
        return display_ratio, sigma, "better"
    inv = 1.0 / display_ratio
    inv_sigma = sigma / (display_ratio**2)
    return inv, inv_sigma, "worse"


def _count_markup(rc: RunCounts) -> str:
    """Render fail|success counts with markup. Single token, pipe separator."""
    f_s = f"[bench.failure]{rc.failures}[/]" if rc.failures else str(rc.failures)
    s_s = f"[bench.success]{rc.successes}[/]"
    return f"{f_s}|{s_s}"


def _variant_suffix(gs: GroupStats | BenchmarkGroup) -> str:
    """Render the variant portion of a display name.

    Prefers the explicit `variant_label` (set via `Benchmark.with_label`).
    Falls back to `" (k=v, …)"` for unlabeled dimensions.
    """
    if gs.variant_label:
        return f"/{gs.variant_label}"
    return format_variant(gs.variant)


def _group_label(gs: GroupStats | BenchmarkGroup) -> str:
    """`suite/benchmark`, collapsing the stutter when both names match."""
    if gs.suite == gs.benchmark:
        return gs.benchmark
    return f"{gs.suite}/{gs.benchmark}"


def _variant_name(gs: GroupStats) -> str:
    """Render only the variant identifier (no benchmark/suite prefix)."""
    if gs.variant_label:
        return gs.variant_label
    formatted = format_variant(gs.variant)
    return formatted.strip(" ()") if formatted else gs.benchmark


class DefaultSummary(Formatter):
    """Per-benchmark stats + per-suite geomean comparison."""

    def __init__(self, metrics: set[str] | None = None) -> None:
        self.metrics = metrics

    def _include(self, mk: MetricKey) -> bool:
        return self.metrics is None or mk[0] in self.metrics

    def format(self, data: SummaryData) -> str:
        lines: list[str] = []
        if data.groups:
            lines.append("")
            for i, gs in enumerate(data.groups):
                if i > 0:
                    lines.append("")
                self._fmt_group(gs, lines)
        self._fmt_ranking(data, lines)
        if data.baseline is not None:
            self._fmt_comparison(data, lines)
        return "\n".join(lines)

    # ----- group block ----------------------------------------------

    def _fmt_group(self, gs: GroupStats, lines: list[str]) -> None:
        name = f"{_group_label(gs)}{_variant_suffix(gs)}"

        rc = gs.run_counts
        total = rc.failures + rc.successes
        word = "run" if total <= 1 else "runs"
        lines.append(f"[bench.label]{name}:[/] {_count_markup(rc)} {word}")

        if not gs.metrics:
            return
        scaled: dict[MetricKey, tuple[float, str]] = {}
        for mk, ms in gs.metrics.items():
            if not self._include(mk):
                continue
            scaled[mk] = scale_unit(ms.mean, ms.unit)
        if not scaled:
            return

        multi = total > 1
        suffix = " (mean ± σ):" if multi else ":"
        labels = {
            mk: f"{mk[0]}{f' [{u}]' if (u := scaled[mk][1]) else ''}{suffix}"
            for mk in scaled
        }
        max_w = max(len(l) for l in labels.values())

        for mk, (scale, _) in scaled.items():
            ms = gs.metrics[mk]
            # Escape after padding: "[ms]" must not be parsed as a rich tag,
            # and the invisible backslash must not skew the column width.
            label = labels[mk].ljust(max_w).replace("[", "\\[")
            mean_v = ms.mean * scale
            if ms.n >= 2:
                std_v = ms.stdev * scale
                min_v = ms.min * scale
                max_v = ms.max * scale
                lines.append(
                    f"  [bench.label]{label}[/]"
                    f"  [bench.value]{mean_v:.2f}[/]"
                    f" ± [bench.success]{std_v:.2f}[/]"
                    f"    ([bench.min]{min_v:.2f}[/]"
                    f" … [bench.max]{max_v:.2f}[/])"
                )
            else:
                lines.append(f"  {label}  [bench.value]{mean_v:.2f}[/]")

        self._fmt_outliers(gs, scaled, lines)

    @staticmethod
    def _fmt_outliers(
        gs: GroupStats, scaled: dict[MetricKey, tuple[float, str]], lines: list[str]
    ) -> None:
        """Note any flagged outliers."""
        notes = [
            (mk[0], gs.metrics[mk].n_outliers)
            for mk in scaled
            if gs.metrics[mk].n_outliers > 0
        ]
        if not notes:
            return
        total = sum(n for _, n in notes)
        detail = ", ".join(f"{m}: {n}" for m, n in notes)
        lines.append(
            f"  [bench.warning]!! statistical outlier(s) detected: {total} ({detail}) !![/]"
        )

    # ----- intra-benchmark ranking (hyperfine-style) -----------------

    def _fmt_ranking(self, data: SummaryData, lines: list[str]) -> None:
        """Append "Summary" blocks ranking the variants WITHIN each benchmark.

        Comparison is meaningful only between variants of the same workload.
        For each `(suite, benchmark)` partition with >= 2 variants and each
        rankable metric, emit one block: best variant, then one line per slower
        variant with the factor and propagated sigma.
        """
        # Partition groups by BenchKey. Preserve insertion order so the
        # Summary blocks follow the order of the per-group stats blocks.
        partitions: dict[BenchKey, list[GroupStats]] = {}
        for gs in data.groups:
            partitions.setdefault((gs.suite, gs.benchmark), []).append(gs)

        rankable_partitions = [
            (key, groups) for key, groups in partitions.items() if len(groups) >= 2
        ]
        if not rankable_partitions:
            return

        multi_partition = len(rankable_partitions) > 1
        for (suite, bench), groups in rankable_partitions:
            # Collect rankable metrics for this partition.
            per_metric: dict[MetricKey, list[GroupStats]] = {}
            for gs in groups:
                for mk, ms in gs.metrics.items():
                    if ms.lower_is_better is None or not self._include(mk):
                        continue
                    per_metric.setdefault(mk, []).append(gs)
            rankable = {mk: gs for mk, gs in per_metric.items() if len(gs) >= 2}
            if not rankable:
                continue

            for mk, gs_list in rankable.items():
                is_lower = gs_list[0].metrics[mk].lower_is_better
                ranked = sorted(
                    gs_list,
                    key=lambda g: g.metrics[mk].median,
                    reverse=not is_lower,
                )
                best = ranked[0]
                word = "lower" if is_lower else "higher"

                lines.append("")
                title = f"Summary — {suite}/{bench}" if multi_partition else "Summary"
                lines.append(f"[bench.label]{title}[/]")
                lines.append(
                    f"  [bench.name]'{_variant_name(best)}'[/]"
                    f" [bench.metric]\\[{mk[0]}][/] was"
                )
                for other in ranked[1:]:
                    mr = metric_ratio(best.metrics[mk], other.metrics[mk])
                    if mr is None:
                        continue
                    ratio, sigma, _ = _orient(mr.display_ratio, mr.sigma)
                    lines.append(
                        f"    [bench.value]{ratio:.2f}[/]"
                        f" ± [bench.success]{sigma:.2f}[/]"
                        f" times [bench.better]{word}[/] than"
                        f" [bench.name]'{_variant_name(other)}'[/]"
                    )

    # ----- comparison block -----------------------------------------

    def _fmt_comparison(self, data: SummaryData, lines: list[str]) -> None:
        assert data.baseline is not None
        baseline = data.baseline
        all_lib: dict[MetricKey, bool] = {}
        all_lib.update(baseline.lower_is_better)
        for c in data.comparees:
            all_lib.update(c.lower_is_better)

        comp_idx: dict[str, dict[BenchmarkId, BenchmarkGroup]] = {
            cn: {(g.suite, g.benchmark, g.variant): g for g in c.groups}
            for c, cn in zip(data.comparees, data.comparee_names)
        }

        lines.append("")
        first = True
        for bl_g in baseline.groups:
            bid: BenchmarkId = (bl_g.suite, bl_g.benchmark, bl_g.variant)
            present = [
                (cn, comp_idx[cn][bid])
                for cn in data.comparee_names
                if bid in comp_idx.get(cn, {})
            ]
            if not present:
                continue
            if not first:
                lines.append("")
            first = False

            name = f"{_group_label(bl_g)}{_variant_suffix(bl_g)}"
            lines.append(f"[bench.label]{name}:[/]")
            lines.append("  runs:")
            lines.append(f"    {self._fmt_runs(baseline.name, bl_g.run_counts)}")
            for cn, cg in present:
                lines.append(f"    {self._fmt_runs(cn, cg.run_counts)}")
            for mk in bl_g.metrics:
                if mk not in all_lib or not self._include(mk):
                    continue
                shown = False
                for cn, _ in present:
                    mr = data.ratios.get(cn, {}).get(bid, {}).get(mk)
                    if mr is None:
                        continue
                    if not shown:
                        lines.append(f"  [bench.metric]{mk[0]}[/]:")
                        shown = True
                    r, s, word = _orient(mr.display_ratio, mr.sigma)
                    lines.append(
                        self._fmt_ratio_line("    ", cn, r, s, word, baseline.name)
                    )

        # Per-suite geomean summary
        suites_in_order: list[str] = []
        for g in baseline.groups:
            if g.suite not in suites_in_order:
                suites_in_order.append(g.suite)
        if not suites_in_order:
            return

        lines.append("\n[bench.label]Summary (geometric mean of ratios):[/]")
        for suite in suites_in_order:
            suite_groups = [g for g in baseline.groups if g.suite == suite]
            lines.append(f"  [bench.label]{suite}:[/]")
            lines.append("    runs:")
            lines.append(
                f"      {self._fmt_runs(baseline.name, self._sum(suite_groups))}"
            )
            for c, cn in zip(data.comparees, data.comparee_names):
                idx = {(g.suite, g.benchmark, g.variant): g for g in c.groups}
                matched = [
                    idx[(g.suite, g.benchmark, g.variant)]
                    for g in suite_groups
                    if (g.suite, g.benchmark, g.variant) in idx
                ]
                lines.append(f"      {self._fmt_runs(cn, self._sum(matched))}")

            metric_keys: list[MetricKey] = []
            for g in suite_groups:
                for mk in g.metrics:
                    if mk not in metric_keys:
                        metric_keys.append(mk)
            for mk in metric_keys:
                if mk not in all_lib or not self._include(mk):
                    continue
                shown = False
                for cn in data.comparee_names:
                    gmr = data.geomeans.get(cn, {}).get(suite, {}).get(mk)
                    if gmr is None:
                        continue
                    if not shown:
                        lines.append(f"    [bench.metric]{mk[0]}[/]:")
                        shown = True
                    r, s, word = _orient(gmr.display_ratio, gmr.sigma)
                    lines.append(
                        self._fmt_ratio_line("      ", cn, r, s, word, baseline.name)
                    )

    @staticmethod
    def _fmt_runs(name: str, rc: RunCounts) -> str:
        return f"{name}: {_count_markup(rc)} (failed|succeeded)"

    @staticmethod
    def _fmt_ratio_line(
        indent: str,
        name: str,
        ratio: float,
        sigma: float,
        word: str,
        baseline_name: str,
    ) -> str:
        err = f" ± {sigma:.2f}" if sigma > 0 else ""
        word_style = "bench.better" if word == "better" else "bench.worse"
        return (
            f"{indent}[bench.name]{name}[/] was"
            f" [bench.value]{ratio:.2f}[/]{err}"
            f" times [{word_style}]{word}[/] than"
            f" [bench.value]{baseline_name}[/]"
        )

    @staticmethod
    def _sum(gs: list[BenchmarkGroup]) -> RunCounts:
        f = s = 0
        for g in gs:
            f += g.run_counts.failures
            s += g.run_counts.successes
        return RunCounts(failures=f, successes=s)


# ---------------------------------------------------------------------------
# Compact
# ---------------------------------------------------------------------------


class Compact(Formatter):
    """One-line-per-benchmark format. Useful for commit messages / CI logs."""

    def __init__(
        self,
        metric: str | list[str],
        *,
        suite: str | None = None,
        baseline_name: str | None = None,
        precision: int = 2,
    ) -> None:
        self._metrics = [metric] if isinstance(metric, str) else list(metric)
        self._suite = suite
        self._baseline_name = baseline_name
        self._precision = precision

    def _match(self, mk: MetricKey) -> bool:
        return mk[0] in self._metrics

    def format(self, data: SummaryData) -> str:
        if data.baseline is not None:
            return self._with_baseline(data)
        return self._no_baseline(data)

    # ----- with baseline --------------------------------------------

    def _with_baseline(self, data: SummaryData) -> str:
        cname = self._baseline_name
        if cname is None:
            cname = (
                "current"
                if "current" in data.comparee_names
                else data.comparee_names[-1]
            )
        if cname not in data.ratios:
            return f"Error: comparee {cname!r} not found"

        bench_ratios = data.ratios[cname]
        entries: list[tuple[BenchKey, MetricRatio]] = []
        matched: set[MetricKey] = set()
        for bid, m in bench_ratios.items():
            if self._suite is not None and bid[0] != self._suite:
                continue
            for mk, mr in m.items():
                if self._match(mk):
                    entries.append(((bid[0], bid[1]), mr))
                    matched.add(mk)
                    break
        if not entries:
            return f"No data for metric(s) {', '.join(self._metrics)!r}"

        gmr: GeoMeanRatio | None = None
        gmr_err: str | None = None
        if self._suite and len(matched) == 1:
            target_mk = next(iter(matched))
            gmr = data.geomeans.get(cname, {}).get(self._suite, {}).get(target_mk)
        else:
            mrs = [e[1] for e in entries]
            if mrs and all(mr.display_ratio > 0 for mr in mrs):
                geo, sigma = geomean_with_sigma(mrs)
                runs = self._infer_run_count(data, cname, bench_ratios, matched)
                gmr = GeoMeanRatio(
                    metric=mrs[0].metric,
                    unit=mrs[0].unit,
                    lower_is_better=mrs[0].lower_is_better,
                    display_ratio=geo,
                    sigma=sigma,
                    n_benchmarks=len(mrs),
                    runs_per_benchmark=runs,
                )
            elif mrs:
                gmr_err = "[red]geomean: skipped (negative values)[/]"

        p = self._precision
        out: list[str] = []
        if gmr is not None:
            label = "speedup" if len(matched) == 1 else "improvement"
            runs_note = (
                f" ({gmr.runs_per_benchmark} runs)"
                if gmr.runs_per_benchmark is not None
                else ""
            )
            out.append(
                f"geometric mean {label} vs baseline:"
                f" {gmr.display_ratio:.{p}f}"
                f" ± {gmr.sigma:.{p}f}"
                f"{runs_note}"
            )
            out.append("")
        elif gmr_err is not None:
            out.append(gmr_err)
            out.append("")
        for key, mr in sorted(entries, key=lambda e: e[0]):
            out.append(f"{key[1]}: {mr.display_ratio:.{p}f} ± {mr.sigma:.{p}f}")
        return "\n".join(out)

    @staticmethod
    def _infer_run_count(
        data: SummaryData,
        cname: str,
        bench_ratios: dict[BenchmarkId, dict[MetricKey, MetricRatio]],
        matched: set[MetricKey],
    ) -> int | None:
        """The run count shared by every matched benchmark, or `None` when
        the counts disagree (no honest single number to print)."""
        runs: set[int] = set()
        for gs in data.groups:
            bid: BenchmarkId = (gs.suite, gs.benchmark, gs.variant)
            if bid in bench_ratios and any(mk in bench_ratios[bid] for mk in matched):
                runs.add(gs.run_counts.successes)
        if not runs:
            idx = data.comparee_names.index(cname)
            comp = data.comparees[idx]
            for g in comp.groups:
                bid = (g.suite, g.benchmark, g.variant)
                if bid in bench_ratios and any(
                    mk in bench_ratios[bid] for mk in matched
                ):
                    runs.add(g.run_counts.successes)
        return runs.pop() if len(runs) == 1 else None

    # ----- no baseline ----------------------------------------------

    def _no_baseline(self, data: SummaryData) -> str:
        by_metric: dict[str, list[tuple[str, MetricStats]]] = {}
        for gs in data.groups:
            if self._suite is not None and gs.suite != self._suite:
                continue
            for mk, ms in gs.metrics.items():
                if self._match(mk):
                    by_metric.setdefault(mk[0], []).append((gs.benchmark, ms))
                    break
        if not by_metric:
            return f"No data for metric(s) {', '.join(self._metrics)!r}"

        p = self._precision
        out: list[str] = []
        for metric in self._metrics:
            entries = by_metric.get(metric)
            if not entries:
                continue
            n = entries[0][1].n
            run_word = "run" if n == 1 else "runs"
            all_means = [ms.mean for _, ms in entries]
            avg = statistics.mean(all_means) if all_means else 0
            sc, scaled = scale_unit(avg, entries[0][1].unit)
            if out:
                out.append("")
            out.append(f"{metric} (mean ± σ, {n} {run_word}):")
            out.append("")
            for name, ms in sorted(entries, key=lambda e: e[0]):
                mean_v = ms.mean * sc
                if ms.n >= 2:
                    std_v = ms.stdev * sc
                    out.append(f"{name}: {mean_v:.{p}f} ± {std_v:.{p}f} {scaled}")
                else:
                    out.append(f"{name}: {mean_v:.{p}f} {scaled}")
            if len(entries) > 1 and all(ms.mean > 0 for _, ms in entries):
                geo = geomean([ms.mean for _, ms in entries]) * sc
                if n >= 2:
                    rel_sq = [(ms.stdev / ms.mean) ** 2 for _, ms in entries]
                    sigma = math.sqrt(sum(rel_sq)) / len(entries)
                    out.append(f"geomean: {geo:.{p}f} ± {geo * sigma:.{p}f} {scaled}")
                else:
                    out.append(f"geomean: {geo:.{p}f} {scaled}")
        return "\n".join(out)
