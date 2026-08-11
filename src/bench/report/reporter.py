"""Streaming reporter sinks."""

from __future__ import annotations

import abc
import csv
import json
import threading
from itertools import chain
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cattrs import unstructure

from rich.cells import cell_len
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape as markup_escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress as RichProgress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Column
from rich.text import Text

from bench.builder.benchmark import Benchmark
from bench.core.environment import Diagnostic, Environment
from bench.core.invocation import (
    SPAWN_FAIL_RC,
    TIMEOUT_RC,
    Variant,
    format_benchmark,
    format_variant_pairs,
)
from bench.core.results import Iteration, Report, Execution, Sample, report_to_json
from bench.report.theme import BENCHR_THEME, console

if TYPE_CHECKING:
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

    def iteration(self, it: Iteration, label: str) -> None:
        pass

    def execution_done(self, execution: Execution) -> None:
        pass

    def benchmark_done(self, b: Benchmark, executions: list[Execution]) -> None:
        pass

    def finalize(self) -> None:
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


class _BufferingReporter(Reporter):
    """Base for reporters that accumulate a Report and render it at
    `finalize()`. Gives subclasses a thread-safe `execution_done`."""

    def __init__(self) -> None:
        self._report = Report()
        self._lock = threading.Lock()

    def execution_done(self, execution: Execution) -> None:
        with self._lock:
            self._report.add(execution)


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

    def iteration(self, it: Iteration, label: str) -> None:
        for r in self.reporters:
            r.iteration(it, label)

    def execution_done(self, execution: Execution) -> None:
        for r in self.reporters:
            r.execution_done(execution)

    def benchmark_done(self, b: Benchmark, executions: list[Execution]) -> None:
        for r in self.reporters:
            r.benchmark_done(b, executions)

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


class CsvReporter(_EnvironmentAware, _BufferingReporter):
    """Buffer runs, write CSV on `finalize()`.

    Schema: `suite, benchmark, run, <variant_cols...>, iteration, warmup, metric,
    value, unit, lower_is_better, outlier, failure`. One row per Sample, for each
    iteration's samples and then the run's whole-process samples, which belong to
    no iteration and leave `iteration`/`warmup` blank. A failed iteration (or run)
    emits one row with blank metric and the failure verdict.

    All runs appear, warmup included: `warmup` says which, so a consumer can
    reproduce the stats (which exclude them) rather than having to guess from
    row order.
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
        self._diagnostics = []

    def finalize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        variant_cols = self._report.variant_keys()
        cols = (
            ["suite", "benchmark", "run"]
            + variant_cols
            + ["iteration", "warmup"]
            + ["metric", "value", "unit", "lower_is_better", "outlier", "failure"]
        )
        with open(self.path, "wt", newline="") as f:
            for line in _environment_comments(self._environment):
                f.write(line)
            w = csv.DictWriter(f, fieldnames=cols, delimiter=self.delimiter)
            w.writeheader()
            for r in self._report.executions:
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
                for i, it in enumerate(iters):
                    it_base = {**base, "iteration": i, "warmup": str(it.warmup)}
                    failure = it.failure or (r.failure if not it.samples else None)
                    if failure:
                        w.writerow(_blank_row(it_base, failure))
                        emitted = True
                        continue
                    for s in it.samples:
                        w.writerow(_sample_row(it_base, s))
                        emitted = True
                # Whole-process samples belong to the run, not to any iteration.
                proc_base = {**base, "iteration": "", "warmup": ""}
                for s in r.process_samples:
                    w.writerow(_sample_row(proc_base, s))
                    emitted = True
                # A run that produced nothing (no samples, no failure) still appears.
                if not emitted:
                    w.writerow(_blank_row(proc_base, ""))


# ---------------------------------------------------------------------------
# JsonReporter
# ---------------------------------------------------------------------------


class JsonReporter(_EnvironmentAware, _BufferingReporter):
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

    def finalize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._report.environment = self._environment
        self._report.diagnostics = self._diagnostics
        self.path.write_text(
            report_to_json(self._report, include_output=self.include_output)
        )


# ---------------------------------------------------------------------------
# DirReporter
# ---------------------------------------------------------------------------


def execution_dir(
    root: Path, suite: str, benchmark: str, leaf: str | int | Path
) -> Path:
    """The per-execution directory `<root>/<suite>/<benchmark>/<leaf>`.

    The single source of truth for the `--dir` layout, so anything that wants to
    drop extra artifacts next to a run's `stdout`/`stderr` (e.g. a `perf.data`)
    joins the same path. `leaf` is the matrix variant sub-path (see
    `variant_path`) when the benchmark has variants, else the 1-based completion
    ordinal `DirReporter` assigns per `(suite, benchmark)`.
    """
    return root / suite / benchmark / (leaf if isinstance(leaf, Path) else str(leaf))


def variant_path(variant: Variant, *, nested: bool = True) -> Path:
    """The sub-directory a matrix `variant` maps to under its benchmark.

    Nested (the default): one directory level per dimension,
    `dim1/val1/dim2/val2`. Flat: a single `dim1=val1, dim2=val2` component (the
    variant label). An empty variant maps to an empty path.
    """
    if not variant:
        return Path()
    if nested:
        return Path(*chain.from_iterable(variant))
    return Path(format_variant_pairs(variant))


class DirReporter(_EnvironmentAware, Reporter):
    """Per-execution tree at `<out>/<suite>/<bench>/<leaf>/` (see `execution_dir`).

    Files: stdout, stderr, exitcode, seq (cwd + cmd + info). For a matrix variant
    `leaf` is the variant sub-path - nested `dim/val/...` by default, or a flat
    `dim=val, ...` component when constructed with `nested=False`; a plain
    benchmark's runs use a per (suite, benchmark) counter in completion order.
    """

    def __init__(
        self,
        root: Path,
        *,
        nested: bool = True,
        environment: Environment | None = None,
        diagnostics: list[Diagnostic] | None = None,
    ) -> None:
        self.root = root
        self.nested = nested
        self._environment = environment
        self._diagnostics = diagnostics or []
        self._counters: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def set_environment(
        self, environment: Environment | None, diagnostics: list[Diagnostic]
    ) -> None:
        self._environment = environment
        self._diagnostics = diagnostics

    def output_dir(self, suite: str, benchmark: str, variant: Variant = ()) -> Path:
        """Where this reporter writes a given execution's files."""
        return execution_dir(
            self.root, suite, benchmark, variant_path(variant, nested=self.nested)
        )

    def start(self, plan: list[Benchmark]) -> None:
        self._counters = {}
        self.root.mkdir(parents=True, exist_ok=True)
        # Pre-create the per-variant directories so a wrapped command (e.g. `perf
        # record -o <dir>/perf.data`) has somewhere to write before it runs. Only
        # variant benchmarks get a deterministic path up front; plain runs are
        # numbered lazily, in completion order, by execution_done.
        for b in plan:
            if b.variant:
                self.output_dir(b.suite, b.name, b.variant).mkdir(
                    parents=True, exist_ok=True
                )
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
        # A matrix variant gets a stable directory from its variant (so a wrapped
        # command's -o path can target the same place); a plain benchmark's runs
        # are numbered per (suite, benchmark) in completion order.
        if execution.variant:
            exec_dir = self.output_dir(
                execution.suite, execution.benchmark, execution.variant
            )
        else:
            key = (execution.suite, execution.benchmark)
            with self._lock:
                self._counters[key] = self._counters.get(key, 0) + 1
                n = self._counters[key]
            exec_dir = execution_dir(self.root, execution.suite, execution.benchmark, n)
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


DEFAULT_NAME_WIDTH = 36


def _truncate_middle(s: str, width: int) -> str:
    """Cap `s` to `width` columns, eliding the middle with `…` (keeps head+tail),
    macOS-Finder style."""
    if width <= 0:
        return ""
    if len(s) <= width:
        return s
    keep = width - 1  # room for the ellipsis
    if keep <= 0:
        return "…"
    left = (keep + 1) // 2
    right = keep // 2
    return s[:left] + "…" + s[len(s) - right :]  # len(s)-right handles right==0


def _bench_total(b: Benchmark) -> int | None:
    """Iterations a benchmark should produce (warmup + runs), or None if either
    policy is unbounded."""
    w, m = b.warmup.max_runs(), b.runs.max_runs()
    if w is None or m is None:
        return None
    return w + m


class _EtaColumn(TimeRemainingColumn):
    """ETA prefixed with 'ETA', blank when the total is unknown or a single
    iteration, where there is nothing to estimate."""

    def render(self, task: Task) -> Text:
        if task.total is None or task.total <= 1:
            return Text("")
        return Text("ETA ") + super().render(task)


class _NameColumn(ProgressColumn):
    """The benchmark name (already middle-truncated), rendered as a literal (no
    markup parsing) so names containing brackets stay intact. Sits in a
    fixed-width column so it isn't squeezed when the row overflows — the flexible
    bar shrinks instead."""

    def __init__(self, name: str) -> None:
        super().__init__(table_column=Column(no_wrap=True, width=cell_len(name)))
        self._name = name

    def render(self, task: Task) -> Text:
        return Text(self._name)


class ProgressReporter(Reporter):
    """Live progress on a terminal.

    A top `Progress` bar tracks how many benchmarks finished and how many failed.
    Under it, each running benchmark occupies a single line led by its name
    (middle-truncated to `name_width`), followed by a spinner, its progress count
    and an ETA when the iteration count is bounded. Bars stretch to the screen
    edge. When a benchmark finishes its bar is replaced by a persistent summary
    line printed above the live region, carrying the same elapsed stats as the
    final summary (or FAILED).

    Each benchmark runs start to finish on one thread, so the bar it owns is held
    on a thread-local.
    """

    def __init__(
        self,
        target_console: Console | None = None,
        name_width: int = DEFAULT_NAME_WIDTH,
    ) -> None:
        self._console = target_console or console
        self._is_tty = self._console.is_terminal
        self._name_width = name_width
        self._lock = threading.Lock()
        self._local = threading.local()
        self._passed = 0
        self._failed = 0
        self._overall_task: TaskID | None = None
        self._active: dict[int, RichProgress] = {}
        self._next_slot = 0
        if self._is_tty:
            self._overall: RichProgress | None = RichProgress(
                TextColumn("[bench.label]Progress[/]"),
                BarColumn(bar_width=None),
                MofNCompleteColumn(),
                TextColumn("({task.fields[failed]} failed)"),
                TimeElapsedColumn(),
                console=self._console,
            )
            self._live: Live | None = Live(
                Group(),
                console=self._console,
                transient=True,
                refresh_per_second=12,
            )
        else:
            self._overall = None
            self._live = None

    def start(self, plan: list[Benchmark]) -> None:
        if self._live is None:
            return
        if len(plan) > 1 and self._overall is not None:
            self._overall_task = self._overall.add_task("", total=len(plan), failed=0)
        self._live.update(self._group())
        self._live.start()

    def benchmark_start(self, b: Benchmark) -> None:
        self._local.n = 0
        self._local.total = _bench_total(b)
        if self._live is None:
            return
        total = self._local.total
        total_str = str(total) if total is not None else "?"
        name = format_benchmark(b.suite, b.name, b.variant, b.variant_label)
        columns: list[Any] = [
            _NameColumn(_truncate_middle(name, self._name_width)),
            SpinnerColumn(),
            BarColumn(bar_width=None),
            TextColumn("{task.completed}/{task.fields[total_str]}"),
            _EtaColumn(),
        ]
        prog = RichProgress(*columns, console=self._console)
        task_id = prog.add_task("", total=total, total_str=total_str)
        with self._lock:
            slot = self._next_slot
            self._next_slot += 1
            self._active[slot] = prog
            self._live.update(self._group())
        self._local.slot = slot
        self._local.prog = prog
        self._local.task_id = task_id

    def iteration(self, it: Iteration, label: str) -> None:
        self._local.n = getattr(self._local, "n", 0) + 1
        if self._live is None:
            self._print_plain(
                it, label, self._local.n, getattr(self._local, "total", None)
            )
            return
        prog = getattr(self._local, "prog", None)
        task_id = getattr(self._local, "task_id", None)
        if prog is None or task_id is None:
            return
        prog.advance(task_id)

    def benchmark_done(self, b: Benchmark, executions: list[Execution]) -> None:
        if self._live is None:
            return
        failed = any(e.is_failure() for e in executions)
        name = format_benchmark(b.suite, b.name, b.variant, b.variant_label)
        with self._lock:
            if failed:
                self._failed += 1
            else:
                self._passed += 1
            if self._overall is not None and self._overall_task is not None:
                self._overall.update(self._overall_task, failed=self._failed)
                self._overall.advance(self._overall_task)
            self._console.print(self._summary_line(b, name, executions))
            self._active.pop(getattr(self._local, "slot", -1), None)
            self._live.update(self._group())
        self._local.slot = None
        self._local.prog = None
        self._local.task_id = None

    def finalize(self) -> None:
        if self._live is not None:
            self._live.stop()
            if self._passed or self._failed:
                self._console.print()

    def _group(self) -> Group:
        parts: list[Any] = []
        if self._overall_task is not None and self._overall is not None:
            parts.append(self._overall)
        parts.extend(self._active.values())
        return Group(*parts)

    @staticmethod
    def _summary_line(b: Benchmark, name: str, executions: list[Execution]) -> str:
        from bench.report.summary import scale_unit, stat_line, summarize

        stats = summarize(Report(executions=list(executions)))
        elapsed = next((s for s in stats if s.metric == "elapsed"), None)
        head = f"[bench.label]Finished:[/] {markup_escape(name)}"
        if elapsed is None:
            return f"{head}: [bench.failure]FAILED[/]"
        if b.harness:
            # One streaming process: `elapsed` is its whole wall-clock, a single
            # measurement, so the per-iteration run/warmup counts do not apply.
            scale, unit = scale_unit(elapsed.mean, elapsed.unit)
            value = f"[bench.value]{elapsed.mean * scale:.2f}[/]"
            label = markup_escape(f"{elapsed.metric} [{unit}] (harness)")
            return f"{head}: {value} {label}"
        return f"{head}: {stat_line(elapsed)}"

    def _print_plain(
        self, it: Iteration, label: str, n: int, total: int | None
    ) -> None:
        total_str = str(total) if total is not None else "?"
        if not it.is_failure():
            tag = "[bench.success]ok[/]"
        else:
            tag = f"[bench.failure]FAIL[/] ({it.failure})"
        self._console.print(f"[{n}|{total_str}] {markup_escape(label)} {tag}")


# ---------------------------------------------------------------------------
# SummaryReporter (renders a Formatter, see report/formatter.py)
# ---------------------------------------------------------------------------


class SummaryReporter(_BufferingReporter):
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

    def finalize(self) -> None:
        from bench.report.summary import summarize

        out = self._formatter(summarize(self._report))
        if out:
            self._console.print(out)
        if self._report.failures:
            self._console.print()
            self._console.print("[bench.label]Failures:[/]")
            for execution in self._report.failures:
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
    "execution_dir",
    "variant_path",
    "ProgressReporter",
    "SummaryReporter",
]
