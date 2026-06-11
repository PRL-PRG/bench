"""Subprocess execution: one Execution in, one ExecutionResult out.

Also home to the Ctrl+C machinery: every live benchmark subprocess is
tracked so a SIGINT can kill the whole subtree (each child runs in its own
process group) before the CLI exits. Without this, Python's
KeyboardInterrupt only unblocks the main thread's ``os.wait4`` and leaves
the children orphaned (and parallel worker threads stuck in ``wait4``
forever, since SIGINT is delivered to the main thread only).
"""

from __future__ import annotations

import contextlib
import errno
import os
import resource
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Generator
from types import FrameType

from benchr.core.execution import (
    SPAWN_FAIL_RC,
    TIMEOUT_RC,
    Execution,
    ExecutionResult,
)

_INTERRUPTED = threading.Event()
_LIVE_PROCS: set[subprocess.Popen[bytes]] = set()
_LIVE_LOCK = threading.Lock()


def interrupted() -> bool:
    """True once a SIGINT has been seen by the installed handler."""
    return _INTERRUPTED.is_set()


def _register_proc(proc: subprocess.Popen[bytes]) -> None:
    with _LIVE_LOCK:
        _LIVE_PROCS.add(proc)


def _unregister_proc(proc: subprocess.Popen[bytes]) -> None:
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
def install_sigint_handler() -> Generator[None]:
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

    def _handler(signum: int, frame: FrameType | None) -> None:
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


def _wait4_eintr(pid: int) -> tuple[int, int, resource.struct_rusage]:
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
    proc: subprocess.Popen[bytes] | None = None
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

        if interrupted():
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
