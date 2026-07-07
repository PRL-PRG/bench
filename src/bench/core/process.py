"""Subprocess execution.

Also home to the Ctrl+C machinery: every live benchmark subprocess is
tracked so a SIGINT can kill the whole subtree (each child runs in its own
process group) before the CLI exits. Without this, Python's
KeyboardInterrupt only unblocks the main thread's `os.wait4` and leaves
the children orphaned (and parallel worker threads stuck in `wait4`
forever, since SIGINT is delivered to the main thread only).
"""

from __future__ import annotations

import contextlib
import dataclasses
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
from pathlib import Path
from types import FrameType

from bench.core.invocation import (
    SPAWN_FAIL_RC,
    TIMEOUT_RC,
    Invocation,
    InvocationResult,
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
            # Already dead, or never made it into its own group, so fall back to
            # a direct kill on the proc itself.
            try:
                p.kill()
            except (ProcessLookupError, OSError):
                pass


@contextlib.contextmanager
def install_sigint_handler() -> Generator[None]:
    """Install a SIGINT handler that kills tracked subprocesses and restores
    the previous handler so a second Ctrl+C is a hard exit.

    No-op when called off the main thread (Python only allows `signal.signal`
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
    """`os.wait4` that retries on EINTR (the SIGINT itself wakes wait4)."""
    while True:
        try:
            return os.wait4(pid, 0)
        except InterruptedError:
            continue
        except OSError as e:
            if e.errno == errno.EINTR:
                continue
            raise


def _resolve_command(command: tuple[str, ...]) -> list[str]:
    """Resolve `argv[0]` against PATH to an absolute path.

    Raises `FileNotFoundError` if the command is not found. The absolute
    path is taken against the invoker's cwd so that `Popen(cwd=...)` doesn't
    re-resolve a relative executable against the subprocess's own cwd.
    """
    cmd = list(command)
    found = shutil.which(cmd[0])
    if found is None:
        raise FileNotFoundError(f"Command not found: {cmd[0]}")
    cmd[0] = os.path.abspath(found)
    return cmd


def execute(exe: Invocation) -> InvocationResult:
    """Spawn one subprocess and return an InvocationResult.

    Honors `exe.timeout` (returncode `TIMEOUT_RC` on timeout), captures
    stdout/stderr, and includes `rusage` via `os.wait4`. Pure mechanism,
    no policy, no reporting.
    """
    try:
        cmd = _resolve_command(exe.command)
    except FileNotFoundError as e:
        return InvocationResult(
            invocation=exe, returncode=SPAWN_FAIL_RC, failure=str(e)
        )

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
            # kill the whole subtree via `os.killpg` (matters for shell
            # wrappers like `sh -c "..."` that spawn the real workload).
            start_new_session=True,
        )
        _register_proc(proc)
        if exe.stdin and proc.stdin is not None:
            with contextlib.suppress(BrokenPipeError):
                proc.stdin.write(exe.stdin)
                proc.stdin.close()

        starttime = time.monotonic()
        # A Timer kills the process on timeout while the main thread blocks on
        # `wait4(pid, 0)`, so `runtime` reflects the exact moment the
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
            return InvocationResult(
                invocation=exe,
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

        # execute() records facts only. Judging success is the Runner's job
        # (see default_success / Benchmark.with_success).
        return InvocationResult(
            invocation=exe,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            runtime=runtime,
            rusage=rusage,
        )
    except OSError as e:
        return InvocationResult(
            invocation=exe, returncode=SPAWN_FAIL_RC, failure=f"spawn failed: {e}"
        )
    finally:
        if proc is not None:
            _unregister_proc(proc)
        stdout_f.close()
        stderr_f.close()


@dataclasses.dataclass
class LiveProcess:
    """A spawned-but-not-yet-reaped process writing to named output files."""

    proc: subprocess.Popen[bytes]
    invocation: Invocation
    stdout_path: Path
    stderr_path: Path
    _start: float
    _killed: threading.Event
    timer: threading.Timer | None = None
    # The reaper runs exactly once and caches its result. is_alive() polls it
    # non-blockingly (a harness reader thread tails until the process exits),
    # finish() reaps blockingly. Both go through _reap so the rusage-bearing
    # wait4 is never lost to a stray poll(). Guarded for the reader thread vs.
    # finish()/close() racing.
    _reap_lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    _reaped: bool = False
    _waitstatus: int = 0
    _rusage: resource.struct_rusage | None = None

    def _reap(self, *, blocking: bool) -> bool:
        """Reap the child once, caching (waitstatus, rusage). Returns True once
        the process has been reaped. Non-blocking reap returns False while the
        process is still running."""
        with self._reap_lock:
            if self._reaped:
                return True
            flags = 0 if blocking else os.WNOHANG
            while True:
                try:
                    pid, waitstatus, rusage = os.wait4(self.proc.pid, flags)
                except InterruptedError:
                    continue
                except OSError as e:
                    if e.errno == errno.EINTR:
                        continue
                    # Already reaped elsewhere (e.g. a stray poll()): fall back
                    # to the returncode subprocess cached, with no rusage.
                    self._reaped = True
                    rc = self.proc.returncode
                    self._waitstatus = 0 if rc is None else (rc if rc >= 0 else (-rc))
                    self.proc.returncode = rc if rc is not None else 0
                    return True
                if pid == 0:  # WNOHANG: still running
                    return False
                self._reaped = True
                self._waitstatus = waitstatus
                self._rusage = rusage
                # Keep subprocess's bookkeeping consistent so its own poll()
                # won't try to wait on an already-reaped pid.
                self.proc.returncode = os.waitstatus_to_exitcode(waitstatus)
                return True

    def is_alive(self) -> bool:
        return not self._reap(blocking=False)

    def kill(self) -> None:
        self._killed.set()
        if self.timer is not None:
            self.timer.cancel()
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            with contextlib.suppress(ProcessLookupError, OSError):
                self.proc.kill()

    def finish(self, *, killed: bool = False) -> InvocationResult:
        if self.timer is not None:
            self.timer.cancel()
        if killed:
            self.kill()
        self._reap(blocking=True)
        waitstatus, rusage = self._waitstatus, self._rusage
        runtime = time.monotonic() - self._start
        _unregister_proc(self.proc)
        stdout = self.stdout_path.read_text(errors="replace")
        stderr = self.stderr_path.read_text(errors="replace")
        shutil.rmtree(self.stdout_path.parent, ignore_errors=True)
        if interrupted():
            return InvocationResult(
                self.invocation,
                os.waitstatus_to_exitcode(waitstatus),
                stdout,
                stderr,
                runtime,
                rusage,
                failure="interrupted",
            )
        rc = (
            TIMEOUT_RC
            if self._killed.is_set()
            else os.waitstatus_to_exitcode(waitstatus)
        )
        return InvocationResult(self.invocation, rc, stdout, stderr, runtime, rusage)


def spawn_streaming(exe: Invocation) -> LiveProcess:
    """Spawn a process writing stdout/stderr to named temp files, and return a
    LiveProcess to be reaped via .finish(). Honors exe.timeout (a Timer kills
    on expiry, .finish() then reports TIMEOUT_RC).

    Raises FileNotFoundError if the command is not found (caller converts to
    a failed InvocationResult if needed).
    """
    cmd = _resolve_command(exe.command)

    d = Path(tempfile.mkdtemp(prefix="bench-harness-"))
    out_path, err_path = d / "stdout", d / "stderr"
    out_f = open(out_path, "wb")
    err_f = open(err_path, "wb")
    killed = threading.Event()
    # A harness streams per-iteration lines, so the (Python) child's stdout must
    # not block-buffer, otherwise it buffers when writing to a file and flushes
    # every line at once on exit, defeating live framing. Force PYTHONUNBUFFERED
    # (a no-op for non-Python children) while keeping env semantics: an empty env
    # still inherits the parent's.
    child_env = {**(dict(exe.env) if exe.env else os.environ), "PYTHONUNBUFFERED": "1"}
    proc = subprocess.Popen(
        cmd,
        cwd=str(exe.cwd),
        env=child_env,
        stdin=subprocess.PIPE if exe.stdin else None,
        stdout=out_f,
        stderr=err_f,
        shell=False,
        start_new_session=True,
    )
    _register_proc(proc)
    if exe.stdin and proc.stdin is not None:
        with contextlib.suppress(BrokenPipeError):
            proc.stdin.write(exe.stdin)
            proc.stdin.close()
    out_f.close()
    err_f.close()

    start = time.monotonic()
    live = LiveProcess(proc, exe, out_path, err_path, start, killed)

    if exe.timeout is not None:

        def _kill() -> None:
            killed.set()
            with contextlib.suppress(ProcessLookupError, OSError):
                os.killpg(proc.pid, signal.SIGKILL)

        timer = threading.Timer(exe.timeout, _kill)
        live.timer = timer
        timer.start()

    return live
