"""Streaming reporter sinks.

A ``Reporter`` is called by the Runner three times: ``start(plan)`` once,
``sample(sched, pr, samples)`` per execution, ``finalize()`` once at the end.

Built-ins:
    Mixed      fan-out to multiple reporters
    Csv        stream rows to a CSV file
    Json       buffer in memory; serialize on finalize
    Dir        per-execution tree (<suite>/<bench>/<run>/{stdout,stderr,...})
    Table      buffer and print a final table
    Summary    buffer and run a Formatter (see report/formatter.py)
"""

from __future__ import annotations

import abc
import csv
import threading
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table as RichTable
from rich.theme import Theme

from benchr.grammar.benchmark import Benchmark
from benchr.grammar.execution import (
    ProcessResult,
    ScheduledExecution,
    SuccessfulProcessResult,
)
from benchr.report.sample import Report, Sample, info_keys, report_to_json


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

    Called by the Runner as ``start(plan)`` once, ``sample(sched, pr, samples)``
    per execution, ``finalize()`` once. ``plan`` is the flattened list of
    Benchmarks the runner has materialized from the suites.
    """

    def start(self, plan: list[Benchmark]) -> None:
        pass

    @abc.abstractmethod
    def sample(
        self,
        sched: ScheduledExecution,
        pr: ProcessResult,
        samples: list[Sample],
    ) -> None: ...

    def finalize(self) -> None:
        pass


class Mixed(Reporter):
    """Fan out events to multiple Reporters in registration order."""

    def __init__(self, *reporters: Reporter) -> None:
        self.reporters = list(reporters)

    def start(self, plan):
        for r in self.reporters:
            r.start(plan)

    def sample(self, sched, pr, samples):
        for r in self.reporters:
            r.sample(sched, pr, samples)

    def finalize(self):
        for r in self.reporters:
            r.finalize()


# ---------------------------------------------------------------------------
# Csv
# ---------------------------------------------------------------------------


class Csv(Reporter):
    """Stream rows to a CSV file. Header is fixed on the first non-empty sample.

    Schema: suite, benchmark, run, phase, <info_cols...>, metric, value, unit,
    lower_is_better. Info columns come from the first sample's info keys.
    """

    def __init__(self, path: Path, *, delimiter: str = ",") -> None:
        self.path = path
        self.delimiter = delimiter
        self._file = None
        self._writer: csv.DictWriter | None = None
        self._info_cols: list[str] | None = None
        self._lock = threading.Lock()

    def start(self, plan):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(self.path, "wt", newline="")
        self._writer = None
        self._info_cols = None

    def sample(self, sched, pr, samples):
        if not samples:
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


class Json(Reporter):
    """Buffer samples in memory, write a single JSON file on finalize()."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._report = Report()
        self._lock = threading.Lock()

    def sample(self, sched, pr, samples):
        with self._lock:
            self._report.extend(samples)

    def finalize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(report_to_json(self._report))


# ---------------------------------------------------------------------------
# Dir
# ---------------------------------------------------------------------------


class Dir(Reporter):
    """Per-execution tree at ``<out>/<suite>/<bench>/<run>/``.

    Files: stdout, stderr, exitcode, rusage, seq (cwd + cmd + info + phase).
    Run numbers are deterministic per (suite, benchmark) based on submission
    order (pre-numbered in start()).
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self._counters: dict[tuple[str, str, str], int] = {}
        self._lock = threading.Lock()

    def start(self, plan):
        self._counters = {}
        self.root.mkdir(parents=True, exist_ok=True)

    def sample(self, sched, pr, samples):
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

        if pr.stdout is not None:
            (run_dir / "stdout").write_text(pr.stdout)
        if pr.stderr is not None:
            (run_dir / "stderr").write_text(pr.stderr)

        rc = 0 if isinstance(pr, SuccessfulProcessResult) else pr.returncode
        (run_dir / "exitcode").write_text(f"{rc}\n")

        if pr.rusage is not None:
            ru_lines = [
                f"{f}={getattr(pr.rusage, f)}"
                for f in dir(pr.rusage)
                if f.startswith("ru_")
            ]
            (run_dir / "rusage").write_text("\n".join(ru_lines) + "\n")


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------


class Table(Reporter):
    """Buffer all samples; print a rich Table on finalize()."""

    def __init__(self, target_console: Console | None = None) -> None:
        self._report = Report()
        self._console = target_console or console
        self._lock = threading.Lock()

    def sample(self, sched, pr, samples):
        with self._lock:
            self._report.extend(samples)

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
# Summary (delegates to a Formatter; see report/formatter.py)
# ---------------------------------------------------------------------------


class Summary(Reporter):
    """Buffer samples; format on finalize().

    Takes an optional ``formatter`` (any callable ``(Report, baseline=...) -> str``).
    Defaults to ``DefaultSummary``.
    """

    def __init__(
        self,
        formatter: Any | None = None,
        *,
        baseline: list[Path] | None = None,
        target_console: Console | None = None,
    ) -> None:
        from benchr.report.formatter import DefaultSummary

        self._report = Report()
        self._formatter = formatter or DefaultSummary()
        self._baseline = baseline or []
        self._console = target_console or console
        self._lock = threading.Lock()

    def sample(self, sched, pr, samples):
        with self._lock:
            self._report.extend(samples)

    def set_baseline(self, paths: list[Path]) -> None:
        self._baseline = list(paths)

    def finalize(self):
        out = self._formatter(self._report, baseline=self._baseline)
        if out:
            self._console.print(out)
