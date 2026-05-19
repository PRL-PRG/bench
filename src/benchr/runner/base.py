"""Runner protocol and shared coroutine pump.

Two abstractions are split intentionally:

  ``execute(sched)``  spawn one subprocess, return ProcessResult.
                      Pure mechanism — no policy, no reporting.
  ``Runner``          orchestrate compile() coroutines for a list of suites.
                      The coroutine yields ScheduledExecution; Runner calls
                      ``execute`` to get a ProcessResult; the benchmark's
                      Processor turns that into Samples; the Runner stamps
                      them, forwards to the Reporter, and sends them back
                      into the coroutine via ``.send()`` so the StoppingPolicy
                      can observe.

The default Runner has a configurable global cap (``max_runs_per_phase``) as a
safety net against custom policies that never converge.
"""

from __future__ import annotations

import abc
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Protocol

from benchr.grammar.benchmark import Benchmark
from benchr.grammar.execution import (
    Execution,
    FailedProcessResult,
    ProcessResult,
    ScheduledExecution,
    SuccessfulProcessResult,
)
from benchr.grammar.processor import Processor, stamp
from benchr.grammar.suite import Suite
from benchr.report.sample import Sample


# Reporter is a forward reference to avoid a circular import; we describe its
# shape with a Protocol so reporter.py can register without inheriting.


class ReporterLike(Protocol):
    def start(self, plan: list[Benchmark]) -> None: ...
    def sample(self, sched: ScheduledExecution, pr: ProcessResult, samples: list[Sample]) -> None: ...
    def finalize(self) -> None: ...


class _NoopReporter:
    def start(self, plan): pass
    def sample(self, sched, pr, samples): pass
    def finalize(self): pass


# ---------------------------------------------------------------------------
# Subprocess execution — single Execution -> ProcessResult.
# ---------------------------------------------------------------------------


def execute(exe: Execution) -> ProcessResult:
    """Spawn one subprocess and return a ProcessResult.

    Honors ``exe.timeout`` (returncode 124 on timeout), captures stdout/stderr,
    and includes ``rusage`` via ``os.wait4``.
    """
    cmd = list(exe.command)
    found = shutil.which(cmd[0])
    if found is None:
        return FailedProcessResult.empty(exe, f"Command not found: {cmd[0]}")
    cmd[0] = found

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
        if exe.stdin:
            try:
                proc.stdin.write(exe.stdin)
                proc.stdin.close()
            except BrokenPipeError:
                pass

        starttime = time.monotonic()
        rusage = None
        waitstatus = None
        timed_out = False

        if exe.timeout is not None:
            stoptime = starttime + exe.timeout
            while True:
                pid, waitstatus, rusage = os.wait4(proc.pid, os.WNOHANG)
                if pid == proc.pid:
                    break
                if time.monotonic() >= stoptime:
                    timed_out = True
                    proc.kill()
                    break
                time.sleep(0.01)

        if waitstatus is None or timed_out:
            _, waitstatus, rusage = os.wait4(proc.pid, 0)

        endtime = time.monotonic()
        runtime = endtime - starttime

        stdout_f.seek(0)
        stderr_f.seek(0)
        stdout = stdout_f.read().decode(errors="replace")
        stderr = stderr_f.read().decode(errors="replace")

        returncode = 124 if timed_out else os.waitstatus_to_exitcode(waitstatus)

        if returncode != 0:
            return FailedProcessResult(
                execution=exe,
                runtime=runtime,
                stdout=stdout,
                stderr=stderr,
                rusage=rusage,
                returncode=returncode,
            )
        return SuccessfulProcessResult(
            execution=exe,
            runtime=runtime,
            stdout=stdout,
            stderr=stderr,
            rusage=rusage,
        )
    except OSError as e:
        return FailedProcessResult.empty(exe, f"spawn failed: {e}")
    finally:
        stdout_f.close()
        stderr_f.close()


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
# Runner base
# ---------------------------------------------------------------------------


class Runner(abc.ABC):
    """Drives compile() coroutines, calls executor, forwards samples."""

    def __init__(
        self,
        reporter: ReporterLike | None = None,
        *,
        max_runs_per_phase: int = 10_000,
        max_consecutive_failures: int = 5,
    ) -> None:
        self.reporter = reporter or _NoopReporter()
        self.max_runs_per_phase = max_runs_per_phase
        self.max_consecutive_failures = max_consecutive_failures

    @abc.abstractmethod
    def run(self, suites: list[Suite], ctx: Any) -> list[Sample]: ...

    # ----- shared pump --------------------------------------------------

    def _run_benchmark(self, planned: PlannedBenchmark, ctx: Any) -> list[Sample]:
        b = planned.benchmark
        if b.processor is None:
            raise ValueError(f"Benchmark {b.name!r} has no processor")
        all_samples: list[Sample] = []
        consecutive_failures = 0
        guard = 0

        gen = b.compile(ctx, suite=planned.suite)
        try:
            sched = next(gen)
        except StopIteration:
            return all_samples

        while True:
            guard += 1
            if guard > self.max_runs_per_phase * 2:  # warmup + measure
                raise RuntimeError(
                    f"Benchmark {b.name!r} exceeded max_runs_per_phase backstop "
                    f"({self.max_runs_per_phase}); did you forget .at_most(N)?"
                )

            pr = execute(sched.execution)

            assert b.processor is not None
            if b.processor.is_success(pr):
                consecutive_failures = 0
                partials = list(b.processor.process(pr))
                samples = list(stamp(partials, sched))
            else:
                consecutive_failures += 1
                # On failure: still call process() — processors may emit a
                # `failed` flag via on_failure handlers. But the policy is not
                # advanced because we send [] back to the coroutine.
                partials = list(b.processor.process(pr))
                samples = list(stamp(partials, sched))

            all_samples.extend(samples)
            self.reporter.sample(sched, pr, samples)

            if consecutive_failures >= self.max_consecutive_failures:
                # Abort this benchmark.
                gen.close()
                return all_samples

            send_value: list[Sample] = (
                samples if b.processor.is_success(pr) else []
            )
            try:
                sched = gen.send(send_value)
            except StopIteration:
                return all_samples
