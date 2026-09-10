from __future__ import annotations

import dataclasses
import threading

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape as markup_escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.progress import (
    Progress as RichProgress,
)
from rich.text import Text

from bench.console.theme import console
from bench.model.benchmark import (
    Benchmark,
    format_benchmark,
    format_identifier,
)
from bench.model.results import Execution, Report
from bench.report.base import Reporter


@dataclasses.dataclass(slots=True)
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
    """Live progress on a terminal, plain `[n/total] <run> ok` lines elsewhere.

    An overall bar over the benchmarks, one bar per running benchmark, and a
    persistent `Finished:` line with its elapsed stats once it is done.

    Each benchmark runs start to finish on one thread, so the bar it owns is held
    on a thread-local.
    """

    class Local(threading.local):
        n: int
        total: int | None
        runtime: float
        task_id: TaskID

        def __init__(self) -> None:
            super().__init__()
            self.n = 0
            self.total = None
            self.runtime = 0.0
            self.task_id = TaskID(-1)

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

            timestr = self._console.get_datetime().strftime("%X")
            self._console.print(
                f"[bench.success][{timestr}][/]", self._summary_line(name, executions)
            )

    def finalize(self, report: Report) -> None:
        if self._tui is not None:
            self._tui.live.stop()

    @staticmethod
    def _summary_line(name: str, executions: list[Execution]) -> str:
        from bench.core.stats import summarize
        from bench.summary import stat_line

        stats = summarize(Report(executions=list(executions)))
        elapsed = [s for s in stats if s.metric_key.metric == "elapsed"]
        head = f"[bench.label]Finished:[/] {markup_escape(name)}"
        if not elapsed:
            return f"{head}"
        return f"{head}: {stat_line(stats, elapsed[0])}"

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
