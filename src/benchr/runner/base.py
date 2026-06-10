"""Runner protocol and shared coroutine pump."""

from __future__ import annotations

import abc
import contextlib
import dataclasses
import errno
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator

from benchr.grammar.benchmark import Benchmark
from benchr.grammar.execution import (
    SPAWN_FAIL_RC,
    TIMEOUT_RC,
    Execution,
    ExecutionResult,
    ScheduledExecution,
    default_success,
)
from benchr.grammar.metric import extract_all
from benchr.grammar.suite import Suite
from benchr.report.reporter import Reporter
from benchr.report.sample import Report, Sample


# ---------------------------------------------------------------------------
# Ctrl+C handling: track every live benchmark subprocess so a SIGINT can kill
# the whole subtree (each child runs in its own process group) before the CLI
# exits. Without this, Python's KeyboardInterrupt only unblocks the main
# thread's ``os.wait4`` and leaves the children orphaned (and parallel worker
# threads stuck in ``wait4`` forever, since SIGINT is delivered to the main
# thread only).
# ---------------------------------------------------------------------------

_INTERRUPTED = threading.Event()
_LIVE_PROCS: set[subprocess.Popen] = set()
_LIVE_LOCK = threading.Lock()


def _register_proc(proc: subprocess.Popen) -> None:
    with _LIVE_LOCK:
        _LIVE_PROCS.add(proc)


def _unregister_proc(proc: subprocess.Popen) -> None:
    with _LIVE_LOCK:
        _LIVE_PROCS.discard(proc)


def _kill_all_live_procs() -> None:
    with _LIVE_LOCK:
        procs = list(_LIVE_PROCS)
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            # Already dead, or never made it into its own group — fall back to
            # a direct kill on the proc itself.
            try:
                p.kill()
            except (ProcessLookupError, OSError):
                pass


@contextlib.contextmanager
def install_sigint_handler() -> Iterator[None]:
    """Install a SIGINT handler that kills tracked subprocesses and restores
    the previous handler so a second Ctrl+C is a hard exit.

    No-op when called off the main thread (Python only allows ``signal.signal``
    in the main thread). Library callers running a runner inside their own
    worker thread get the previous behavior.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    _INTERRUPTED.clear()
    prev = signal.getsignal(signal.SIGINT)

    def _handler(signum: int, frame) -> None:
        _INTERRUPTED.set()
        _kill_all_live_procs()
        # Restore the previous handler so a second Ctrl+C is a hard exit
        # instead of being swallowed while we drain the pool.
        signal.signal(signal.SIGINT, prev)

    signal.signal(signal.SIGINT, _handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, prev)
        _INTERRUPTED.clear()


def _wait4_eintr(pid: int) -> tuple[int, int, Any]:
    """``os.wait4`` that retries on EINTR (the SIGINT itself wakes wait4)."""
    while True:
        try:
            return os.wait4(pid, 0)
        except InterruptedError:
            continue
        except OSError as e:
            if e.errno == errno.EINTR:
                continue
            raise


class _NoopReporter(Reporter):
    def sample(
        self, sched: ScheduledExecution, result: ExecutionResult, samples: list[Sample]
    ) -> None:
        pass


# ---------------------------------------------------------------------------
# Subprocess execution — single Execution -> ExecutionResult.
# ---------------------------------------------------------------------------


def execute(exe: Execution) -> ExecutionResult:
    """Spawn one subprocess and return an ExecutionResult.

    Honors ``exe.timeout`` (returncode ``TIMEOUT_RC`` on timeout), captures
    stdout/stderr, and includes ``rusage`` via ``os.wait4``. Pure mechanism —
    no policy, no reporting.
    """
    cmd = list(exe.command)
    found = shutil.which(cmd[0])
    if found is None:
        return ExecutionResult(
            execution=exe,
            returncode=SPAWN_FAIL_RC,
            failure=f"Command not found: {cmd[0]}",
        )
    # Resolve to absolute against the invoker's cwd so that ``Popen(cwd=…)``
    # doesn't re-resolve a relative executable against the subprocess's cwd.
    cmd[0] = os.path.abspath(found)

    stdout_f = tempfile.TemporaryFile()
    stderr_f = tempfile.TemporaryFile()
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(exe.cwd),
            env=dict(exe.env) if exe.env else None,
            stdin=subprocess.PIPE if exe.stdin else None,
            stdout=stdout_f,
            stderr=stderr_f,
            shell=False,
            # Put the child in its own process group so a Ctrl+C handler can
            # kill the whole subtree via ``os.killpg`` (matters for shell
            # wrappers like ``sh -c "..."`` that spawn the real workload).
            start_new_session=True,
        )
        _register_proc(proc)
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

        _, waitstatus, rusage = _wait4_eintr(proc.pid)
        endtime = time.monotonic()
        if timer is not None:
            timer.cancel()
        runtime = endtime - starttime

        stdout_f.seek(0)
        stderr_f.seek(0)
        stdout = stdout_f.read().decode(errors="replace")
        stderr = stderr_f.read().decode(errors="replace")

        if _INTERRUPTED.is_set():
            return ExecutionResult(
                execution=exe,
                returncode=os.waitstatus_to_exitcode(waitstatus),
                stdout=stdout,
                stderr=stderr,
                runtime=runtime,
                rusage=rusage,
                failure="interrupted",
            )

        returncode = (
            TIMEOUT_RC if killed.is_set() else os.waitstatus_to_exitcode(waitstatus)
        )

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
        return ExecutionResult(
            execution=exe, returncode=SPAWN_FAIL_RC, failure=f"spawn failed: {e}"
        )
    finally:
        if proc is not None:
            _unregister_proc(proc)
        stdout_f.close()
        stderr_f.close()


def judge(
    b: Benchmark, sched: ScheduledExecution, result: ExecutionResult
) -> tuple[ExecutionResult, list[Sample]]:
    """Apply the success policy and extract samples.

    Asks the benchmark's ``success`` policy (``default_success`` unless
    overridden) for a verdict and stamps the reason onto ``result.failure``.
    On success runs the metrics; on failure returns no samples — a failed run
    never produces metrics.
    """
    reason = b.success(sched.execution, result)
    if reason is not None and result.failure is None:
        result = dataclasses.replace(result, failure=reason)
    samples = (
        list(extract_all(b.metrics, result)) if not result.is_failure() else []
    )
    return result, samples


# ---------------------------------------------------------------------------
# Plan builder (Suite list -> flat Benchmark list)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PlannedBenchmark:
    """A Benchmark associated with the suite name it belongs to."""

    suite: str
    benchmark: Benchmark


def plan(suites: Suite | list[Suite], ctx: Any = None) -> list[PlannedBenchmark]:
    """Flatten suites + their deferred factories into concrete benchmarks."""
    if isinstance(suites, Suite):
        suites = [suites]
    out: list[PlannedBenchmark] = []
    for s in suites:
        for b in s.materialize(ctx):
            out.append(PlannedBenchmark(suite=s.name, benchmark=b))
    return out


def format_scheduled_verbose(sched: ScheduledExecution, benchmark: Benchmark) -> str:
    """Dump a ScheduledExecution + benchmark plan as a deterministic text block.

    One header (``sched.identifier()``) followed by every field of
    ``ScheduledExecution`` (and its nested ``Execution``) plus the benchmark's
    metric/success/plan summary. Every line printed every time — no
    conditional fields.
    """
    e = sched.execution
    env_str = ", ".join(f"{k}={v}" for k, v in e.env.items()) if e.env else ""
    stdin_str = f"{len(e.stdin)} bytes" if e.stdin is not None else "<none>"
    timeout_str = f"{e.timeout}s" if e.timeout is not None else "<none>"
    metric_str = ", ".join(type(m).__name__ for m in benchmark.metrics)
    success_str = (
        "<default>"
        if benchmark.success is default_success
        else getattr(benchmark.success, "__name__", repr(benchmark.success))
    )
    variant_str = dict(sched.variant) if sched.variant else {}
    label_str = sched.variant_label or "<none>"

    return "\n".join(
        [
            sched.identifier(),
            f"  suite:      {sched.suite}",
            f"  benchmark:  {sched.benchmark}",
            f"  run:        {sched.run}",
            f"  phase:      {sched.phase}",
            f"  command:    {' '.join(e.command)}",
            f"  cwd:        {e.cwd}",
            f"  env:        {{{env_str}}}",
            f"  timeout:    {timeout_str}",
            f"  stdin:      {stdin_str}",
            f"  metrics:    {metric_str}",
            f"  success:    {success_str}",
            f"  variant:    {variant_str}",
            f"  label:      {label_str}",
        ]
    )


# ---------------------------------------------------------------------------
# Runner base
# ---------------------------------------------------------------------------


class Runner(abc.ABC):
    """Drives compile() coroutines, calls executor, forwards samples.

    The coroutine yields ScheduledExecution; the Runner runs it via
    ``execute``, ``judge``s the result against the benchmark's metrics, forwards
    samples to the Reporter, and sends them back into the coroutine with
    ``.send()`` so the StoppingPolicy can observe. ``run()`` returns the
    accumulated ``Report``.

    ``max_runs_per_phase`` is a defensive backstop for non-converging custom
    policies. ``max_consecutive_failures`` silently aborts a benchmark whose
    runs keep failing — surface that fact in the report rather than retrying
    forever. Bump it for flaky benchmarks; set high for purely-success suites.
    """

    def __init__(
        self,
        reporter: Reporter | None = None,
        *,
        max_runs_per_phase: int = 10_000,
        max_consecutive_failures: int = 5,
        verbose: bool = False,
    ) -> None:
        self.reporter = reporter or _NoopReporter()
        self.max_runs_per_phase = max_runs_per_phase
        self.max_consecutive_failures = max_consecutive_failures
        self.verbose = verbose

    @abc.abstractmethod
    def run(
        self, planned: list[PlannedBenchmark], ctx: Any = None
    ) -> Report: ...

    # ----- shared pump --------------------------------------------------

    def _record(
        self,
        report: Report,
        sched: ScheduledExecution,
        result: ExecutionResult,
        samples: list[Sample],
    ) -> None:
        report.record(sched, result, samples)
        self.reporter.sample(sched, result, samples)

    def _print_verbose(self, sched: ScheduledExecution, b: Benchmark) -> None:
        """Print the per-benchmark verbose block."""
        print(format_scheduled_verbose(sched, b))

    def _run_benchmark(
        self, planned: PlannedBenchmark, ctx: Any, report: Report
    ) -> None:
        b = planned.benchmark
        consecutive_failures = 0
        guard = 0

        bounded = (
            b.warmup.max_runs() is not None
            and b.measure.max_runs() is not None
        )
        failure_cap = None if bounded else self.max_consecutive_failures

        if _INTERRUPTED.is_set():
            return

        gen = b.compile(ctx, suite=planned.suite)
        try:
            sched = next(gen)
        except StopIteration:
            return

        if self.verbose:
            self._print_verbose(sched, b)

        while True:
            guard += 1
            if guard > self.max_runs_per_phase * 2:  # warmup + measure
                raise RuntimeError(
                    f"Benchmark {b.name!r} exceeded max_runs_per_phase backstop "
                    f"({self.max_runs_per_phase}); did you forget .at_most(N)?"
                )

            result, samples = judge(b, sched, execute(sched.execution))
            consecutive_failures = (
                0 if not result.is_failure() else consecutive_failures + 1
            )
            self._record(report, sched, result, samples)

            if _INTERRUPTED.is_set():
                gen.close()
                return

            if failure_cap is not None and consecutive_failures >= failure_cap:
                gen.close()
                return

            try:
                sched = gen.send(samples)
            except StopIteration:
                return
