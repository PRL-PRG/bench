"""Runner protocol and shared coroutine pump.

Two abstractions are split intentionally:

  ``execute(sched)``  spawn one subprocess, return an ExecutionResult.
                      Pure mechanism — no policy, no reporting.
  ``Runner``          orchestrate compile() coroutines for a list of suites.
                      The coroutine yields ScheduledExecution; Runner calls
                      ``execute`` to get an ExecutionResult; the benchmark's
                      Processor turns that into Samples; the Runner stamps
                      them, forwards to the Reporter, and sends them back
                      into the coroutine via ``.send()`` so the StoppingPolicy
                      can observe. ``run()`` returns the accumulated ``Report``
                      (every Sample plus a RunRecord per execution).

The default Runner has a configurable global cap (``max_runs_per_phase``) as a
safety net against custom policies that never converge.
"""

from __future__ import annotations

import abc
import dataclasses
import os
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

from benchr.grammar.benchmark import Benchmark
from benchr.grammar.execution import (
    Execution,
    ExecutionResult,
    ScheduledExecution,
    Verdict,
    format_variant,
)
from benchr.grammar.policy import StoppingPolicy
from benchr.grammar.processor import process_all, stamp
from benchr.grammar.suite import Suite
from benchr.report.sample import Report, Sample


# Reporter is a forward reference to avoid a circular import; we describe its
# shape with a Protocol so reporter.py can register without inheriting.


class ReporterLike(Protocol):
    def start(self, plan: list[Benchmark]) -> None: ...
    def sample(self, sched: ScheduledExecution, result: ExecutionResult, samples: list[Sample]) -> None: ...
    def finalize(self) -> None: ...


class _NoopReporter:
    def start(self, plan: list[Benchmark]) -> None: pass
    def sample(self, sched: ScheduledExecution, result: ExecutionResult,
               samples: list[Sample]) -> None: pass
    def finalize(self) -> None: pass


# ---------------------------------------------------------------------------
# Subprocess execution — single Execution -> ExecutionResult.
# ---------------------------------------------------------------------------


def execute(exe: Execution) -> ExecutionResult:
    """Spawn one subprocess and return a ExecutionResult.

    Honors ``exe.timeout`` (returncode 124 on timeout), captures stdout/stderr,
    and includes ``rusage`` via ``os.wait4``.
    """
    cmd = list(exe.command)
    found = shutil.which(cmd[0])
    if found is None:
        return ExecutionResult(execution=exe, returncode=-1,
                               failure=f"Command not found: {cmd[0]}")
    # Resolve to absolute against the invoker's cwd so that ``Popen(cwd=…)``
    # doesn't re-resolve a relative executable against the subprocess's cwd.
    cmd[0] = os.path.abspath(found)

    stdout_f = tempfile.TemporaryFile()
    stderr_f = tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(exe.cwd),
            env=dict(exe.env) if exe.env else None,
            stdin=subprocess.PIPE if exe.stdin else None,
            stdout=stdout_f,
            stderr=stderr_f,
            shell=False,
        )
        if exe.stdin and proc.stdin is not None:
            try:
                proc.stdin.write(exe.stdin)
                proc.stdin.close()
            except BrokenPipeError:
                pass

        starttime = time.monotonic()
        # A Timer kills the process on timeout while the main thread blocks on
        # ``wait4(pid, 0)`` — so ``runtime`` reflects the exact moment the
        # process exited (no busy-wait poll granularity inflating timed runs).
        killed = threading.Event()
        timer: threading.Timer | None = None
        if exe.timeout is not None:
            def _kill() -> None:
                killed.set()
                proc.kill()
            timer = threading.Timer(exe.timeout, _kill)
            timer.start()

        _, waitstatus, rusage = os.wait4(proc.pid, 0)
        endtime = time.monotonic()
        if timer is not None:
            timer.cancel()
        runtime = endtime - starttime

        stdout_f.seek(0)
        stderr_f.seek(0)
        stdout = stdout_f.read().decode(errors="replace")
        stderr = stderr_f.read().decode(errors="replace")

        returncode = 124 if killed.is_set() else os.waitstatus_to_exitcode(waitstatus)

        # execute() records facts only; judging success is the Runner's job
        # (see default_success / Benchmark.with_success).
        return ExecutionResult(
            execution=exe,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            runtime=runtime,
            rusage=rusage,
        )
    except OSError as e:
        return ExecutionResult(execution=exe, returncode=-1,
                               failure=f"spawn failed: {e}")
    finally:
        stdout_f.close()
        stderr_f.close()


def default_success(execution: Execution, result: ExecutionResult) -> Verdict:
    """Default success policy: clean exit passes, anything else fails."""
    if result.failure is not None:        # spawn failure already judged by execute()
        return result.failure
    if result.returncode == 124:
        return "timeout"
    if result.returncode != 0:
        return f"exit code {result.returncode}"
    return None


def judge(
    b: Benchmark, sched: ScheduledExecution, result: ExecutionResult
) -> tuple[ExecutionResult, list[Sample]]:
    """Apply the success policy to an already-run result, then parse samples.

    Asks the benchmark's ``success`` policy (or ``default_success``) for a
    verdict and stamps the reason onto ``result.failure``. On success runs the
    processors and stamps benchmark identity onto the samples; on failure
    returns no samples — a failed run never produces metrics.
    """
    reason = (b.success or default_success)(sched.execution, result)
    if reason is not None and result.failure is None:
        result = dataclasses.replace(result, failure=reason)
    samples = list(stamp(process_all(b.processors, result), sched)) if not result.is_failure() else []
    return result, samples


def evaluate(
    b: Benchmark, sched: ScheduledExecution
) -> tuple[ExecutionResult, list[Sample]]:
    """Spawn one ScheduledExecution and judge+parse it (see ``judge``)."""
    return judge(b, sched, execute(sched.execution))


# ---------------------------------------------------------------------------
# Plan builder (Suite list -> flat Benchmark list)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PlannedBenchmark:
    """A Benchmark associated with the suite name it belongs to."""

    suite: str
    benchmark: Benchmark


def plan(suites: list[Suite], ctx: Any) -> list[PlannedBenchmark]:
    """Flatten suites + their deferred factories into concrete benchmarks."""
    out: list[PlannedBenchmark] = []
    for s in suites:
        for b in s.materialize(ctx):
            out.append(PlannedBenchmark(suite=s.name, benchmark=b))
    return out


# ---------------------------------------------------------------------------
# Plan rendering (shared by Dry and the --verbose echo)
# ---------------------------------------------------------------------------


def format_scheduled(sched: ScheduledExecution, benchmark: Benchmark) -> str:
    """Render the full per-execution detail block printed by ``--verbose``.

    A header line plus indented command / cwd / env / timeout / run-plan / info
    fields. The run-plan is derived from the benchmark's warmup and measure
    policies. Shared by every runner's ``--verbose`` echo and by ``--dry -v``.
    """
    # "unbounded" for convergence policies (CoV etc.) whose max_runs() is None.
    def _label(p: StoppingPolicy) -> str:
        n = p.max_runs()
        return "unbounded" if n is None else str(n)

    e = sched.execution
    ident = f"{sched.suite}/{sched.benchmark}{format_variant(sched.info)}"

    measure = f"measure x{_label(benchmark.measure)}"
    warmup_n = _label(benchmark.warmup)
    plan_str = measure if warmup_n == "0" else f"warmup x{warmup_n}, {measure}"

    lines = [ident, f"  command: {' '.join(e.command)}", f"  cwd:     {e.cwd}"]
    if e.env:
        lines.append(f"  env:     {{{', '.join(f'{k}={v}' for k, v in e.env.items())}}}")
    if e.timeout is not None:
        lines.append(f"  timeout: {e.timeout}s")
    lines.append(f"  plan:    {plan_str}")
    if sched.info:
        lines.append(f"  info:    {dict(sched.info)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Runner base
# ---------------------------------------------------------------------------


class Runner(abc.ABC):
    """Drives compile() coroutines, calls executor, forwards samples.

    ``max_runs_per_phase`` is a defensive backstop for non-converging custom
    policies. ``max_consecutive_failures`` silently aborts a benchmark whose
    runs keep failing — surface that fact in the report rather than retrying
    forever. Bump it for flaky benchmarks; set high for purely-success suites.
    """

    def __init__(
        self,
        reporter: ReporterLike | None = None,
        *,
        max_runs_per_phase: int = 10_000,
        max_consecutive_failures: int = 5,
        verbose: bool = False,
    ) -> None:
        self.reporter = reporter or _NoopReporter()
        self.max_runs_per_phase = max_runs_per_phase
        self.max_consecutive_failures = max_consecutive_failures
        self.verbose = verbose
        # Guards the shared Report when workers record concurrently (Parallel).
        self._report_lock = threading.Lock()

    @abc.abstractmethod
    def run(self, suites: list[Suite], ctx: Any) -> Report: ...

    # ----- shared pump --------------------------------------------------

    def _record(
        self,
        report: Report,
        sched: ScheduledExecution,
        result: ExecutionResult,
        samples: list[Sample],
    ) -> None:
        with self._report_lock:
            report.record(sched, result, samples)
        # Reporters guard their own state; no need to hold the report lock.
        self.reporter.sample(sched, result, samples)

    def _run_benchmark(
        self, planned: PlannedBenchmark, ctx: Any, report: Report
    ) -> None:
        b = planned.benchmark
        consecutive_failures = 0
        guard = 0

        gen = b.compile(ctx, suite=planned.suite)
        try:
            sched = next(gen)
        except StopIteration:
            return

        if self.verbose:
            # One block per benchmark; lock so Parallel workers don't interleave.
            with self._report_lock:
                print(format_scheduled(sched, b))

        while True:
            guard += 1
            if guard > self.max_runs_per_phase * 2:  # warmup + measure
                raise RuntimeError(
                    f"Benchmark {b.name!r} exceeded max_runs_per_phase backstop "
                    f"({self.max_runs_per_phase}); did you forget .at_most(N)?"
                )

            # A failed run produces no metrics, only a RunRecord. The policy
            # still observes (sees ``[]``) so that ``.runs(N)`` counts every
            # attempt, not just successes.
            result, samples = evaluate(b, sched)
            consecutive_failures = 0 if not result.is_failure() else consecutive_failures + 1
            self._record(report, sched, result, samples)

            if consecutive_failures >= self.max_consecutive_failures:
                gen.close()
                return

            try:
                sched = gen.send(samples)
            except StopIteration:
                return
