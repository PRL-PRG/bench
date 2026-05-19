"""Formatters: turn a Report (or pre-computed SummaryData) into a string.

The Reporter sinks (Csv/Json/Dir) handle raw output; Formatters are for
human-readable summaries. They are pure: ``format(report, baseline=...) -> str``.

Built-ins:
    DefaultSummary   per-benchmark stats + per-suite geomean comparison
    Compact          one-line-per-benchmark (good for commit messages)
"""

from __future__ import annotations

import abc
import math
import statistics
from pathlib import Path
from typing import Optional

from benchr.report.sample import Report, Sample
from benchr.report.stats import (
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
    geomean_with_sigma,
    scale_unit,
)


class Formatter(abc.ABC):
    """Pure: render a Report → text."""

    def __call__(self, report: Report, *, baseline: list[Path] | None = None) -> str:
        data = build_summary(report, baseline)
        return self.format(data)

    @abc.abstractmethod
    def format(self, data: SummaryData) -> str: ...


# ---------------------------------------------------------------------------
# DefaultSummary
# ---------------------------------------------------------------------------


def _orient(display_ratio: float, sigma: float) -> tuple[float, float, str]:
    """Express any oriented ratio as ratio ≥ 1 plus better/worse word."""
    if display_ratio >= 1:
        return display_ratio, sigma, "better"
    inv = 1.0 / display_ratio
    inv_sigma = sigma / (display_ratio ** 2)
    return inv, inv_sigma, "worse"


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
        if data.baseline is not None:
            self._fmt_comparison(data, lines)
        return "\n".join(lines)

    # ----- group block ----------------------------------------------

    def _fmt_group(self, gs: GroupStats, lines: list[str]) -> None:
        name = f"{gs.suite}/{gs.benchmark}"
        if gs.info:
            name += " (" + ", ".join(f"{k}={v}" for k, v in gs.info) + ")"

        rc = gs.run_counts
        total = rc.failures + rc.successes
        word = "run" if total <= 1 else "runs"
        f_s = f"[benchr.failure]{rc.failures}[/]" if rc.failures else str(rc.failures)
        s_s = f"[benchr.success]{rc.successes}[/]"
        lines.append(f"[benchr.label]{name}:[/] {f_s}/{s_s} {word}")

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
        labels = {mk: f"{mk[0]} [{scaled[mk][1]}]{suffix}" for mk in scaled}
        max_w = max(len(l) for l in labels.values())

        for mk, (scale, _) in scaled.items():
            ms = gs.metrics[mk]
            label = labels[mk].ljust(max_w)
            mean_v = ms.mean * scale
            if ms.n >= 2:
                std_v = ms.stdev * scale
                min_v = ms.min * scale
                max_v = ms.max * scale
                lines.append(
                    f"  [benchr.label]{label}[/]"
                    f"  [benchr.value]{mean_v:.2f}[/]"
                    f" ± [benchr.success]{std_v:.2f}[/]"
                    f"    ([benchr.min]{min_v:.2f}[/]"
                    f" … [benchr.max]{max_v:.2f}[/])"
                )
            else:
                lines.append(f"  {label}  [benchr.value]{mean_v:.2f}[/]")

    # ----- comparison block -----------------------------------------

    def _fmt_comparison(self, data: SummaryData, lines: list[str]) -> None:
        assert data.baseline is not None
        baseline = data.baseline
        all_lib: dict[MetricKey, bool] = {}
        all_lib.update(baseline.lower_is_better)
        for c in data.comparees:
            all_lib.update(c.lower_is_better)

        comp_idx: dict[str, dict[BenchmarkId, BenchmarkGroup]] = {
            cn: {(g.suite, g.benchmark, g.info): g for g in c.groups}
            for c, cn in zip(data.comparees, data.comparee_names)
        }

        lines.append("")
        first = True
        for bl_g in baseline.groups:
            bid: BenchmarkId = (bl_g.suite, bl_g.benchmark, bl_g.info)
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

            name = f"{bl_g.suite}/{bl_g.benchmark}"
            if bl_g.info:
                name += " (" + ", ".join(f"{k}={v}" for k, v in bl_g.info) + ")"
            lines.append(f"[benchr.label]{name}:[/]")
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
                        lines.append(f"  [benchr.metric]{mk[0]}[/]:")
                        shown = True
                    r, s, word = _orient(mr.display_ratio, mr.sigma)
                    lines.append(self._fmt_ratio_line("    ", cn, r, s, word, baseline.name))

        # Per-suite geomean summary
        suites_in_order: list[str] = []
        for g in baseline.groups:
            if g.suite not in suites_in_order:
                suites_in_order.append(g.suite)
        if not suites_in_order:
            return

        lines.append("\n[benchr.label]Summary (geometric mean of ratios):[/]")
        for suite in suites_in_order:
            suite_groups = [g for g in baseline.groups if g.suite == suite]
            lines.append(f"  [benchr.label]{suite}:[/]")
            lines.append("    runs:")
            lines.append(f"      {self._fmt_runs(baseline.name, self._sum(suite_groups))}")
            for c, cn in zip(data.comparees, data.comparee_names):
                idx = {(g.suite, g.benchmark, g.info): g for g in c.groups}
                matched = [idx[(g.suite, g.benchmark, g.info)] for g in suite_groups
                           if (g.suite, g.benchmark, g.info) in idx]
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
                    gmr = data.geomeans.get(suite, {}).get(cn, {}).get(mk)
                    if gmr is None:
                        continue
                    if not shown:
                        lines.append(f"    [benchr.metric]{mk[0]}[/]:")
                        shown = True
                    r, s, word = _orient(gmr.display_ratio, gmr.sigma)
                    lines.append(self._fmt_ratio_line("      ", cn, r, s, word, baseline.name))

    @staticmethod
    def _fmt_runs(name: str, rc: RunCounts) -> str:
        f_s = f"[benchr.failure]{rc.failures}[/]" if rc.failures else str(rc.failures)
        s_s = f"[benchr.success]{rc.successes}[/]"
        return f"{name}: {f_s} failed / {s_s} succeeded"

    @staticmethod
    def _fmt_ratio_line(indent: str, name: str, ratio: float, sigma: float,
                       word: str, baseline_name: str) -> str:
        err = f" ± {sigma:.2f}" if sigma > 0 else ""
        word_style = "benchr.better" if word == "better" else "benchr.worse"
        return (
            f"{indent}[benchr.name]{name}[/] was"
            f" [benchr.value]{ratio:.2f}[/]{err}"
            f" times [{word_style}]{word}[/] than"
            f" [benchr.value]{baseline_name}[/]"
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

    def __init__(self, metric: str | list[str], *,
                 suite: str | None = None,
                 baseline_name: str | None = None,
                 precision: int = 2) -> None:
        self._metrics = [metric] if isinstance(metric, str) else list(metric)
        self._metrics_set = set(self._metrics)
        self._suite = suite
        self._baseline_name = baseline_name
        self._precision = precision

    def format(self, data: SummaryData) -> str:
        if data.baseline is not None:
            return self._with_baseline(data)
        return self._no_baseline(data)

    # ----- with baseline --------------------------------------------

    def _with_baseline(self, data: SummaryData) -> str:
        cname = self._baseline_name
        if cname is None:
            cname = "current" if "current" in data.comparee_names else data.comparee_names[-1]
        if cname not in data.ratios:
            return f"Error: comparee {cname!r} not found"

        bench_ratios = data.ratios[cname]
        entries: list[tuple[str, MetricRatio]] = []
        matched: set[MetricKey] = set()
        for bid, m in bench_ratios.items():
            if self._suite is not None and bid[0] != self._suite:
                continue
            for mk, mr in m.items():
                if mk[0] in self._metrics_set:
                    entries.append((bid[1], mr))
                    matched.add(mk)
                    break
        if not entries:
            return f"No data for metric(s) {', '.join(self._metrics)!r}"

        gmr: Optional[GeoMeanRatio] = None
        gmr_err: Optional[str] = None
        if self._suite and len(matched) == 1:
            target_mk = next(iter(matched))
            gmr = data.geomeans.get(self._suite, {}).get(cname, {}).get(target_mk)
        else:
            mrs = [e[1] for e in entries]
            if mrs and all(mr.display_ratio > 0 for mr in mrs):
                geo, sigma = geomean_with_sigma(mrs)
                runs = self._infer_run_count(data, cname, bench_ratios, matched)
                gmr = GeoMeanRatio(
                    metric=mrs[0].metric, unit=mrs[0].unit,
                    lower_is_better=mrs[0].lower_is_better,
                    display_ratio=geo, sigma=sigma,
                    n_benchmarks=len(mrs),
                    runs_per_benchmark=runs,
                )
            elif mrs:
                gmr_err = "[red]geomean: skipped (negative values)[/]"

        p = self._precision
        out: list[str] = []
        if gmr is not None:
            label = "speedup" if len(matched) == 1 else "improvement"
            out.append(
                f"geometric mean {label} vs baseline:"
                f" {gmr.display_ratio:.{p}f}"
                f" ± {gmr.sigma:.{p}f}"
                f" ({gmr.runs_per_benchmark} runs)"
            )
            out.append("")
        elif gmr_err is not None:
            out.append(gmr_err)
            out.append("")
        for name, mr in sorted(entries, key=lambda e: e[0]):
            out.append(f"{name}: {mr.display_ratio:.{p}f} ± {mr.sigma:.{p}f}")
        return "\n".join(out)

    @staticmethod
    def _infer_run_count(data: SummaryData, cname: str, bench_ratios, matched) -> int:
        runs: set[int] = set()
        for gs in data.groups:
            bid: BenchmarkId = (gs.suite, gs.benchmark, gs.info)
            if bid in bench_ratios and any(mk in bench_ratios[bid] for mk in matched):
                runs.add(gs.run_counts.successes)
        if not runs:
            idx = data.comparee_names.index(cname)
            comp = data.comparees[idx]
            for g in comp.groups:
                bid = (g.suite, g.benchmark, g.info)
                if bid in bench_ratios and any(mk in bench_ratios[bid] for mk in matched):
                    runs.add(g.run_counts.successes)
        return runs.pop() if len(runs) == 1 else 0

    # ----- no baseline ----------------------------------------------

    def _no_baseline(self, data: SummaryData) -> str:
        by_metric: dict[str, list[tuple[str, MetricStats]]] = {}
        for gs in data.groups:
            if self._suite is not None and gs.suite != self._suite:
                continue
            for mk, ms in gs.metrics.items():
                if mk[0] in self._metrics_set:
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
                log_means = [math.log(ms.mean) for _, ms in entries]
                geo = math.exp(statistics.mean(log_means)) * sc
                if n >= 2:
                    rel_sq = [(ms.stdev / ms.mean) ** 2 for _, ms in entries]
                    sigma = math.sqrt(sum(rel_sq)) / len(entries)
                    out.append(f"geomean: {geo:.{p}f} ± {geo * sigma:.{p}f} {scaled}")
                else:
                    out.append(f"geomean: {geo:.{p}f} {scaled}")
        return "\n".join(out)
