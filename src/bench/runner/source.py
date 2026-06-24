"""RunSource: produces Observations and assembles Runs for one benchmark-variant.

`CommandSource` spawns one process per observation (pull): each observation is a
finished Run with a single `Observation`. `HarnessSource` spawns one long-running
process and frames its output into many `Observation`s (push), all belonging to a
single `Run`, killable via close(). The `Controller` drives either uniformly: it
pulls `Observation`s (driving the stopping policy) and, when done, collects the
assembled `Run`(s) via `runs()`.
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

from bench.core.execution import (
    SPAWN_FAIL_RC,
    ExecutionResult,
    default_success,
    format_identifier,
)
from bench.core.metric import extract_process, extract_run, partition_metrics
from bench.core.process import LiveProcess, execute, spawn_streaming
from bench.core.sample import Observation, Run, diagnostic_excerpt
from bench.grammar.benchmark import Benchmark
from bench.runner.base import format_benchmark_verbose


class RunSource(abc.ABC):
    """Produces Observations and assembles Runs for one benchmark-variant.

    Two-method surface: pull `(Observation, label)` pairs with `next()` (the
    label is the benchmark-variant display identifier, for live progress only),
    then `close()` to release resources and get the assembled `Run`(s). A
    command yields one `Run` per observation. A harness yields one `Run`
    holding all its observations.
    """

    @abc.abstractmethod
    def next(self) -> tuple[Observation, str]:
        """Next observation and its display label. Raise `StopIteration` when
        exhausted.

        The source owns its own sequencing, callers just pull."""

    @abc.abstractmethod
    def close(self) -> list[Run]:
        """Release resources (kill a running harness) and return the assembled
        `Run`(s)."""


class CommandSource(RunSource):
    """One process per observation. Each observation is its own finished Run."""

    def __init__(self, b: Benchmark, *, verbose: bool = False) -> None:
        self._b = b
        self._verbose = verbose
        self._run = 0
        self._runs: list[Run] = []

    def next(self) -> tuple[Observation, str]:
        self._run += 1
        b = self._b
        if self._verbose and self._run == 1:
            print(format_benchmark_verbose(b, self._run))
        result = execute(b.execution)

        success = b.success if b.success is not None else default_success
        reason = success(result)
        if reason is not None and result.failure is None:
            result = dataclasses.replace(result, failure=reason)

        label = format_identifier(
            b.suite, b.name, b.variant, self._run, b.variant_label
        )
        if result.is_failure():
            obs = Observation(samples=[], failure=result.failure)
            message = diagnostic_excerpt(result.stdout, result.stderr)
        else:
            # For a command, the process is the run.
            # Run and process metrics both fold into this single observation.
            samples = list(extract_run(b.metrics, result)) + list(
                extract_process(b.metrics, result)
            )
            obs = Observation(samples=samples)
            message = ""

        ex = b.execution
        self._runs.append(
            Run(
                suite=b.suite,
                benchmark=b.name,
                variant=b.variant,
                variant_label=b.variant_label,
                run=self._run,
                command=ex.command,
                cwd=str(ex.cwd),
                env=dict(ex.env),
                returncode=result.returncode,
                runtime=result.runtime,
                failure=result.failure,
                message=message,
                stdout=result.stdout,
                stderr=result.stderr,
                observations=[obs],
            )
        )
        return obs, label

    def close(self) -> list[Run]:
        return self._runs


# ---------------------------------------------------------------------------
# HarnessSource: one long-running process, framed into many observations.
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class HarnessHandle:
    """What a monitor needs: the growing output path and liveness.

    A read-only view over the internal LiveProcess. It exposes only what a
    monitor should touch (tail the output, poll liveness), not the
    reaping/kill internals."""

    _live: LiveProcess

    @property
    def output_path(self) -> Path:
        return self._live.stdout_path

    def is_alive(self) -> bool:
        return self._live.is_alive()


# A monitor frames a harness process's output into per-iteration blocks. It may
# raise to fail the run: the exception's message becomes the failure reason.
type HarnessMonitor = Callable[[HarnessHandle], Iterator[str]]


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
                s = line.strip()
                if s:
                    yield s
                return


_DONE = object()

_ZERO_DELIVERY = "no iterations parsed from harness output"


class HarnessSource(RunSource):
    """One process, framed into many observations, killable mid-flight via close()."""

    def __init__(self, b: Benchmark, *, verbose: bool = False) -> None:
        self._b = b
        self._label = format_identifier(b.suite, b.name, b.variant, 1, b.variant_label)
        self._run_metrics, self._process_metrics = partition_metrics(b.metrics)
        self._monitor: HarnessMonitor = b.monitor or line_monitor
        self._q: queue.Queue[Any] = queue.Queue()
        self._taken: list[Observation] = []
        self._proc_result: ExecutionResult | None = None
        self._monitor_failure: str | None = None
        self._run: Run | None = None
        self._closed = threading.Event()
        self._reader: threading.Thread | None = None
        if verbose:
            print(format_benchmark_verbose(b, 1))
        try:
            self._live: LiveProcess | None = spawn_streaming(b.execution)
        except FileNotFoundError as e:
            self._live = None
            self._proc_result = ExecutionResult(
                b.execution, SPAWN_FAIL_RC, failure=str(e)
            )
            self._q.put(_DONE)
            return
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _finalize_process(self) -> None:
        assert self._live is not None
        killed = self._closed.is_set() or self._monitor_failure is not None
        try:
            result = self._live.finish(killed=killed)
        except Exception as e:
            result = ExecutionResult(
                self._b.execution, SPAWN_FAIL_RC, failure=f"harness finish failed: {e}"
            )
        # A harness we killed ourselves on convergence is expected termination,
        # not a failure. Only judge a process that ended on its own. A monitor
        # failure always wins.
        if self._monitor_failure is not None:
            reason = self._monitor_failure
        elif not killed:
            reason = self._b.success(result)
        else:
            reason = None
        if reason is not None and result.failure is None:
            result = dataclasses.replace(result, failure=reason)
        self._proc_result = result

    def _read(self) -> None:
        # _DONE must always reach the queue (even on error) so a consumer's
        # next() raises StopIteration instead of hanging.
        assert self._live is not None
        handle = HarnessHandle(self._live)
        try:
            for block in self._monitor(handle):
                if self._closed.is_set():
                    break
                frame = ExecutionResult(self._b.execution, 0, stdout=block)
                samples = list(extract_run(self._run_metrics, frame))
                # A framed block that parses to nothing is not an iteration.
                if samples:
                    self._q.put(Observation(samples=samples))
        except Exception as e:
            # A monitor that raises fails the run. The process is killed below
            # since nothing is consuming its output anymore.
            self._monitor_failure = f"monitor failed: {type(e).__name__}: {e}"
        finally:
            try:
                self._finalize_process()
            finally:
                self._q.put(_DONE)

    def next(self) -> tuple[Observation, str]:
        item = self._q.get()
        if item is _DONE:
            raise StopIteration
        self._taken.append(item)
        return item, self._label

    def close(self) -> list[Run]:
        self._closed.set()
        if self._live is not None and self._live.is_alive():
            self._live.kill()
        if self._run is None:
            self._run = self._assemble()
        return [self._run]

    def _assemble(self) -> Run:
        if self._reader is not None:
            self._reader.join(timeout=5)
        result = self._proc_result
        assert result is not None

        observations = list(self._taken)
        # Whole-process metrics become a trailing observation on the run.
        if not result.is_failure():
            proc_samples = list(extract_process(self._process_metrics, result))
            if proc_samples:
                observations.append(Observation(samples=proc_samples))

        run_failure = result.failure  # process verdict or monitor failure
        # Clean-but-empty delivery: nothing parsed from the harness output.
        if run_failure is None and not any(not o.is_failure() for o in observations):
            run_failure = _ZERO_DELIVERY

        message = (
            diagnostic_excerpt(result.stdout, result.stderr) if run_failure else ""
        )
        ex = self._b.execution
        b = self._b
        return Run(
            suite=b.suite,
            benchmark=b.name,
            variant=b.variant,
            variant_label=b.variant_label,
            run=1,
            command=ex.command,
            cwd=str(ex.cwd),
            env=dict(ex.env),
            returncode=result.returncode,
            runtime=result.runtime,
            failure=run_failure,
            message=message,
            stdout=result.stdout,
            stderr=result.stderr,
            observations=observations,
        )


def make_source(b: Benchmark, *, verbose: bool = False) -> RunSource:
    if b.harness:
        return HarnessSource(b, verbose=verbose)
    return CommandSource(b, verbose=verbose)
