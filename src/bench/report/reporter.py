"""Streaming reporter sinks."""

from __future__ import annotations

import abc
import csv
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markup import escape as markup_escape
from rich.progress import (
    BarColumn,
    Progress as RichProgress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from bench.grammar.benchmark import Benchmark
from bench.core.execution import SPAWN_FAIL_RC, TIMEOUT_RC
from bench.core.sample import Iteration, Report, Run, Sample, report_to_json
from bench.report.theme import BENCHR_THEME, console

if TYPE_CHECKING:
    from bench.report.formatter import Formatter


# ---------------------------------------------------------------------------
# Reporter ABC
# ---------------------------------------------------------------------------


class Reporter(abc.ABC):
    """Streaming sink for benchmark progress and results."""

    def start(self, plan: list[Benchmark]) -> None:
        pass

    def iteration(self, it: Iteration, label: str) -> None:
        pass

    def run_done(self, run: Run) -> None:
        pass

    def finalize(self) -> None:
        pass


class _BufferingReporter(Reporter):
    """Base for reporters that accumulate a Report and render it at
    `finalize()`. Gives subclasses a thread-safe `run_done`."""

    def __init__(self) -> None:
        self._report = Report()
        self._lock = threading.Lock()

    def run_done(self, run: Run) -> None:
        with self._lock:
            self._report.add(run)


class CompositeReporter(Reporter):
    """Fan out events to multiple Reporters in registration order."""

    def __init__(self, *reporters: Reporter) -> None:
        self.reporters = list(reporters)

    def start(self, plan: list[Benchmark]) -> None:
        for r in self.reporters:
            r.start(plan)

    def iteration(self, it: Iteration, label: str) -> None:
        for r in self.reporters:
            r.iteration(it, label)

    def run_done(self, run: Run) -> None:
        for r in self.reporters:
            r.run_done(run)

    def finalize(self) -> None:
        for r in self.reporters:
            r.finalize()


# ---------------------------------------------------------------------------
# CsvReporter
# ---------------------------------------------------------------------------


def _sample_row(base: dict[str, Any], s: Sample) -> dict[str, Any]:
    return {
        **base,
        "metric": s.metric,
        "value": s.value,
        "unit": s.unit,
        "lower_is_better": "" if s.lower_is_better is None else str(s.lower_is_better),
        "outlier": str(s.outlier),
        "failure": "",
    }


def _blank_row(base: dict[str, Any], failure: str) -> dict[str, Any]:
    return {
        **base,
        "metric": "",
        "value": "",
        "unit": "",
        "lower_is_better": "",
        "outlier": "",
        "failure": failure,
    }


class CsvReporter(_BufferingReporter):
    """Buffer runs, write CSV on `finalize()`.

    Schema: `suite, benchmark, run, <variant_cols...>, metric, value, unit,
    lower_is_better, outlier, failure`. One row per Sample, for each iteration's samples
    and then the run's whole-process samples. A failed iteration (or run) emits
    one row with blank metric and the failure verdict. All runs appear, warmup
    included.
    """

    def __init__(self, path: Path, *, delimiter: str = ",") -> None:
        super().__init__()
        self.path = path
        self.delimiter = delimiter

    def finalize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        variant_cols = self._report.variant_keys()
        cols = (
            ["suite", "benchmark", "run"]
            + variant_cols
            + ["metric", "value", "unit", "lower_is_better", "outlier", "failure"]
        )
        with open(self.path, "wt", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, delimiter=self.delimiter)
            w.writeheader()
            for r in self._report.runs:
                variant_map = dict(r.variant)
                base: dict[str, Any] = {
                    "suite": r.suite,
                    "benchmark": r.benchmark,
                    "run": r.run,
                }
                for k in variant_cols:
                    base[k] = variant_map.get(k, "")
                iters = r.iterations or [Iteration(failure=r.failure)]
                emitted = False
                for it in iters:
                    failure = it.failure or (r.failure if not it.samples else None)
                    if failure:
                        w.writerow(_blank_row(base, failure))
                        emitted = True
                        continue
                    for s in it.samples:
                        w.writerow(_sample_row(base, s))
                        emitted = True
                for s in r.process_samples:
                    w.writerow(_sample_row(base, s))
                    emitted = True
                # A run that produced nothing (no samples, no failure) still appears.
                if not emitted:
                    w.writerow(_blank_row(base, ""))


# ---------------------------------------------------------------------------
# JsonReporter
# ---------------------------------------------------------------------------


class JsonReporter(_BufferingReporter):
    """Buffer runs in memory, write a single JSON file on finalize().

    `include_output` keeps each run's stdout/stderr/env in the JSON (off by
    default, they bloat the file and are rarely needed offline)."""

    def __init__(self, path: Path, *, include_output: bool = False) -> None:
        super().__init__()
        self.path = path
        self.include_output = include_output

    def finalize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            report_to_json(self._report, include_output=self.include_output)
        )


# ---------------------------------------------------------------------------
# DirReporter
# ---------------------------------------------------------------------------


class DirReporter(Reporter):
    """Per-run tree at `<out>/<suite>/<bench>/<n>/`.

    Files: stdout, stderr, exitcode, seq (cwd + cmd + info). Directories count
    up per (suite, benchmark) in completion order.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._counters: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def start(self, plan: list[Benchmark]) -> None:
        self._counters = {}
        self.root.mkdir(parents=True, exist_ok=True)

    def run_done(self, run: Run) -> None:
        key = (run.suite, run.benchmark)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
            n = self._counters[key]

        run_dir = self.root / run.suite / run.benchmark / str(n)
        run_dir.mkdir(parents=True, exist_ok=True)

        lines = [
            f"cwd={run.cwd}",
            f"command={' '.join(run.command)}",
            f"run={run.run}",
        ]
        lines.extend(f"variant[{k}]={v}" for k, v in run.variant)
        if run.variant_label:
            lines.append(f"variant_label={run.variant_label}")
        (run_dir / "seq").write_text("\n".join(lines) + "\n")

        (run_dir / "stdout").write_text(run.stdout)
        (run_dir / "stderr").write_text(run.stderr)
        (run_dir / "exitcode").write_text(f"{run.returncode}\n")


# ---------------------------------------------------------------------------
# ProgressReporter: live spinner + bar while the run is in flight
# ---------------------------------------------------------------------------


class ProgressReporter(Reporter):
    """Live progress over the planned benchmarks, one tick per observation.

    On a terminal, renders a progress bar and clears itself before the
    SummaryReporter prints. On a non-terminal it falls back to plain
    one-line-per-observation output. Total is known when every benchmark's
    policies expose a `max_runs()`, otherwise displays `?`.
    """

    def __init__(self, target_console: Console | None = None) -> None:
        self._console = target_console or console
        self._is_tty = self._console.is_terminal
        self._task_id: TaskID | None = None
        self._failures = 0
        self._successes = 0
        self._total: int | None = None
        self._lock = threading.Lock()
        self._progress = (
            RichProgress(
                SpinnerColumn(),
                TimeElapsedColumn(),
                BarColumn(),
                TextColumn(
                    "([bench.failure]{task.fields[failures]}[/]"
                    "|[bench.success]{task.fields[successes]}[/]"
                    "|{task.fields[total_str]})"
                ),
                TextColumn("[bench.in_process]{task.description}[/]"),
                console=self._console,
                transient=True,
            )
            if self._is_tty
            else None
        )

    def start(self, plan: list[Benchmark]) -> None:
        self._total = self._compute_total(plan)
        if self._progress is not None:
            self._progress.start()
            self._task_id = self._progress.add_task(
                "Running",
                total=self._total,
                failures=0,
                successes=0,
                total_str=str(self._total) if self._total is not None else "?",
            )

    def iteration(self, it: Iteration, label: str) -> None:
        with self._lock:
            if not it.is_failure():
                self._successes += 1
            else:
                self._failures += 1
            if self._progress is not None and self._task_id is not None:
                self._progress.update(
                    self._task_id,
                    description=markup_escape(label),
                    failures=self._failures,
                    successes=self._successes,
                )
                self._progress.advance(self._task_id)
            else:
                self._print_plain(it, label)

    def finalize(self) -> None:
        if self._progress is not None and self._task_id is not None:
            self._progress.stop()

    # ----- helpers ---------------------------------------------------

    def _print_plain(self, it: Iteration, label: str) -> None:
        n = self._failures + self._successes
        total_str = str(self._total) if self._total is not None else "?"
        if not it.is_failure():
            tag = "[bench.success]ok[/]"
        else:
            tag = f"[bench.failure]FAIL[/] ({it.failure})"
        self._console.print(f"[{n}|{total_str}] {markup_escape(label)} {tag}")

    @staticmethod
    def _compute_total(plan: list[Benchmark]) -> int | None:
        total = 0
        for b in plan:
            w, m = b.warmup.max_runs(), b.runs.max_runs()
            if w is None or m is None:
                return None
            total += w + m
        return total


# ---------------------------------------------------------------------------
# SummaryReporter (delegates to a Formatter, see report/formatter.py)
# ---------------------------------------------------------------------------


class SummaryReporter(_BufferingReporter):
    """Buffer runs, format on finalize().

    Takes an optional `formatter` (any callable `(Report, baseline=...) -> str`).
    Defaults to `DefaultSummary`. After the formatter output, appends a
    `Failures:` block listing every failed run.
    """

    def __init__(
        self,
        formatter: Formatter | None = None,
        *,
        baseline: list[Path] | None = None,
        target_console: Console | None = None,
    ) -> None:
        from bench.report.formatter import DefaultSummary

        super().__init__()
        self._formatter = formatter or DefaultSummary()
        self._baseline = baseline or []
        self._console = target_console or console

    def set_baseline(self, paths: list[Path]) -> None:
        self._baseline = list(paths)

    def finalize(self) -> None:
        out = self._formatter(self._report, baseline=self._baseline)
        if out:
            self._console.print(out)
        if self._report.failures:
            self._console.print()
            self._console.print("[bench.label]Failures:[/]")
            for run in self._report.failures:
                self._console.print("  " + self._failure_line(run))

    @staticmethod
    def _failure_line(run: Run) -> str:
        if run.returncode == TIMEOUT_RC:
            verdict = f"[bench.failure]timeout (exit {TIMEOUT_RC})[/]"
        elif run.returncode == SPAWN_FAIL_RC:
            verdict = f"[bench.failure]spawn failed[/]: {run.failure or 'unknown'}"
        else:
            verdict = f"[bench.failure]exit {run.returncode}[/]"
        return (
            f"[bench.failure]✗[/] {markup_escape(run.identifier())}"
            f" — {verdict}: {markup_escape(run.message) or '(no output)'}"
        )


__all__ = [
    "BENCHR_THEME",
    "console",
    "Reporter",
    "CompositeReporter",
    "CsvReporter",
    "JsonReporter",
    "DirReporter",
    "ProgressReporter",
    "SummaryReporter",
]
