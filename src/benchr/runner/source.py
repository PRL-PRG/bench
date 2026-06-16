"""RunSource: produces RunResults for one benchmark-variant.

CommandSource spawns one process per run (pull). HarnessSource spawns one
long-running process and frames its output into many runs (push), killable via
close(). The Controller drives either uniformly.
"""

from __future__ import annotations

import abc
import dataclasses
import queue
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from benchr.core.execution import (
    SPAWN_FAIL_RC,
    ExecutionResult,
    ScheduledExecution,
    default_success,
)
from benchr.core.metric import extract_process, extract_run, partition_metrics
from benchr.core.process import LiveProcess, spawn_streaming
from benchr.core.sample import RunResult, Sample, diagnostic_excerpt
from benchr.runner.base import PlannedBenchmark


class RunSource(abc.ABC):
    """Produces identity-free RunResults for one benchmark-variant."""

    @abc.abstractmethod
    def next(self, run: int) -> RunResult:
        """Next run's result. Raise StopIteration when exhausted."""

    def drain_process_events(self) -> list[tuple[ScheduledExecution, ExecutionResult]]:
        """OS-process completions since the last call (drive process_done)."""
        return []

    def metadata(self) -> list[Sample]:
        """Whole-process samples for Report.metadata (harness only)."""
        return []

    def process_result(self) -> ExecutionResult | None:
        """The whole-process ExecutionResult, once known (harness only).

        Lets the Controller tell a *process failure* from clean-but-short
        delivery when the source is exhausted before the policy converges.
        """
        return None

    def close(self) -> None:
        """Release resources; HarnessSource kills the process."""


class CommandSource(RunSource):
    """One process per run. All metrics fold into the run's samples."""

    def __init__(self, planned: PlannedBenchmark, params: Any) -> None:
        self._planned = planned
        self._params = params
        self._b = planned.benchmark
        self._events: list[tuple[ScheduledExecution, ExecutionResult]] = []

    def next(self, run: int) -> RunResult:
        from benchr.core.process import execute

        sched = self._b.schedule(self._params, suite=self._planned.suite, run=run)
        result = execute(sched.execution)
        # Apply the success policy (stamp the failure reason).
        success = self._b.success if self._b.success is not None else default_success
        reason = success(result)
        if reason is not None and result.failure is None:
            result = dataclasses.replace(result, failure=reason)
        self._events.append((sched, result))
        if result.is_failure():
            return RunResult(
                samples=[],
                returncode=result.returncode,
                runtime=result.runtime,
                failure=result.failure,
                message=diagnostic_excerpt(result),
            )
        # For a command, the process IS the run: both run and process metrics
        # fold into this run's samples.
        samples = list(extract_run(self._b.metrics, result)) + list(
            extract_process(self._b.metrics, result)
        )
        return RunResult(
            samples=samples, returncode=result.returncode, runtime=result.runtime
        )

    def drain_process_events(self) -> list[tuple[ScheduledExecution, ExecutionResult]]:
        ev, self._events = self._events, []
        return ev

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# HarnessSource: one long-running process, framed into many runs.
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class HarnessHandle:
    """What a monitor needs: pid, the growing output path, and liveness."""

    pid: int
    output_path: Path
    _live: LiveProcess

    def is_alive(self) -> bool:
        return self._live.is_alive()


type BenchmarkMonitor = Callable[[HarnessHandle], Iterator[str]]


def line_monitor(handle: HarnessHandle) -> Iterator[str]:
    """Default monitor: one non-empty line of output = one iteration."""
    with open(handle.output_path) as f:
        while True:
            line = f.readline()
            if line.endswith("\n"):
                s = line.strip()
                if s:
                    yield s
            elif handle.is_alive():
                time.sleep(0.02)
            else:
                s = line.strip()      # final newline-less remainder
                if s:
                    yield s
                return


_DONE = object()


class HarnessSource(RunSource):
    """One process, framed into many runs, killable mid-flight via close()."""

    def __init__(self, planned: PlannedBenchmark, params: Any) -> None:
        self._b = planned.benchmark
        self._sched = self._b.schedule(params, suite=planned.suite, run=1)
        self._run_metrics, self._process_metrics = partition_metrics(self._b.metrics)
        self._monitor: BenchmarkMonitor = getattr(self._b, "monitor", None) or line_monitor
        self._q: queue.Queue[Any] = queue.Queue()
        self._proc_result: ExecutionResult | None = None
        self._events: list[tuple[ScheduledExecution, ExecutionResult]] = []
        self._closed = threading.Event()
        self._reader: threading.Thread | None = None
        try:
            self._live: LiveProcess | None = spawn_streaming(self._sched.execution)
        except FileNotFoundError as e:
            self._live = None
            self._proc_result = ExecutionResult(
                self._sched.execution, SPAWN_FAIL_RC, failure=str(e)
            )
            self._events.append((self._sched, self._proc_result))
            self._q.put(_DONE)
            return
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self._live is not None
        handle = HarnessHandle(self._live.proc.pid, self._live.stdout_path, self._live)
        try:
            for block in self._monitor(handle):
                if self._closed.is_set():
                    break
                result = ExecutionResult(self._sched.execution, 0, stdout=block)
                samples = list(extract_run(self._run_metrics, result))
                if samples:
                    self._q.put(RunResult(samples=samples))
        finally:
            try:
                killed = self._closed.is_set()
                result = self._live.finish(killed=killed)
                # A harness we killed ourselves on convergence is expected
                # termination, not a failure — only judge a process that ended
                # on its own (crash / timeout / clean exhaustion).
                if not killed:
                    reason = self._b.success(result)
                    if reason is not None and result.failure is None:
                        result = dataclasses.replace(result, failure=reason)
                self._proc_result = result
                self._events.append((self._sched, self._proc_result))
            finally:
                self._q.put(_DONE)

    def next(self, run: int) -> RunResult:
        item = self._q.get()
        if item is _DONE:
            raise StopIteration
        return item

    def drain_process_events(self) -> list[tuple[ScheduledExecution, ExecutionResult]]:
        ev, self._events = self._events, []
        return ev

    def metadata(self) -> list[Sample]:
        if self._proc_result is None or self._proc_result.is_failure():
            return []
        return list(extract_process(self._process_metrics, self._proc_result))

    def process_result(self) -> ExecutionResult | None:
        return self._proc_result

    def close(self) -> None:
        self._closed.set()
        if self._live is not None and self._live.is_alive():
            self._live.kill()
        if self._reader is not None:
            self._reader.join(timeout=5)


def make_source(planned: PlannedBenchmark, params: Any) -> RunSource:
    if planned.benchmark.harness:
        return HarnessSource(planned, params)
    return CommandSource(planned, params)
