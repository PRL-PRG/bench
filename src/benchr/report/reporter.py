"""Streaming reporter sinks.

A ``Reporter`` is called by the Runner three times: ``start(plan)`` once,
``sample(sched, result, samples)`` per execution, ``finalize()`` once at the end.

Built-ins:
    Mixed      fan-out to multiple reporters
    Csv        stream rows to a CSV file
    Json       buffer in memory; serialize on finalize
    Dir        per-execution tree (<suite>/<bench>/<phase>/<run>/{stdout,stderr,...})
    Table      buffer and print a final table
    Progress   live spinner + bar; ``transient`` so it clears at finalize
    Summary    buffer and run a Formatter (see report/formatter.py)
"""

from __future__ import annotations

import abc
import csv
import threading
from pathlib import Path
from typing import IO, Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress as RichProgress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table as RichTable
from rich.theme import Theme

from benchr.grammar.benchmark import Benchmark
from benchr.grammar.execution import (
    ExecutionResult,
    ScheduledExecution,
)
from benchr.report.sample import (
    RunRecord,
    Report,
    Sample,
    info_keys,
    report_to_json,
)


# Centralized rich theme — change colors in one place.
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


class Mixed(Reporter):
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
# Csv
# ---------------------------------------------------------------------------


class Csv(Reporter):
    """Stream rows to a CSV file. Header is fixed on the first non-empty sample.

    Schema: suite, benchmark, run, phase, <info_cols...>, metric, value, unit,
    lower_is_better. Info columns are fixed from the *first* sample's info keys;
    in a heterogeneous run (e.g. a matrix variant alongside a plain benchmark)
    info keys absent from the first sample are silently dropped — use ``--json``
    for complete fidelity.

    Note: one row per *Sample*, so failed runs (which emit no samples) do not
    appear here — use ``--json`` / ``--dir`` to capture failures.
    """

    def __init__(self, path: Path, *, delimiter: str = ",") -> None:
        self.path = path
        self.delimiter = delimiter
        self._file: IO[str] | None = None
        self._writer: csv.DictWriter | None = None
        self._info_cols: list[str] | None = None
        self._lock = threading.Lock()

    def start(self, plan):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "wt", newline="")
        self._writer = None
        self._info_cols = None

    def sample(self, sched, result, samples):
        if not samples or self._file is None:
            return
        with self._lock:
            if self._writer is None:
                self._info_cols = [k for k, _ in samples[0].info]
                cols = ["suite", "benchmark", "run", "phase"] + self._info_cols + [
                    "metric", "value", "unit", "lower_is_better"
                ]
                self._writer = csv.DictWriter(self._file, fieldnames=cols, delimiter=self.delimiter)
                self._writer.writeheader()
            for s in samples:
                row = {
                    "suite": s.suite, "benchmark": s.benchmark, "run": s.run,
                    "phase": s.phase, "metric": s.metric, "value": s.value,
                    "unit": s.unit,
                    "lower_is_better": (
                        "" if s.lower_is_better is None else str(s.lower_is_better)
                    ),
                }
                for k in self._info_cols or []:
                    row[k] = dict(s.info).get(k, "")
                self._writer.writerow(row)
            self._file.flush()

    def finalize(self):
        if self._file is not None:
            self._file.close()
            self._file = None


# ---------------------------------------------------------------------------
# Json
# ---------------------------------------------------------------------------


class Json(_BufferingReporter):
    """Buffer samples + runs in memory, write a single JSON file on finalize()."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def finalize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(report_to_json(self._report))


# ---------------------------------------------------------------------------
# Dir
# ---------------------------------------------------------------------------


class Dir(Reporter):
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
        lines.extend(f"info[{k}]={v}" for k, v in sched.info)
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
# Table
# ---------------------------------------------------------------------------


class Table(_BufferingReporter):
    """Buffer all samples; print a rich Table on finalize()."""

    def __init__(self, target_console: Console | None = None) -> None:
        super().__init__()
        self._console = target_console or console

    def finalize(self):
        info_cols = info_keys(self._report.samples)
        cols = ["suite", "benchmark", "run", "phase"] + info_cols + [
            "metric", "value", "unit", "lib"
        ]
        t = RichTable(show_header=True, show_edge=False, pad_edge=False)
        for c in cols:
            t.add_column(c)
        for s in self._report.samples:
            row = [s.suite, s.benchmark, str(s.run), s.phase]
            row += [dict(s.info).get(k, "") for k in info_cols]
            lib = "" if s.lower_is_better is None else ("↓" if s.lower_is_better else "↑")
            row += [s.metric, f"{s.value:g}", s.unit, lib]
            t.add_row(*row)
        self._console.print()
        self._console.print(t)


# ---------------------------------------------------------------------------
# Progress: live spinner + bar while the run is in flight
# ---------------------------------------------------------------------------


class Progress(Reporter):
    """Live progress over the planned benchmarks.

    On a terminal, renders a rich spinner + bar + counter + description and
    clears itself (``transient=True``) before the Summary prints. On a
    non-terminal sink (piped output, CI log, file redirect), falls back to
    plain one-line-per-sample output: ``[n/total] <bench id> ok | FAIL exit N``.
    Total is known when every benchmark's policies expose a ``max_runs()``;
    otherwise displays ``?`` and (on terminals) draws an indeterminate bar.
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
                    "([benchr.failure]{task.fields[failures]}[/]"
                    "/[benchr.success]{task.fields[successes]}[/]"
                    "/{task.fields[total_str]})"
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
        elif result.returncode == 124:
            tag = "[benchr.failure]FAIL timeout[/]"
        elif result.returncode == -1:
            tag = f"[benchr.failure]FAIL spawn[/] ({result.failure or 'unknown'})"
        else:
            tag = f"[benchr.failure]FAIL exit {result.returncode}[/]"
        self._console.print(f"[{n}/{total_str}] {sched.identifier()} {tag}")

    @staticmethod
    def _compute_total(plan: list[Benchmark]) -> int | None:
        total = 0
        for b in plan:
            w, m = b.warmup.max_runs(), b.measure.max_runs()
            if w is None or m is None:
                return None
            total += w + m
        return total


# ---------------------------------------------------------------------------
# Summary (delegates to a Formatter; see report/formatter.py)
# ---------------------------------------------------------------------------


class Summary(_BufferingReporter):
    """Buffer samples; format on finalize().

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
                self._console.print("  " + _failure_line(run))


def _failure_line(run: RunRecord) -> str:
    if run.returncode == 124:
        verdict = "[benchr.failure]timeout (exit 124)[/]"
    elif run.returncode == -1:
        verdict = f"[benchr.failure]spawn failed[/]: {run.failure or 'unknown'}"
    else:
        verdict = f"[benchr.failure]exit {run.returncode}[/]"
    return f"[benchr.failure]✗[/] {run.identifier()} — {verdict}: {run.message or '(no output)'}"
