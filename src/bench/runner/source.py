"""ExecutionSource: produces Iterations and assembles Executions for one benchmark-variant.

`CommandSource` spawns one process per iteration (pull): each iteration is a
finished Execution with a single `Iteration`. `HarnessSource` spawns one long-running
process and frames its output into many `Iteration`s (push), all belonging to a
single `Execution`, killable via close(). The `Controller` drives either uniformly: it
pulls `Iteration`s (driving the stopping policy) and, when done, collects the
assembled `Execution`(s) via `close()`.
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

from bench.core.invocation import (
    SPAWN_FAIL_RC,
    InvocationResult,
    Verdict,
    default_success,
    format_identifier,
)
from bench.core.process import LiveProcess, execute, spawn_streaming
from bench.core.results import Iteration, Execution, Sample, diagnostic_excerpt
from bench.builder.benchmark import Benchmark
from bench.runner.base import format_benchmark_verbose


def _with_elapsed(samples: list[Sample], result: InvocationResult) -> list[Sample]:
    """Wall-clock elapsed is intrinsic to every run (`result.runtime`), so record
    it once, prepended, unless a metric already produced an `elapsed` sample."""
    if result.runtime is None or any(s.metric == "elapsed" for s in samples):
        return samples
    return [Sample("elapsed", result.runtime, unit="s", lower_is_better=True), *samples]


def _apply_verdict(result: InvocationResult, reason: Verdict) -> InvocationResult:
    """Record `reason` as the failure on `result`, unless it already failed."""
    if reason is not None and result.failure is None:
        return dataclasses.replace(result, failure=reason)
    return result


def _make_execution(
    b: Benchmark,
    result: InvocationResult,
    *,
    run: int,
    iterations: list[Iteration],
    process_samples: list[Sample],
    failure: str | None,
    message: str,
) -> Execution:
    """Assemble an Execution from a resolved benchmark and its process result."""
    ex = b.invocation
    return Execution(
        suite=b.suite,
        benchmark=b.name,
        variant=b.variant,
        variant_label=b.variant_label,
        run=run,
        command=ex.command,
        cwd=str(ex.cwd),
        env=dict(ex.env),
        returncode=result.returncode,
        runtime=result.runtime,
        failure=failure,
        message=message,
        stdout=result.stdout,
        stderr=result.stderr,
        iterations=iterations,
        process_samples=process_samples,
    )


class ExecutionSource(abc.ABC):
    """Produces Iterations and assembles Executions for one benchmark-variant.

    Two-method surface: pull `(Iteration, label)` pairs with `next()` (the
    label is the benchmark-variant display identifier, for live progress only),
    then `close()` to release resources and get the assembled `Execution`(s). A
    command yields one `Execution` per iteration. A harness yields one `Execution`
    holding all its iterations.
    """

    @abc.abstractmethod
    def next(self) -> tuple[Iteration, str]:
        """Next iteration and its display label. Raise `StopIteration` when
        exhausted.

        The source owns its own sequencing, callers just pull."""

    @abc.abstractmethod
    def close(self) -> list[Execution]:
        """Release resources (kill a running harness) and return the assembled
        `Execution`(s)."""


class CommandSource(ExecutionSource):
    """One process per iteration. Each iteration is its own finished Execution."""

    def __init__(self, b: Benchmark, *, verbose: bool = False) -> None:
        self._b = b
        self._verbose = verbose
        self._run = 0
        self._executions: list[Execution] = []

    def next(self) -> tuple[Iteration, str]:
        self._run += 1
        b = self._b
        if self._verbose and self._run == 1:
            print(format_benchmark_verbose(b, self._run))
        result = execute(b.invocation)

        result = _apply_verdict(result, b.success(result))

        label = format_identifier(
            b.suite, b.name, b.variant, self._run, b.variant_label
        )
        runtime = result.runtime or 0.0
        if result.is_failure():
            it = Iteration(samples=[], failure=result.failure, runtime=runtime)
            process_samples = []
            message = diagnostic_excerpt(result.stdout, result.stderr)
        else:
            # A command is one iteration: its iteration metrics read the chosen
            # source text, while its process metrics read the whole result.
            it_samples = [
                s
                for m, source in b.iteration_metrics
                for s in m.process(source(result))
            ]
            process_samples = _with_elapsed(
                [s for m in b.process_metrics for s in m.process(result)], result
            )
            it = Iteration(samples=it_samples, runtime=runtime)
            message = ""

        self._executions.append(
            _make_execution(
                b,
                result,
                run=self._run,
                iterations=[it],
                process_samples=process_samples,
                failure=result.failure,
                message=message,
            )
        )
        return it, label

    def close(self) -> list[Execution]:
        return self._executions


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


class HarnessSource(ExecutionSource):
    """One process, framed into many iterations, killable mid-flight via close()."""

    def __init__(self, b: Benchmark, *, verbose: bool = False) -> None:
        self._b = b
        self._label = format_identifier(b.suite, b.name, b.variant, 1, b.variant_label)
        self._iteration_metrics = b.iteration_metrics
        self._process_metrics = b.process_metrics
        self._monitor: HarnessMonitor = b.monitor or line_monitor
        self._kill_on_convergence = b.kill_on_convergence
        self._q: queue.Queue[Any] = queue.Queue()
        self._taken: list[Iteration] = []
        self._proc_result: InvocationResult | None = None
        self._monitor_failure: str | None = None
        self._execution: Execution | None = None
        self._closed = threading.Event()
        self._reader: threading.Thread | None = None
        if verbose:
            print(format_benchmark_verbose(b, 1))
        try:
            self._live: LiveProcess | None = spawn_streaming(b.invocation)
        except FileNotFoundError as e:
            self._live = None
            self._proc_result = InvocationResult(
                b.invocation, SPAWN_FAIL_RC, failure=str(e)
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
            result = InvocationResult(
                self._b.invocation, SPAWN_FAIL_RC, failure=f"harness finish failed: {e}"
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
        self._proc_result = _apply_verdict(result, reason)

    def _read(self) -> None:
        # _DONE must always reach the queue (even on error) so a consumer's
        # next() raises StopIteration instead of hanging.
        assert self._live is not None
        handle = HarnessHandle(self._live)
        # No per-iteration runtime is measured for a continuous harness, so each
        # frame carries its wall-delta since the previous one. Summed, that is
        # the wall-clock the harness has been running (approximately its command
        # runtime).
        last = time.monotonic()
        try:
            for block in self._monitor(handle):
                if self._closed.is_set():
                    break
                # The monitor frame is the iteration text. Its source is fixed
                # (the streamed output), so each metric's source is ignored here.
                samples = [
                    s
                    for m, _source in self._iteration_metrics
                    for s in m.process(block)
                ]
                # A framed block that parses to nothing is not an iteration.
                if samples:
                    now = time.monotonic()
                    self._q.put(Iteration(samples=samples, runtime=now - last))
                    last = now
        except Exception as e:
            # A monitor that raises fails the run. The process is killed below
            # since nothing is consuming its output anymore.
            self._monitor_failure = f"monitor failed: {type(e).__name__}: {e}"
        finally:
            try:
                self._finalize_process()
            finally:
                self._q.put(_DONE)

    def next(self) -> tuple[Iteration, str]:
        item = self._q.get()
        if item is _DONE:
            raise StopIteration
        self._taken.append(item)
        return item, self._label

    def close(self) -> list[Execution]:
        # kill_on_convergence=False: the harness produces a fixed number of
        # iterations and exits on its own, so leave it to finish (the reader ends
        # on natural EOF, .with_timeout() is the backstop) and report its real
        # exit code instead of killing it.
        if self._kill_on_convergence:
            self._closed.set()
            if self._live is not None and self._live.is_alive():
                self._live.kill()
        if self._execution is None:
            self._execution = self._assemble()
        return [self._execution]

    def _assemble(self) -> Execution:
        if self._reader is not None:
            # When not killing, block until the harness exits on its own; when
            # killing, it dies promptly, so 5s is only a safety margin.
            self._reader.join(timeout=None if not self._kill_on_convergence else 5)
        result = self._proc_result
        assert result is not None

        iterations = list(self._taken)
        process_samples = (
            _with_elapsed(
                [s for m in self._process_metrics for s in m.process(result)], result
            )
            if not result.is_failure()
            else []
        )

        run_failure = result.failure  # process verdict or monitor failure
        # Per-iteration metrics were expected but none parsed: nothing delivered.
        if run_failure is None and self._iteration_metrics and not iterations:
            run_failure = _ZERO_DELIVERY

        message = (
            diagnostic_excerpt(result.stdout, result.stderr) if run_failure else ""
        )
        return _make_execution(
            self._b,
            result,
            run=1,
            iterations=iterations,
            process_samples=process_samples,
            failure=run_failure,
            message=message,
        )


def make_source(b: Benchmark, *, verbose: bool = False) -> ExecutionSource:
    if b.harness:
        return HarnessSource(b, verbose=verbose)
    return CommandSource(b, verbose=verbose)
