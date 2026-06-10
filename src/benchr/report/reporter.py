"""Streaming reporter sinks."""

from __future__ import annotations

import abc
import csv
import threading
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress as RichProgress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)

from benchr.grammar.benchmark import Benchmark
from benchr.grammar.execution import (
    SPAWN_FAIL_RC,
    TIMEOUT_RC,
    ExecutionResult,
    ScheduledExecution,
)
from benchr.report.sample import (
    Report,
    RunRecord,
    Sample,
    report_to_json,
)
from benchr.report.theme import BENCHR_THEME, console


# ---------------------------------------------------------------------------
# Reporter ABC
# ---------------------------------------------------------------------------


class Reporter(abc.ABC):
    """Streaming sink for per-execution results.

    Called by the Runner as ``start(plan)`` once, ``sample(sched, result, samples)``
    per execution, ``finalize()`` once. ``plan`` is the flattened list of
    Benchmarks the runner has materialized from the suites.
    """

    def start(self, plan: list[Benchmark]) -> None:
        pass

    @abc.abstractmethod
    def sample(
        self,
        sched: ScheduledExecution,
        result: ExecutionResult,
        samples: list[Sample],
    ) -> None: ...

    def finalize(self) -> None:
        pass


class _BufferingReporter(Reporter):
    """Base for reporters that accumulate a Report in memory and render it at
    ``finalize()``. Subclasses get a thread-safe ``sample`` for free and
    override ``finalize`` to emit ``self._report``."""

    def __init__(self) -> None:
        self._report = Report()
        self._lock = threading.Lock()

    def sample(self, sched, result, samples):
        with self._lock:
            self._report.record(sched, result, samples)


class CompositeReporter(Reporter):
    """Fan out events to multiple Reporters in registration order."""

    def __init__(self, *reporters: Reporter) -> None:
        self.reporters = list(reporters)

    def start(self, plan):
        for r in self.reporters:
            r.start(plan)

    def sample(self, sched, result, samples):
        for r in self.reporters:
            r.sample(sched, result, samples)

    def finalize(self):
        for r in self.reporters:
            r.finalize()


# ---------------------------------------------------------------------------
# CsvReporter
# ---------------------------------------------------------------------------


class CsvReporter(_BufferingReporter):
    """Buffer per-execution rows; write CSV on ``finalize()``.

    Schema: ``suite, benchmark, run, phase, <variant_cols...>, metric, value,
    unit, lower_is_better, failure``. Variant columns are the union of every
    axis observed across all runs (cells absent in a particular run are blank).

    One row per Sample for successful runs. Failed runs (which emit no samples)
    are still represented: one row with blank metric/value/unit and the failure
    verdict in the ``failure`` column.
    """

    def __init__(self, path: Path, *, delimiter: str = ",") -> None:
        super().__init__()
        self.path = path
        self.delimiter = delimiter

    def finalize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        variant_cols = self._report.variant_keys()
        cols = ["suite", "benchmark", "run", "phase"] + variant_cols + [
            "metric", "value", "unit", "lower_is_better", "failure"
        ]
        with open(self.path, "wt", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, delimiter=self.delimiter)
            w.writeheader()
            for r in self._report.runs:
                variant_map = dict(r.variant)
                base = {
                    "suite": r.suite,
                    "benchmark": r.benchmark,
                    "run": r.run,
                    "phase": r.phase,
                }
                for k in variant_cols:
                    base[k] = variant_map.get(k, "")
                if r.is_failure():
                    row = {**base,
                           "metric": "", "value": "", "unit": "",
                           "lower_is_better": "",
                           "failure": r.failure or ""}
                    w.writerow(row)
                    continue
                for s in r.samples:
                    row = {**base,
                           "metric": s.metric,
                           "value": s.value,
                           "unit": s.unit,
                           "lower_is_better": (
                               "" if s.lower_is_better is None else str(s.lower_is_better)
                           ),
                           "failure": ""}
                    w.writerow(row)


# ---------------------------------------------------------------------------
# JsonReporter
# ---------------------------------------------------------------------------


class JsonReporter(_BufferingReporter):
    """Buffer runs in memory, write a single JSON file on finalize()."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def finalize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(report_to_json(self._report))


# ---------------------------------------------------------------------------
# DirReporter
# ---------------------------------------------------------------------------


class DirReporter(Reporter):
    """Per-execution tree at ``<out>/<suite>/<bench>/<phase>/<run>/``.

    Files: stdout, stderr, exitcode, rusage, seq (cwd + cmd + info + phase).
    Run numbers count up per (suite, benchmark, phase) in execution order.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._counters: dict[tuple[str, str, str], int] = {}
        self._lock = threading.Lock()

    def start(self, plan):
        self._counters = {}
        self.root.mkdir(parents=True, exist_ok=True)

    def sample(self, sched, result, samples):
        key = (sched.suite, sched.benchmark, sched.phase)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
            n = self._counters[key]

        run_dir = self.root / sched.suite / sched.benchmark / sched.phase / str(n)
        run_dir.mkdir(parents=True, exist_ok=True)

        lines = [
            f"cwd={sched.execution.cwd}",
            f"command={' '.join(sched.execution.command)}",
            f"phase={sched.phase}",
            f"run={sched.run}",
        ]
        lines.extend(f"variant[{k}]={v}" for k, v in sched.variant)
        if sched.variant_label:
            lines.append(f"variant_label={sched.variant_label}")
        (run_dir / "seq").write_text("\n".join(lines) + "\n")

        (run_dir / "stdout").write_text(result.stdout)
        (run_dir / "stderr").write_text(result.stderr)
        (run_dir / "exitcode").write_text(f"{result.returncode}\n")

        if result.rusage is not None:
            ru_lines = [
                f"{f}={getattr(result.rusage, f)}"
                for f in dir(result.rusage)
                if f.startswith("ru_")
            ]
            (run_dir / "rusage").write_text("\n".join(ru_lines) + "\n")


# ---------------------------------------------------------------------------
# ProgressReporter: live spinner + bar while the run is in flight
# ---------------------------------------------------------------------------


class ProgressReporter(Reporter):
    """Live progress over the planned benchmarks.

    On a terminal, renders a progress bar and clears itself before the SummaryReporter
    prints. On a non-terminal it falls back to plain one-line-per-sample
    output. Total is known when every benchmark's policies expose a
    ``max_runs()``; otherwise displays ``?`` and (on terminals) draws an
    indeterminate bar. """

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
                    "([benchr.failure]{task.fields[failures]}[/]"
                    "|[benchr.success]{task.fields[successes]}[/]"
                    "|{task.fields[total_str]})"
                ),
                TextColumn("[benchr.in_process]{task.description}[/]"),
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

    def sample(self, sched: ScheduledExecution, result: ExecutionResult,
               samples: list[Sample]) -> None:
        with self._lock:
            if not result.is_failure():
                self._successes += 1
            else:
                self._failures += 1
            if self._progress is not None and self._task_id is not None:
                self._progress.update(
                    self._task_id,
                    description=sched.identifier(),
                    failures=self._failures,
                    successes=self._successes,
                )
                self._progress.advance(self._task_id)
            else:
                self._print_plain(sched, result)

    def finalize(self) -> None:
        if self._progress is not None and self._task_id is not None:
            self._progress.stop()

    # ----- helpers ---------------------------------------------------

    def _print_plain(self, sched: ScheduledExecution, result: ExecutionResult) -> None:
        n = self._failures + self._successes
        total_str = str(self._total) if self._total is not None else "?"
        if not result.is_failure():
            tag = "[benchr.success]ok[/]"
        elif result.returncode == TIMEOUT_RC:
            tag = "[benchr.failure]FAIL timeout[/]"
        elif result.returncode == SPAWN_FAIL_RC:
            tag = f"[benchr.failure]FAIL spawn[/] ({result.failure or 'unknown'})"
        else:
            tag = f"[benchr.failure]FAIL exit {result.returncode}[/]"
        self._console.print(f"[{n}|{total_str}] {sched.identifier()} {tag}")

    @staticmethod
    def _compute_total(plan: list[Benchmark]) -> int | None:
        total = 0
        for b in plan:
            w, m = b.warmup_policy().max_runs(), b.measure_policy().max_runs()
            if w is None or m is None:
                return None
            total += w + m
        return total


# ---------------------------------------------------------------------------
# SummaryReporter (delegates to a Formatter; see report/formatter.py)
# ---------------------------------------------------------------------------


class SummaryReporter(_BufferingReporter):
    """Buffer runs; format on finalize().

    Takes an optional ``formatter`` (any callable ``(Report, baseline=...) -> str``).
    Defaults to ``DefaultSummary``. After the formatter output, appends a
    ``Failures:`` block listing every failed run (one line per failure)
    so users can see *why* something failed without having to re-run with
    ``--dir``.
    """

    def __init__(
        self,
        formatter: Any | None = None,
        *,
        baseline: list[Path] | None = None,
        target_console: Console | None = None,
    ) -> None:
        from benchr.report.formatter import DefaultSummary

        super().__init__()
        self._formatter = formatter or DefaultSummary()
        self._baseline = baseline or []
        self._console = target_console or console

    def set_baseline(self, paths: list[Path]) -> None:
        self._baseline = list(paths)

    def finalize(self):
        out = self._formatter(self._report, baseline=self._baseline)
        if out:
            self._console.print(out)
        if self._report.failures:
            self._console.print()
            self._console.print("[benchr.label]Failures:[/]")
            for run in self._report.failures:
                self._console.print("  " + self._failure_line(run))

    @staticmethod
    def _failure_line(run: RunRecord) -> str:
        if run.returncode == TIMEOUT_RC:
            verdict = f"[benchr.failure]timeout (exit {TIMEOUT_RC})[/]"
        elif run.returncode == SPAWN_FAIL_RC:
            verdict = f"[benchr.failure]spawn failed[/]: {run.failure or 'unknown'}"
        else:
            verdict = f"[benchr.failure]exit {run.returncode}[/]"
        return f"[benchr.failure]✗[/] {run.identifier()} — {verdict}: {run.message or '(no output)'}"


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
