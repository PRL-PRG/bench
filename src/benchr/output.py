import abc
import argparse
import csv
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from pprint import pprint
from threading import Lock
from typing import Any, Callable, Optional

from rich.console import Console
from rich.progress import (
    Progress,
    BarColumn,
    TimeElapsedColumn,
    SpinnerColumn,
    TextColumn,
)
from rich.table import Table
from rich.theme import Theme

from benchr.types import (
    const,
    Env,
    Command,
    Parameters,
    ProcessResult,
    SuccessfulProcessResult,
    FailedProcessResult,
    Execution,
)
from benchr.suites import Config
from benchr.results import (
    Measurement,
    ExecutionResult,
    execution_result_to_json,
    execution_result_from_json,
    MetricKey,
    VariantInfo,
    BenchmarkRunCounts,
    BenchmarkGroup,
    GroupedResult,
    BenchmarkId,
    MetricStats,
    GroupStats,
    MetricRatio,
    GeoMeanRatio,
    SummaryData,
    build_summary_data,
    build_summary_data_from_grouped,
    scale_unit,
    geomean_with_sigma,
)
from benchr.parsers import ResultParser


# Centralized style definitions — change colors in one place
BENCHR_THEME = Theme(
    {
        "benchr.success": "green",
        "benchr.failure": "red",
        "benchr.metric": "cyan",
        "benchr.value": "green bold",
        "benchr.min": "cyan",
        "benchr.max": "magenta",
        "benchr.name": "magenta",
        "benchr.label": "bold",
        "benchr.better": "green bold",
        "benchr.worse": "red bold",
        "benchr.progress": "blue bold",
        "benchr.in_process": "magenta bold",
    }
)

console = Console(theme=BENCHR_THEME, highlight=False)
err_console = Console(theme=BENCHR_THEME, highlight=False, stderr=True)


def _make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TimeElapsedColumn(),
        BarColumn(),
        TextColumn(
            "({task.fields[failures]}/{task.fields[successes]}/{task.total:.0f})"
        ),
        TextColumn("{task.description}"),
        console=console,
        transient=True,
    )


class Reporter(abc.ABC):
    """
    Reports the results. Lifecycle:
      1. start(executions) - once before any execution runs.
      2. report(process_result, parsed) - once per finished execution.
      3. finalize() - once after all executions have completed.
    """

    def start(self, executions: list[Execution]) -> None:
        """Called once before any execution runs. Default no-op."""
        pass

    @abc.abstractmethod
    def report(self, process_result: ProcessResult, parsed: ExecutionResult) -> None:
        """Called once per finished execution (success or failure)."""
        ...

    def finalize(self) -> None:
        """Called once after all executions have completed. Default no-op."""
        pass


class MixedReporter(Reporter):
    """
    Multiple reporters posing as one
    """

    reporters: list[Reporter]

    def __init__(self, *reporters: Reporter) -> None:
        self.reporters = list(reporters)

    def start(self, executions: list[Execution]) -> None:
        for r in self.reporters:
            r.start(executions)

    def report(self, process_result: "ProcessResult", parsed: ExecutionResult):
        for r in self.reporters:
            r.report(process_result, parsed)

    def finalize(self) -> None:
        for r in self.reporters:
            r.finalize()


class CsvReporter(Reporter):
    """
    Report into CSV file. Streams rows to disk as executions finish.

    The header schema (info columns) is fixed from the first reported result;
    subsequent measurements are written using that same schema.
    """

    filepath: Path
    separator: str

    def __init__(self, filepath: Path, separator: str = ",") -> None:
        self.filepath = filepath
        self.separator = separator
        self._file = None
        self._writer = None
        self._info_cols: Optional[list[str]] = None

    def start(self, executions: list[Execution]) -> None:
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.filepath, "wt", newline="")
        self._writer = csv.writer(self._file, delimiter=self.separator)
        self._info_cols = None

    def report(self, process_result: ProcessResult, parsed: ExecutionResult):
        if self._writer is None:
            # Reporter used without start(); open lazily.
            self.start([])
        assert self._writer is not None

        if self._info_cols is None:
            self._info_cols = parsed.info_columns()
            columns = (
                ["benchmark", "suite", "run"]
                + self._info_cols
                + ["lower_is_better", "metric", "value", "unit"]
            )
            self._writer.writerow(columns)

        for measure in parsed.measurements:
            row: list[str] = [
                measure.execution.benchmark_name,
                measure.execution.suite,
                str(measure.execution.run),
            ]

            for col in self._info_cols:
                row.append(measure.execution.info.get(col, ""))

            lib_str = (
                "" if measure.lower_is_better is None else str(measure.lower_is_better)
            )
            row += [lib_str, measure.metric, str(measure.value), measure.unit]

            self._writer.writerow(row)

        self._file.flush()

    def finalize(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
            self._writer = None


class JsonReporter(Reporter):
    """
    Report as a single JSON file (ExecutionResult serialized).
    Buffers measurements in memory and writes the file on finalize().
    """

    filepath: Path

    def __init__(self, filepath: Path) -> None:
        self.filepath = filepath
        self._result = ExecutionResult()

    def report(self, process_result: ProcessResult, parsed: ExecutionResult):
        self._result.measurements.extend(parsed.measurements)

    def finalize(self) -> None:
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(self.filepath, "wt") as file:
            file.write(execution_result_to_json(self._result))


class TableReporter(Reporter):
    """
    Report into CLI. Buffers measurements and prints the full table on
    finalize().
    """

    def __init__(self) -> None:
        self._result = ExecutionResult()

    def report(self, process_result: "ProcessResult", parsed: ExecutionResult):
        self._result.measurements.extend(parsed.measurements)

    def finalize(self) -> None:
        self.print_result(self._result)

    def print_result(self, result: ExecutionResult):
        info_cols = result.info_columns()

        def lib_str(m: Measurement) -> str:
            return "" if m.lower_is_better is None else str(m.lower_is_better)

        headers = (
            ["benchmark", "suite", "run"]
            + info_cols
            + ["lower_is_better", "metric", "value", "unit"]
        )
        rows = []
        for m in result.measurements:
            row = [
                m.execution.benchmark_name,
                m.execution.suite,
                str(m.execution.run),
            ]
            row += [m.execution.info.get(c, "") for c in info_cols]
            row += [lib_str(m), m.metric, str(m.value), m.unit]
            rows.append(row)

        table = Table(show_header=True, show_edge=False, pad_edge=False)
        for h in headers:
            table.add_column(h)
        for row in rows:
            table.add_row(*[str(c) for c in row])
        console.print()
        console.print(table)


def _orient_ratio(display_ratio: float, sigma: float) -> tuple[float, float, str]:
    """Convert oriented ratio to always->=1 ratio + better/worse word."""
    if display_ratio >= 1:
        return display_ratio, sigma, "better"
    inv = 1.0 / display_ratio
    inv_sigma = sigma / (display_ratio**2)
    return inv, inv_sigma, "worse"


class SummaryFormatter(abc.ABC):
    """Format pre-computed SummaryData into a printable string."""

    @abc.abstractmethod
    def format(self, data: SummaryData) -> str: ...


class DefaultSummaryFormatter(SummaryFormatter):
    """Reproduces the original SummaryReporter + compare_and_print output."""

    def __init__(self, metrics: set[str] | None = None) -> None:
        self.metrics = metrics

    def _include_metric(self, mk: MetricKey) -> bool:
        return self.metrics is None or mk[0] in self.metrics

    def format(self, data: SummaryData) -> str:
        lines: list[str] = []
        if data.groups:
            lines.append("")
            for i, gs in enumerate(data.groups):
                if i > 0:
                    lines.append("")
                self._format_group(gs, lines)
        if data.baseline is not None:
            self._format_comparison(data, lines)
        return "\n".join(lines)

    def _format_group(self, gs: GroupStats, lines: list[str]) -> None:
        name = f"{gs.suite}/{gs.benchmark}"
        if gs.info:
            info_str = ", ".join(f"{k}={v}" for k, v in gs.info)
            name += f" ({info_str})"

        rc = gs.run_counts
        total_runs = (rc.failures + rc.successes) or 1
        run_word = "run" if total_runs == 1 else "runs"
        f_s = f"[benchr.failure]{rc.failures}[/]" if rc.failures else str(rc.failures)
        s_s = f"[benchr.success]{rc.successes}[/]"
        lines.append(f"[benchr.label]{name}:[/] {f_s}/{s_s} {run_word}")

        if not gs.metrics:
            return

        scaled_info: dict[MetricKey, tuple[float, str]] = {}
        for mk, ms in gs.metrics.items():
            if not self._include_metric(mk):
                continue
            scaled_info[mk] = scale_unit(ms.mean, ms.unit)

        if not scaled_info:
            return

        multi_run = total_runs > 1
        suffix = " (mean \u00b1 \u03c3):" if multi_run else ":"
        labels = {mk: f"{mk[0]} \\[{scaled_info[mk][1]}]{suffix}" for mk in scaled_info}
        max_label_w = max(len(l) for l in labels.values())

        for mk, ms in scaled_info.items():
            ms = gs.metrics[mk]
            label = labels[mk].ljust(max_label_w)
            scale, _ = scaled_info[mk]
            mean_val = ms.mean * scale
            if ms.n >= 2:
                stddev_val = ms.stdev * scale
                min_val = ms.min * scale
                max_val = ms.max * scale
                lines.append(
                    f"  [benchr.label]{label}[/]"
                    f"  [benchr.value]{mean_val:.2f}[/]"
                    f" \u00b1 [benchr.success]{stddev_val:.2f}[/]"
                    f"    ([benchr.min]{min_val:.2f}[/]"
                    f" \u2026 [benchr.max]{max_val:.2f}[/])"
                )
            else:
                lines.append(f"  {label}  [benchr.value]{mean_val:.2f}[/]")

    def _format_comparison(self, data: SummaryData, lines: list[str]) -> None:
        assert data.baseline is not None
        baseline = data.baseline

        def format_runs(name: str, rc: BenchmarkRunCounts) -> str:
            f_s = (
                f"[benchr.failure]{rc.failures}[/]" if rc.failures else str(rc.failures)
            )
            s_s = f"[benchr.success]{rc.successes}[/]"
            return f"{name}: {f_s} failed / {s_s} succeeded"

        def format_ratio_line(
            indent: str,
            name: str,
            ratio: float,
            sigma: float,
            word: str,
            baseline_name: str,
        ) -> str:
            err_str = f" \u00b1 {sigma:.2f}" if sigma > 0 else ""
            word_style = "benchr.better" if word == "better" else "benchr.worse"
            word_str = f"[{word_style}]{word}[/]"
            return (
                f"{indent}[benchr.name]{name}[/] was"
                f" [benchr.value]{ratio:.2f}[/]{err_str}"
                f" times {word_str} than"
                f" [benchr.value]{baseline_name}[/]"
            )

        all_lib: dict[MetricKey, bool] = {}
        all_lib.update(baseline.lower_is_better)
        for comp in data.comparees:
            all_lib.update(comp.lower_is_better)

        # Index comparees by identity for run-count lookups
        comp_indices: dict[str, dict[BenchmarkId, BenchmarkGroup]] = {}
        for comp, cname in zip(data.comparees, data.comparee_names):
            comp_indices[cname] = {
                (g.suite, g.benchmark, g.info): g for g in comp.benchmarks
            }

        # Per-benchmark comparison
        lines.append("")
        first = True
        for bl_group in baseline.benchmarks:
            bid: BenchmarkId = (
                bl_group.suite,
                bl_group.benchmark,
                bl_group.info,
            )

            present = [
                (cname, comp_indices[cname][bid])
                for cname in data.comparee_names
                if bid in comp_indices.get(cname, {})
            ]
            if not present:
                continue

            if not first:
                lines.append("")
            first = False

            name = f"{bl_group.suite}/{bl_group.benchmark}"
            if bl_group.info:
                info_str = ", ".join(f"{k}={v}" for k, v in bl_group.info)
                name += f" ({info_str})"
            lines.append(f"[benchr.label]{name}:[/]")

            lines.append("  runs:")
            lines.append(f"    {format_runs(baseline.name, bl_group.run_counts)}")
            for cname, comp_g in present:
                lines.append(f"    {format_runs(cname, comp_g.run_counts)}")
            for mk in bl_group.metrics:
                if mk not in all_lib or not self._include_metric(mk):
                    continue
                metric_printed = False
                for cname, _ in present:
                    mr = data.ratios.get(cname, {}).get(bid, {}).get(mk)
                    if mr is None:
                        continue
                    if not metric_printed:
                        lines.append(f"  [benchr.metric]{mk[0]}[/]:")
                        metric_printed = True
                    abs_r, abs_s, word = _orient_ratio(mr.display_ratio, mr.sigma)
                    lines.append(
                        format_ratio_line(
                            "    ", cname, abs_r, abs_s, word, baseline.name
                        )
                    )

        # Summary: per-suite geometric mean of ratios
        suites_in_order: list[str] = []
        for g in baseline.benchmarks:
            if g.suite not in suites_in_order:
                suites_in_order.append(g.suite)

        if not suites_in_order:
            return

        lines.append(f"\n[benchr.label]Summary (geometric mean of ratios):[/]")

        def sum_counts(groups: list[BenchmarkGroup]) -> BenchmarkRunCounts:
            f = s = 0
            for g in groups:
                f += g.run_counts.failures
                s += g.run_counts.successes
            return BenchmarkRunCounts(f, s)

        for suite in suites_in_order:
            suite_groups = [g for g in baseline.benchmarks if g.suite == suite]
            lines.append(f"  [benchr.label]{suite}:[/]")

            lines.append("    runs:")
            lines.append(
                f"      {format_runs(baseline.name, sum_counts(suite_groups))}"
            )
            for comp, cname in zip(data.comparees, data.comparee_names):
                idx = {(g.suite, g.benchmark, g.info): g for g in comp.benchmarks}
                matched = [
                    idx[(g.suite, g.benchmark, g.info)]
                    for g in suite_groups
                    if (g.suite, g.benchmark, g.info) in idx
                ]
                lines.append(f"      {format_runs(cname, sum_counts(matched))}")

            suite_metric_keys: list[MetricKey] = []
            for g in suite_groups:
                for mk in g.metrics:
                    if mk not in suite_metric_keys:
                        suite_metric_keys.append(mk)

            for mk in suite_metric_keys:
                if mk not in all_lib or not self._include_metric(mk):
                    continue
                metric_printed = False
                for cname in data.comparee_names:
                    gmr = data.geomeans.get(suite, {}).get(cname, {}).get(mk)
                    if gmr is None:
                        continue
                    if not metric_printed:
                        lines.append(f"    [benchr.metric]{mk[0]}[/]:")
                        metric_printed = True
                    abs_r, abs_s, word = _orient_ratio(gmr.display_ratio, gmr.sigma)
                    lines.append(
                        format_ratio_line(
                            "      ",
                            cname,
                            abs_r,
                            abs_s,
                            word,
                            baseline.name,
                        )
                    )


class CompactFormatter(SummaryFormatter):
    """Compact, one-line-per-benchmark output suitable for commit messages."""

    def __init__(
        self,
        metric: "str | list[str]",
        suite: Optional[str] = None,
        baseline_name: Optional[str] = None,
        precision: int = 2,
    ) -> None:
        self._metrics = [metric] if isinstance(metric, str) else list(metric)
        self._metrics_set = set(self._metrics)
        self._suite = suite
        self._baseline_name = baseline_name
        self._precision = precision

    def format(self, data: SummaryData) -> str:
        if data.baseline is not None:
            return self._format_with_baseline(data)
        return self._format_no_baseline(data)

    def _format_with_baseline(self, data: SummaryData) -> str:
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

        # Collect entries for any of the target metrics
        entries: list[tuple[str, MetricRatio]] = []
        matched_mks: set[MetricKey] = set()
        for bid, metrics in bench_ratios.items():
            if self._suite is not None and bid[0] != self._suite:
                continue
            for mk, mr in metrics.items():
                if mk[0] in self._metrics_set:
                    entries.append((bid[1], mr))
                    matched_mks.add(mk)
                    break

        if not entries:
            return f"No data for metric(s) {', '.join(self._metrics)!r}"

        # Compute geo-mean: use pre-computed per-suite or pool across suites
        gmr: Optional[GeoMeanRatio] = None
        gmr_error: Optional[str] = None
        if self._suite and len(matched_mks) == 1:
            target_mk = next(iter(matched_mks))
            gmr = data.geomeans.get(self._suite, {}).get(cname, {}).get(target_mk)
        else:
            # Pool across all suites / metrics
            all_mrs = [mr for _, mr in entries]
            if all_mrs and all(mr.display_ratio > 0 for mr in all_mrs):
                geo, sigma = geomean_with_sigma(all_mrs)

                runs = set()
                for gs in data.groups:
                    bid_gs: BenchmarkId = (gs.suite, gs.benchmark, gs.info)
                    if bid_gs in bench_ratios:
                        for mk in matched_mks:
                            if mk in bench_ratios[bid_gs]:
                                runs.add(gs.run_counts.successes)
                                break
                # Fallback: check comparee GroupedResults if groups is empty
                if not runs:
                    idx = data.comparee_names.index(cname)
                    comp_gr = data.comparees[idx]
                    for g in comp_gr.benchmarks:
                        bid_g: BenchmarkId = (g.suite, g.benchmark, g.info)
                        if bid_g in bench_ratios:
                            for mk in matched_mks:
                                if mk in bench_ratios[bid_g]:
                                    runs.add(g.run_counts.successes)
                                    break

                if len(runs) != 1:
                    raise ValueError(
                        f"Inconsistent run counts for metric(s)"
                        f" {', '.join(self._metrics)!r}: {runs}"
                    )

                first_mr = all_mrs[0]
                gmr = GeoMeanRatio(
                    metric=first_mr.metric,
                    unit=first_mr.unit,
                    lower_is_better=first_mr.lower_is_better,
                    display_ratio=geo,
                    sigma=sigma,
                    n_benchmarks=len(all_mrs),
                    runs_per_benchmark=runs.pop(),
                )
            elif all_mrs:
                gmr_error = "[red]geomean: skipped (negative values)[/]"

        p = self._precision
        lines: list[str] = []

        if gmr is not None:
            label = "speedup" if len(matched_mks) == 1 else "improvement"
            lines.append(
                f"geometric mean {label} vs baseline:"
                f" {gmr.display_ratio:.{p}f}"
                f" \u00b1 {gmr.sigma:.{p}f}"
                f" ({gmr.runs_per_benchmark} runs)"
            )
            lines.append("")
        elif gmr_error is not None:
            lines.append(gmr_error)
            lines.append("")

        for name, mr in sorted(entries, key=lambda e: e[0]):
            lines.append(f"{name}: {mr.display_ratio:.{p}f} \u00b1 {mr.sigma:.{p}f}")

        return "\n".join(lines)

    def _format_no_baseline(self, data: SummaryData) -> str:
        """Fallback: mean +/- sigma per benchmark when no baseline."""
        # Group entries by metric name for proper unit scaling
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
        lines: list[str] = []

        for metric_name in self._metrics:
            entries = by_metric.get(metric_name)
            if not entries:
                continue

            n = entries[0][1].n
            run_word = "run" if n == 1 else "runs"

            all_means = [ms.mean for _, ms in entries]
            avg_mean = statistics.mean(all_means) if all_means else 0
            target_unit = entries[0][1].unit
            sc, scaled_unit = scale_unit(avg_mean, target_unit)

            if lines:
                lines.append("")
            lines.append(f"{metric_name} (mean \u00b1 \u03c3, {n} {run_word}):")
            lines.append("")

            for name, ms in sorted(entries, key=lambda e: e[0]):
                mean_val = ms.mean * sc
                if ms.n >= 2:
                    std_val = ms.stdev * sc
                    lines.append(
                        f"{name}: {mean_val:.{p}f} \u00b1 {std_val:.{p}f} {scaled_unit}"
                    )
                else:
                    lines.append(f"{name}: {mean_val:.{p}f} {scaled_unit}")

            # Geometric mean across benchmarks for this metric
            if len(entries) > 1:
                if all(ms.mean > 0 for _, ms in entries):
                    log_means = [math.log(ms.mean) for _, ms in entries]
                    geo = math.exp(statistics.mean(log_means)) * sc
                    if n >= 2:
                        rel_errs_sq = [(ms.stdev / ms.mean) ** 2 for _, ms in entries]
                        sigma_log = math.sqrt(sum(rel_errs_sq)) / len(entries)
                        geo_sigma = geo * sigma_log
                        lines.append(
                            f"geomean: {geo:.{p}f} \u00b1 {geo_sigma:.{p}f} {scaled_unit}"
                        )
                    else:
                        lines.append(f"geomean: {geo:.{p}f} {scaled_unit}")
                else:
                    lines.append("[red]geomean: skipped (negative values)[/]")

        return "\n".join(lines)


class SummaryReporter(Reporter):
    """
    Buffers measurements and prints a formatted summary on finalize().
    Delegates statistics to ``build_summary_data`` and rendering to a
    ``SummaryFormatter``.
    """

    def __init__(
        self,
        formatter: Optional[SummaryFormatter] = None,
        baseline: Optional["Path | list[Path]"] = None,
    ) -> None:
        self._result = ExecutionResult()
        self._formatter = formatter or DefaultSummaryFormatter()
        self._baseline_paths: list[Path] = (
            [baseline]
            if isinstance(baseline, Path)
            else list(baseline)
            if baseline
            else []
        )
        self._start_time: float = 0.0
        self._total: int = 0
        self._failures: int = 0

    def set_baseline(self, paths: list[Path]) -> None:
        """Override baselines (used by ``main()`` to forward ``--compare``)."""
        self._baseline_paths = list(paths)

    def start(self, executions: list[Execution]) -> None:
        self._start_time = time.monotonic()
        self._total = len(executions)

    def report(self, process_result: ProcessResult, parsed: ExecutionResult):
        self._result.measurements.extend(parsed.measurements)
        if isinstance(process_result, FailedProcessResult):
            self._failures += 1

    def finalize(self) -> None:
        elapsed = time.monotonic() - self._start_time
        successes = self._total - self._failures
        data = build_summary_data(self._result, self._baseline_paths)
        out = self._formatter.format(data)
        header = (
            f"Ran ({self._failures}/{successes}/{self._total})"
            f" executions in {elapsed:.1f}s."
        )
        console.print(header)
        console.print()
        if out:
            console.print(out)


class DirReporter(Reporter):
    """
    Stream per-execution raw artifacts into a directory tree:

        <output_dir>/<suite>/<benchmark>/<run_id>/
            seq, stdout, stderr, exitcode, rusage, result.csv

    Stable per-benchmark run ids are pre-computed in submission order from
    the list passed to `start`, so numbering is deterministic under parallel
    execution.
    """

    output_dir: Path

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self._run_ids: dict[int, int] = {}

    def start(self, executions: list[Execution]) -> None:
        self._run_ids = {}
        counts: dict[tuple[str, str], int] = {}
        for exe in executions:
            key = (exe.suite, exe.benchmark_name)
            counts[key] = counts.get(key, 0) + 1
            self._run_ids[id(exe)] = counts[key]

    def report(self, process_result: ProcessResult, parsed: ExecutionResult):
        pr = process_result
        exe = pr.execution
        run_id = self._run_ids.get(id(exe), exe.run)
        run_dir = self.output_dir / exe.suite / exe.benchmark_name / str(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)

        # seq: cwd, command, then info k=v lines
        seq_lines = [str(exe.working_directory), " ".join(exe.command)]
        seq_lines.append(f"run={exe.run}")
        seq_lines.extend(f"{k}={v}" for k, v in exe.info.items())
        (run_dir / "seq").write_text("\n".join(seq_lines) + "\n")

        if pr.stdout is not None:
            (run_dir / "stdout").write_text(pr.stdout)
        if pr.stderr is not None:
            (run_dir / "stderr").write_text(pr.stderr)

        if isinstance(pr, SuccessfulProcessResult):
            (run_dir / "exitcode").write_text("0\n")
        else:
            (run_dir / "exitcode").write_text(f"{pr.returncode}\n")

        if pr.rusage is not None:
            ru_lines = [
                f"{field}={getattr(pr.rusage, field)}"
                for field in dir(pr.rusage)
                if field.startswith("ru_")
            ]
            (run_dir / "rusage").write_text("\n".join(ru_lines) + "\n")

        # Per-execution result.csv: drive a fresh CsvReporter so the schema
        # matches the top-level CSV exactly.
        csv_reporter = CsvReporter(run_dir / "result.csv")
        csv_reporter.start([])
        csv_reporter.report(pr, parsed)
        csv_reporter.finalize()


def compare_and_print(
    datasets: list[GroupedResult],
    metrics: set[str] | None = None,
):
    """
    Compare N grouped result sets. The first dataset is the baseline; all
    subsequent datasets are compared against it.

    This is a thin wrapper around ``build_summary_data_from_grouped`` +
    ``DefaultSummaryFormatter``.
    """
    if len(datasets) < 2:
        return
    data = build_summary_data_from_grouped(datasets)
    out = DefaultSummaryFormatter(metrics=metrics).format(data)
    if out:
        console.print(out)


class Executor(abc.ABC):
    """
    Execute the executions
    """

    @abc.abstractmethod
    def execute(self, execution: Execution):
        """
        Run single execution - implementation detail, use `execute_all`
        """
        ...

    def execute_all(self, executions: list[Execution]) -> Optional[ExecutionResult]:
        """
        Run all executions - this is the prefered way of running executions
        """
        for execution in executions:
            self.execute(execution)
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class DefaultExecutor(Executor):
    """
    The main Executor
    """

    all_executions: Optional[int]
    finished_executions: int
    failed_executions: int

    reporter: Reporter

    result: ExecutionResult

    def __init__(self, reporter: Reporter) -> None:
        self.all_executions = None
        self.finished_executions = 0
        self.failed_executions = 0

        self.reporter = reporter

        self.result = ExecutionResult()

    def execute_all(self, executions: list[Execution]) -> ExecutionResult:
        self.all_executions = len(executions)
        self.reporter.start(executions)
        self.progress = _make_progress()
        self.progress.start()
        self.task_id = self.progress.add_task(
            "Running", total=self.all_executions, failures=0, successes=0
        )
        try:
            Executor.execute_all(self, executions)
        finally:
            self.progress.stop()
        return self.result

    def execute(self, execution: Execution):
        cmd = shutil.which(execution.command[0])
        if cmd is None:
            self.error_execution(
                FailedProcessResult.empty(
                    execution=execution,
                    reason=f"Command not found ({execution.command[0]})",
                )
            )
            self.progress.advance(self.task_id)
            return

        execution.command[0] = cmd

        self.start_execution(execution)

        stdout_file = tempfile.TemporaryFile()
        stderr_file = tempfile.TemporaryFile()
        try:
            proc = subprocess.Popen(
                execution.command,
                cwd=execution.working_directory,
                env=execution.env,
                stdin=None,
                stdout=stdout_file,
                stderr=stderr_file,
                shell=False,
            )
            starttime = time.monotonic()

            rusage = None
            waitstatus = None
            timed_out = False
            if execution.timeout is not None:
                stoptime = time.monotonic() + execution.timeout

                # Poll for the child with WNOHANG until it exits or the
                # timeout is exceeded. The child has not been reaped yet, so
                # os.wait4 cannot raise ChildProcessError here.
                while True:
                    pid, waitstatus, rusage = os.wait4(proc.pid, os.WNOHANG)
                    assert pid == proc.pid or pid == 0

                    if pid == proc.pid:
                        break  # child exited, waitstatus/rusage populated

                    if stoptime - time.monotonic() <= 0:
                        timed_out = True
                        proc.kill()
                        break

                    time.sleep(0.01)

            # Reap the child if the polling loop did not already get it
            # (either because there was no timeout, or because we just killed
            # it after the timeout expired).
            if waitstatus is None or timed_out:
                _, waitstatus, rusage = os.wait4(proc.pid, 0)

            endtime = time.monotonic()
            runtime = endtime - starttime
            stdout_file.seek(0)
            stderr_file.seek(0)
            stdout = stdout_file.read().decode(errors="replace")
            stderr = stderr_file.read().decode(errors="replace")
            returncode = 124 if timed_out else os.waitstatus_to_exitcode(waitstatus)

            if returncode != 0:
                result = FailedProcessResult(
                    execution=execution,
                    runtime=runtime,
                    stdout=stdout,
                    stderr=stderr,
                    rusage=rusage,
                    returncode=returncode,
                )
                self.error_execution(result)
            else:
                result = SuccessfulProcessResult(
                    execution=execution,
                    runtime=runtime,
                    stdout=stdout,
                    stderr=stderr,
                    rusage=rusage,
                )

            self.finalize(result)
            self.progress.advance(self.task_id)

        except OSError as e:
            self.error_execution(
                FailedProcessResult.empty(
                    execution=execution,
                    reason=str(e),
                )
            )
            self.progress.advance(self.task_id)
        finally:
            stdout_file.close()
            stderr_file.close()

    def start_execution(self, execution: Execution) -> None:
        self.progress.update(self.task_id, description=execution.as_identifier())

    def error_execution(self, process_result: FailedProcessResult):
        self.failed_executions += 1
        self.progress.update(self.task_id, failures=self.failed_executions)
        lines = [
            f"[benchr.failure]Error in {process_result.execution.as_identifier()}[/]"
        ]
        if process_result.returncode == 124:
            lines.append(
                f"Program timed out after {process_result.execution.timeout} seconds"
            )
        elif process_result.returncode != 0:
            lines.append(
                f"Program ended with non-zero return code ({process_result.returncode})"
            )
        else:
            # Pre-execution failure (command not found, spawn OSError).
            lines.append(process_result.reason or "Unknown error")
        err_console.print("\n".join(lines))

    def finalize(self, process_result: ProcessResult) -> None:
        self.finished_executions += 1
        successes = self.finished_executions - self.failed_executions
        self.progress.update(self.task_id, successes=successes)
        parser = process_result.execution.parser
        assert parser is not None, "finalize only called for fully-resolved executions"
        parsed = parser.parse(process_result)
        self.result.measurements.extend(parsed.measurements)
        self.reporter.report(process_result, parsed)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.reporter.finalize()
        return False


class ParallelExecutor(DefaultExecutor):
    """
    An executor that runs multiple tasks in parallel - mostly usable for
    collecting metrics other that runtime
    """

    pool: ThreadPoolExecutor
    futures: list[Future]
    lock: Lock
    in_process_runs: int

    def __init__(
        self,
        ncores: int,
        reporter: Reporter,
    ) -> None:
        super().__init__(reporter)

        self.pool = ThreadPoolExecutor(max_workers=ncores)
        self.futures = []
        self.lock = Lock()
        self.in_process_runs = 0

    def execute(self, execution: Execution):
        future = self.pool.submit(super().execute, execution)
        self.futures.append(future)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.pool.shutdown(wait=True)
        exceptions = [
            e for e in map(lambda f: f.exception(None), self.futures) if e is not None
        ]

        if len(exceptions) != 0:
            raise ExceptionGroup("In ParallelExecutor:", exceptions)  # type: ignore
        super().__exit__(*args)
        return False

    def start_execution(self, execution: Execution) -> None:
        with self.lock:
            self.in_process_runs += 1
            self.progress.update(
                self.task_id,
                description=f"[{self.in_process_runs} active] {execution.as_identifier()}",
            )

    def error_execution(self, process_result: FailedProcessResult):
        with self.lock:
            super().error_execution(process_result)

    def finalize(self, process_result: ProcessResult) -> None:
        with self.lock:
            self.in_process_runs -= 1
            super().finalize(process_result)


class DryExecutor(Executor):
    """
    Pseudo-executor which only prints the execution plan
    """

    def execute(self, execution: Execution):
        print("Execution:", " ".join(execution.command))
        print("Working directory:", str(execution.working_directory))
        print("Environment: ", end="")
        pprint(execution.env)
        print("Info: ", end="")
        pprint(execution.info)
        print("-" * 10)


def make_argparser(*params: str, **kwarg_params: Any) -> argparse.ArgumentParser:
    """
    Create a default argument parser from the given parameters
    """
    parser = argparse.ArgumentParser()
    user = parser.add_argument_group("User specified parameters")

    for p in params:
        user.add_argument(
            f"--{p}",
            metavar="str",
            type=str,
            required=True,
            dest=p,
        )

    for k, v in kwarg_params.items():
        t = type(v) if v is not None else str
        user.add_argument(
            f"--{k}",
            help=f"(Default: {v})",
            metavar=t.__name__,
            type=t,
            default=v,
            dest=k,
        )

    return parser


def parse_params(*params: str, **kwarg_params: Any) -> Parameters:
    """
    Create a default argument parser and run it on argv
    """
    parser = make_argparser(*params, **kwarg_params)
    args = parser.parse_args()
    return Parameters.from_namespace(args)


def main(
    config: Config,
    params: list[str],
    kwarg_params: dict[str, Any],
    reporter: Optional[Reporter] = None,
    executor: Optional[Executor] = None,
) -> Optional[ExecutionResult]:
    """
    Sane default main. config is the benchmarks configuration that will be
    executed, params is a list of required parameters from the user,
    kwarg_params are optional parameters with their default value.

    Three independent output flags are available: `--output-csv <file>`,
    `--output-json <file>` and `--output <dir>` (full per-execution tree).
    They may be combined; if none is given, results are only summarized to
    the terminal via SummaryReporter.

    If no executor is specified, it defaults to DefaultExecutor, which can be
    changed with CLI arguments --dry and --jobs/-j.
    """
    parser = make_argparser(*params, **kwarg_params)

    defp = parser.add_argument_group("Default benchr parameters")
    defp.add_argument(
        "--output",
        help="Directory to export a full per-execution tree "
        "(<suite>/<benchmark>/<run>/{stdout,stderr,seq,exitcode,rusage,result.csv})",
        metavar="dir",
        type=str,
        default=None,
        dest="__output",
    )
    defp.add_argument(
        "--output-csv",
        help="Export results as a single CSV file",
        metavar="file",
        type=str,
        default=None,
        dest="__output_csv",
    )
    defp.add_argument(
        "--output-json",
        help="Export results as a single JSON file (ExecutionResult)",
        metavar="file",
        type=str,
        default=None,
        dest="__output_json",
    )

    if executor is None:
        defp.add_argument(
            "--jobs",
            "-j",
            help="Allow this many runs in parallel (Default: 1)",
            metavar="jobs",
            type=int,
            default=1,
            dest="__jobs",
        )
        defp.add_argument(
            "--dry",
            help="Do not run, only print what would be run",
            action="store_true",
            dest="__dry",
        )

    defp.add_argument(
        "--compare",
        help="Compare results against baseline JSON file(s); first is baseline",
        metavar="json",
        nargs="+",
        type=str,
        default=None,
        dest="__compare",
    )

    ps = Parameters.from_namespace(parser.parse_args())

    executions = list(config.get_executions(ps))

    if executor is None:
        if ps.__dry:
            executor = DryExecutor()
        else:
            if reporter is None:
                reporters: list[Reporter] = [SummaryReporter()]
            else:
                reporters = [reporter]
            if ps.__output_csv:
                reporters.append(CsvReporter(Path(ps.__output_csv)))
            if ps.__output_json:
                reporters.append(JsonReporter(Path(ps.__output_json)))
            if ps.__output:
                reporters.append(DirReporter(Path(ps.__output).resolve()))
            reporter = (
                reporters[0] if len(reporters) == 1 else MixedReporter(*reporters)
            )

            # Forward --compare baselines into every SummaryReporter
            if ps.__compare is not None:
                compare_paths = [Path(f) for f in ps.__compare]
                _set_baselines_on_reporter(reporter, compare_paths)

            if ps.__jobs > 1:
                executor = ParallelExecutor(ps.__jobs, reporter)
            else:
                executor = DefaultExecutor(reporter)

    result = None
    try:
        with executor:
            result = executor.execute_all(executions)
    except KeyboardInterrupt:
        print("Interrupted")
        sys.exit(0)

    return result


def _set_baselines_on_reporter(reporter: Reporter, paths: list[Path]) -> None:
    """Walk a (possibly mixed) reporter and set baselines on all SummaryReporters."""
    if isinstance(reporter, SummaryReporter):
        reporter.set_baseline(paths)
    elif isinstance(reporter, MixedReporter):
        for r in reporter.reporters:
            _set_baselines_on_reporter(r, paths)


# TODO: Run info - date, reflog
