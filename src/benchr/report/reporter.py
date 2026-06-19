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

from benchr.grammar.benchmark import Benchmark
from benchr.core.execution import SPAWN_FAIL_RC, TIMEOUT_RC
from benchr.core.sample import Observation, Report, Run, report_to_json
from benchr.report.theme import BENCHR_THEME, console

if TYPE_CHECKING:
    from benchr.report.formatter import Formatter


# ---------------------------------------------------------------------------
# Reporter ABC
# ---------------------------------------------------------------------------


class Reporter(abc.ABC):
    """Streaming sink for benchmark progress and results.

    Called by the Runner as `start(plan)` once, `observation(obs)` per
    measurement (live progress; `obs.label` is the benchmark identifier),
    `run_done(run)` per completed Run (a command run, or a harness's single
    run), `warmup(key, n)` once per variant, and `finalize()` once.
    """

    def start(self, plan: list[Benchmark]) -> None:
        pass

    def observation(self, obs: Observation) -> None:
        pass

    def run_done(self, run: Run) -> None:
        pass

    def warmup(self, key: str, observations: int) -> None:
        """The variant's first `observations` observations were warmup."""
        pass

    def finalize(self) -> None:
        pass


class _BufferingReporter(Reporter):
    """Base for reporters that accumulate a Report and render it at
    `finalize()`. Gives subclasses a thread-safe `run_done`/`warmup`."""

    def __init__(self) -> None:
        self._report = Report()
        self._lock = threading.Lock()

    def run_done(self, run: Run) -> None:
        with self._lock:
            self._report.add(run)

    def warmup(self, key: str, observations: int) -> None:
        with self._lock:
            self._report.warmup(key, observations)


class CompositeReporter(Reporter):
    """Fan out events to multiple Reporters in registration order."""

    def __init__(self, *reporters: Reporter) -> None:
        self.reporters = list(reporters)

    def start(self, plan: list[Benchmark]) -> None:
        for r in self.reporters:
            r.start(plan)

    def observation(self, obs: Observation) -> None:
        for r in self.reporters:
            r.observation(obs)

    def run_done(self, run: Run) -> None:
        for r in self.reporters:
            r.run_done(run)

    def warmup(self, key: str, observations: int) -> None:
        for r in self.reporters:
            r.warmup(key, observations)

    def finalize(self) -> None:
        for r in self.reporters:
            r.finalize()


# ---------------------------------------------------------------------------
# CsvReporter
# ---------------------------------------------------------------------------


class CsvReporter(_BufferingReporter):
    """Buffer runs; write CSV on `finalize()`.

    Schema: `suite, benchmark, run, <variant_cols...>, metric, value, unit,
    lower_is_better, failure`. One row per Sample for successful observations;
    a failed observation (or run) emits one row with blank metric and the
    failure verdict. All runs appear, warmup included.
    """

    def __init__(self, path: Path, *, delimiter: str = ",") -> None:
        super().__init__()
        self.path = path
        self.delimiter = delimiter

    def finalize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        variant_cols = self._report.variant_keys()
        cols = ["suite", "benchmark", "run"] + variant_cols + [
            "metric", "value", "unit", "lower_is_better", "failure"
        ]
        with open(self.path, "wt", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, delimiter=self.delimiter)
            w.writeheader()
            for r in self._report.runs:
                variant_map = dict(r.variant)
                base: dict[str, Any] = {"suite": r.suite, "benchmark": r.benchmark,
                                        "run": r.run}
                for k in variant_cols:
                    base[k] = variant_map.get(k, "")
                obs_list = r.observations or [Observation(failure=r.failure)]
                for obs in obs_list:
                    failure = obs.failure or (r.failure if not obs.samples else None)
                    if failure or not obs.samples:
                        w.writerow({**base, "metric": "", "value": "", "unit": "",
                                    "lower_is_better": "", "failure": failure or ""})
                        continue
                    for s in obs.samples:
                        w.writerow({**base,
                                    "metric": s.metric, "value": s.value, "unit": s.unit,
                                    "lower_is_better": (
                                        "" if s.lower_is_better is None
                                        else str(s.lower_is_better)),
                                    "failure": ""})


# ---------------------------------------------------------------------------
# JsonReporter
# ---------------------------------------------------------------------------


class JsonReporter(_BufferingReporter):
    """Buffer runs in memory, write a single JSON file on finalize().

    `include_output` keeps each run's stdout/stderr/env in the JSON (off by
    default — they bloat the file and are rarely needed offline)."""

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
    policies expose a `max_runs()`; otherwise displays `?`.
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

    def observation(self, obs: Observation) -> None:
        with self._lock:
            if not obs.is_failure():
                self._successes += 1
            else:
                self._failures += 1
            if self._progress is not None and self._task_id is not None:
                self._progress.update(
                    self._task_id,
                    description=markup_escape(obs.label),
                    failures=self._failures,
                    successes=self._successes,
                )
                self._progress.advance(self._task_id)
            else:
                self._print_plain(obs)

    def finalize(self) -> None:
        if self._progress is not None and self._task_id is not None:
            self._progress.stop()

    # ----- helpers ---------------------------------------------------

    def _print_plain(self, obs: Observation) -> None:
        n = self._failures + self._successes
        total_str = str(self._total) if self._total is not None else "?"
        if not obs.is_failure():
            tag = "[benchr.success]ok[/]"
        else:
            tag = f"[benchr.failure]FAIL[/] ({obs.failure})"
        self._console.print(f"[{n}|{total_str}] {markup_escape(obs.label)} {tag}")

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
# SummaryReporter (delegates to a Formatter; see report/formatter.py)
# ---------------------------------------------------------------------------


class SummaryReporter(_BufferingReporter):
    """Buffer runs; format on finalize().

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
        from benchr.report.formatter import DefaultSummary

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
            self._console.print("[benchr.label]Failures:[/]")
            for run in self._report.failures:
                self._console.print("  " + self._failure_line(run))

    @staticmethod
    def _failure_line(run: Run) -> str:
        if run.returncode == TIMEOUT_RC:
            verdict = f"[benchr.failure]timeout (exit {TIMEOUT_RC})[/]"
        elif run.returncode == SPAWN_FAIL_RC:
            verdict = f"[benchr.failure]spawn failed[/]: {run.failure or 'unknown'}"
        else:
            verdict = f"[benchr.failure]exit {run.returncode}[/]"
        return (f"[benchr.failure]✗[/] {markup_escape(run.identifier())}"
                f" — {verdict}: {markup_escape(run.message) or '(no output)'}")


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
