"""Streaming reporter sinks."""

from __future__ import annotations

import abc
import csv
import dataclasses
import itertools
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cattrs import unstructure

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape as markup_escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress as RichProgress,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.text import Text

from bench.core.environment import Diagnostic, Environment
from bench.core.invocation import (
    SPAWN_FAIL_RC,
    TIMEOUT_RC,
    format_benchmark,
    format_identifier,
)
from bench.core.results import Report, Execution, report_to_json
from bench.report.theme import BENCHR_THEME, console

if TYPE_CHECKING:
    from bench.builder.benchmark import Benchmark
    from bench.report.formatter import Formatter


def print_diagnostics(diagnostics: list[Diagnostic], title: str) -> None:
    if not diagnostics:
        return
    console.print(f"\n[bench.label]{title}:[/]")
    for d in diagnostics:
        tag = "[bench.failure]✗[/]" if d.severity == "high" else "[bench.warning]!![/]"
        console.print(f"  {tag} {markup_escape(d.message)}")
        if d.fix:
            console.print(f"      [dim]fix:[/] {markup_escape(d.fix)}")


# ---------------------------------------------------------------------------
# Reporter ABC
# ---------------------------------------------------------------------------


def _environment_comments(env: Environment | None) -> list[str]:
    """`# key: value` lines for each known field, for a CSV preamble."""
    if env is None:
        return []
    return [f"# {k}: {v}\n" for k, v in env.display_items()]


class Reporter(abc.ABC):
    """Streaming sink for benchmark progress and results."""

    def set_environment(
        self, environment: Environment | None, diagnostics: list[Diagnostic]
    ) -> None:
        """Inject the collected machine snapshot. Called once before `start()`.

        Reporters that embed the environment override this. The rest ignore it.
        """
        pass

    def start(self, plan: list[Benchmark]) -> None:
        pass

    def benchmark_start(self, b: Benchmark) -> None:
        pass

    def execution_done(self, execution: Execution) -> None:
        pass

    def benchmark_done(self, b: Benchmark, executions: list[Execution]) -> None:
        pass

    def finalize(self, report: Report) -> None:
        pass


class _EnvironmentAware:
    """Mixin for sinks that embed the machine snapshot; stores what
    `set_environment` injects."""

    _environment: Environment | None
    _diagnostics: list[Diagnostic]

    def set_environment(
        self, environment: Environment | None, diagnostics: list[Diagnostic]
    ) -> None:
        self._environment = environment
        self._diagnostics = diagnostics


class CompositeReporter(Reporter):
    """Fan out events to multiple Reporters in registration order."""

    def __init__(self, *reporters: Reporter) -> None:
        self.reporters = list(reporters)

    def set_environment(
        self, environment: Environment | None, diagnostics: list[Diagnostic]
    ) -> None:
        for r in self.reporters:
            r.set_environment(environment, diagnostics)

    def start(self, plan: list[Benchmark]) -> None:
        for r in self.reporters:
            r.start(plan)

    def benchmark_start(self, b: Benchmark) -> None:
        for r in self.reporters:
            r.benchmark_start(b)

    def execution_done(self, execution: Execution) -> None:
        for r in self.reporters:
            r.execution_done(execution)

    def benchmark_done(self, b: Benchmark, executions: list[Execution]) -> None:
        for r in self.reporters:
            r.benchmark_done(b, executions)

    def finalize(self, report: Report) -> None:
        for r in self.reporters:
            r.finalize(report)


# ---------------------------------------------------------------------------
# CsvReporter
# ---------------------------------------------------------------------------


class CsvReporter(_EnvironmentAware, Reporter):
    """Buffer runs, write CSV on `finalize()`.

    Schema: `suite, benchmark, run, <variant_cols...>, metric, value, unit,
    lower_is_better, outlier, failure`. One row per Sample, for each iteration's samples
    and then the run's whole-process samples. A failed iteration (or run) emits
    one row with blank metric and the failure verdict. All runs appear, warmup
    included.
    """

    def __init__(
        self,
        path: Path,
        *,
        delimiter: str = ",",
        environment: Environment | None = None,
    ) -> None:
        super().__init__()
        self.path = path
        self.delimiter = delimiter
        self._environment = environment

    def finalize(self, report: Report) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        variant_cols = report.variant_keys()
        samples_extra = report.samples_extra_keys()
        cols = (
            ["suite", "benchmark", "run"]
            + variant_cols
            + ["failure", "iteration", "metric", "value", "unit", "lower_is_better"]
            + samples_extra
        )

        with open(self.path, "wt", newline="") as f:
            for line in _environment_comments(self._environment):
                f.write(line)
            w = csv.DictWriter(f, fieldnames=cols, delimiter=self.delimiter)
            w.writeheader()

            for e in report.executions:
                base: dict[str, Any] = {
                    "suite": e.suite,
                    "benchmark": e.benchmark,
                    "run": e.run,
                    "failure": e.failure or "",
                }
                for k in variant_cols:
                    base[k] = e.variant.get(k, "")

                w.writerow(
                    base
                    | {
                        "metric": "runtime",
                        "value": str(e.runtime),
                        "unit": "s",
                    }
                )

                for sample in itertools.chain(
                    e.process_samples, (s for i in e.iterations for s in i.samples)
                ):
                    w.writerow(
                        base
                        | {
                            "iteration": sample.iteration
                            if sample.iteration is not None
                            else "",
                            "metric": sample.metric,
                            "value": sample.value,
                            "unit": sample.unit,
                            "lower_is_better": True
                            if sample.direction == "lower better"
                            else False
                            if sample.direction == "higher better"
                            else "",
                        }
                        | {name: sample.extra.get(name, "") for name in samples_extra}
                    )


# ---------------------------------------------------------------------------
# JsonReporter
# ---------------------------------------------------------------------------


class JsonReporter(_EnvironmentAware, Reporter):
    """Buffer runs in memory, write a single JSON file on finalize().

    `include_output` keeps each run's stdout/stderr/env in the JSON (off by
    default, they bloat the file and are rarely needed offline)."""

    def __init__(
        self,
        path: Path,
        *,
        include_output: bool = False,
        environment: Environment | None = None,
        diagnostics: list[Diagnostic] | None = None,
    ) -> None:
        super().__init__()
        self.path = path
        self.include_output = include_output
        self._environment = environment
        self._diagnostics = diagnostics or []

    def finalize(self, report: Report) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        report.environment = self._environment
        report.diagnostics.extend(self._diagnostics)
        self.path.write_text(report_to_json(report, include_output=self.include_output))


# ---------------------------------------------------------------------------
# DirReporter
# ---------------------------------------------------------------------------


class DirReporter(_EnvironmentAware, Reporter):
    """Per-execution tree at `<out>/<suite>/<bench>/<n>/`.

    Files: stdout, stderr, exitcode, seq (cwd + cmd + info). Directories count
    up per (suite, benchmark) in completion order.
    """

    def __init__(
        self,
        root: Path,
        *,
        environment: Environment | None = None,
        diagnostics: list[Diagnostic] | None = None,
    ) -> None:
        self.root = root
        self._environment = environment
        self._diagnostics = diagnostics or []
        self._counters: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def start(self, plan: list[Benchmark]) -> None:
        self._counters = {}
        self.root.mkdir(parents=True, exist_ok=True)
        if self._environment is not None:
            (self.root / "environment.json").write_text(
                json.dumps(
                    {
                        "environment": unstructure(self._environment),
                        "diagnostics": unstructure(self._diagnostics),
                    },
                    indent=2,
                )
            )

    def execution_done(self, execution: Execution) -> None:
        key = (execution.suite, execution.benchmark)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
            n = self._counters[key]

        exec_dir = self.root / execution.suite / execution.benchmark / str(n)
        exec_dir.mkdir(parents=True, exist_ok=True)

        lines = [
            f"cwd={execution.cwd}",
            f"command={' '.join(execution.command)}",
            f"run={execution.run}",
        ]
        lines.extend(f"variant[{k}]={v}" for k, v in execution.variant)
        if execution.variant_label:
            lines.append(f"variant_label={execution.variant_label}")
        (exec_dir / "seq").write_text("\n".join(lines) + "\n")

        (exec_dir / "stdout").write_text(execution.stdout)
        (exec_dir / "stderr").write_text(execution.stderr)
        (exec_dir / "exitcode").write_text(f"{execution.returncode}\n")


@dataclasses.dataclass
class _TUI:
    class _EtaColumn(TimeRemainingColumn):
        """ETA prefixed with 'ETA', blank when the total is unknown or a single
        iteration, where there is nothing to estimate."""

        def render(self, task: Task) -> Text:
            if task.total is None or task.total <= 1:
                return Text("")
            return Text("ETA ") + super().render(task)

    class _Current:
        current: str

        def __init__(self) -> None:
            self.current = ""

        def __rich__(self):
            return Text(f"[bold]Running:[/bold] {markup_escape(self.current)}")

    overall_progress: RichProgress
    overall_task: TaskID | None
    task_progress: RichProgress
    live: Live

    def __init__(self, console: Console) -> None:
        self.overall_progress = RichProgress(
            TextColumn("[bench.label]Progress[/]"),
            BarColumn(bar_width=None),
            MofNCompleteColumn(),
            TextColumn("({task.fields[failed]} failed)"),
            TimeElapsedColumn(),
            console=console,
        )
        self.overall_task = None
        self.task_progress = RichProgress(
            # TODO: Split on a new line
            TextColumn("[bold]Running:[/bold] {task.fields[benchmark_name]}"),
            SpinnerColumn(),
            BarColumn(bar_width=None),
            TextColumn("{task.completed}/{task.fields[total_str]}"),
            self._EtaColumn(),
            console=console,
        )
        self.live = Live(
            Group(self.overall_progress, self.task_progress),
            console=console,
            transient=True,
            refresh_per_second=12,
        )


class ProgressReporter(Reporter):
    """Live progress on a terminal.

    A top `Progress` bar tracks how many benchmarks finished and how many failed.
    Under it, each running benchmark has a bar with its progress count; command
    benchmarks also show a per-iteration elapsed estimate. Both show an ETA when the
    iteration count is bounded. Bars stretch to the screen edge. When a
    benchmark finishes its bar is replaced by a persistent summary line printed
    above the live region, carrying the same elapsed stats as the final summary
    (or FAILED).

    Each benchmark runs start to finish on one thread, so the bar it owns is held
    on a thread-local.
    """

    class Local(threading.local):
        n: int
        total: int | None
        runtime: float
        task_id: TaskID

        def reset(self, total: int | None, task_id: TaskID) -> None:
            self.n = 0
            self.total = total
            self.runtime = 0.0
            self.task_id = task_id

    _console: Console

    _lock: threading.Lock
    _local: Local

    _passed: int
    _failed: int

    _tui: _TUI | None

    def __init__(self, target_console: Console = console) -> None:
        self._console = target_console

        self._lock = threading.Lock()
        self._local = self.Local()

        self._passed = 0
        self._failed = 0

        if self._console.is_terminal:
            self._tui = _TUI(self._console)
        else:
            self._tui = None

    def start(self, plan: list[Benchmark]) -> None:
        if self._tui is None:
            return

        if len(plan) > 1:
            self._tui.overall_task = self._tui.overall_progress.add_task(
                "", total=len(plan), failed=0
            )

        self._tui.live.start()

    def benchmark_start(self, b: Benchmark) -> None:
        w_max_runs = b.warmup.max_runs()
        max_runs = b.runs.max_runs()

        total = (
            w_max_runs + max_runs
            if w_max_runs is not None and max_runs is not None
            else None
        )

        if self._tui is None:
            self._local.reset(total, TaskID(-1))
            return

        total_str = str(total) if total is not None else "?"
        name = format_benchmark(b.suite, b.name, b.variant, b.variant_label)

        with self._lock:
            task_id = self._tui.task_progress.add_task(
                "",
                benchmark_name=name,
                total=total,
                total_str=total_str,
            )

        self._local.reset(total, task_id)

    def execution_done(self, execution: Execution) -> None:
        self._local.n += 1
        if self._tui is None:
            self._print_plain(execution)
            return

        self._local.runtime += execution.runtime
        self._tui.task_progress.advance(self._local.task_id)

    def benchmark_done(self, b: Benchmark, executions: list[Execution]) -> None:
        if self._tui is None:
            return

        failed = any(e.is_failure() for e in executions)
        name = format_benchmark(b.suite, b.name, b.variant, b.variant_label)

        with self._lock:
            if failed:
                self._failed += 1
            else:
                self._passed += 1

            self._tui.task_progress.remove_task(self._local.task_id)

            if self._tui.overall_task is not None:
                self._tui.overall_progress.update(
                    self._tui.overall_task, failed=self._failed
                )
                self._tui.overall_progress.advance(self._tui.overall_task)

            self._console.print(self._summary_line(name, executions))

    def finalize(self, report: Report) -> None:
        if self._tui is not None:
            self._tui.live.stop()

    @staticmethod
    def _summary_line(name: str, executions: list[Execution]) -> str:
        from bench.report.summary import stat_line, summarize

        stats = summarize(Report(executions=list(executions)))
        elapsed = next((s for s in stats if s.metric == "elapsed"), None)
        head = f"[bench.label]Finished:[/] {markup_escape(name)}"
        if elapsed is None:
            return f"{head}"
        return f"{head}: {stat_line(elapsed)}"

    def _print_plain(self, ex: Execution) -> None:
        total_str = str(self._local.total) if self._local.total is not None else "?"
        if not ex.is_failure():
            tag = "ok"
        else:
            tag = f"FAIL ({ex.failure})"

        id = format_identifier(
            ex.suite, ex.benchmark, ex.variant, ex.run, ex.variant_label
        )

        self._console.print(
            f"[{self._local.n}/{total_str}] {id} {tag}",
            markup=False,
        )


# ---------------------------------------------------------------------------
# SummaryReporter (renders a Formatter, see report/formatter.py)
# ---------------------------------------------------------------------------


class SummaryReporter(Reporter):
    """Buffer runs, format on finalize().

    Takes a single `Formatter` (compose several with `&`). Summarizes the
    buffered runs once and renders them, defaulting to `DefaultSummary`. After
    the formatter output, appends a `Failures:` block listing every failed run.
    """

    def __init__(
        self,
        formatter: Formatter | None = None,
        *,
        target_console: Console | None = None,
    ) -> None:
        from bench.report.formatter import DefaultSummary

        super().__init__()
        self._formatter: Formatter = formatter or DefaultSummary()
        self._console = target_console or console

    def finalize(self, report: Report) -> None:
        from bench.report.summary import summarize

        out = self._formatter(summarize(report))
        if out:
            self._console.print(out)
        if report.failures:
            self._console.print()
            self._console.print("[bench.label]Failures:[/]")
            for execution in report.failures:
                self._console.print("  " + self._failure_line(execution))

    @staticmethod
    def _failure_line(execution: Execution) -> str:
        if execution.returncode == TIMEOUT_RC:
            verdict = f"[bench.failure]timeout (exit {TIMEOUT_RC})[/]"
        elif execution.returncode == SPAWN_FAIL_RC:
            verdict = (
                f"[bench.failure]spawn failed[/]: {execution.failure or 'unknown'}"
            )
        else:
            verdict = f"[bench.failure]exit {execution.returncode}[/]"
        return (
            f"[bench.failure]✗[/] {markup_escape(execution.identifier())}"
            f" - {verdict}: {markup_escape(execution.message) or '(no output)'}"
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
