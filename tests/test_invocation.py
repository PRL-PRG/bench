"""Invocation, run identifiers, and streaming spawn."""

import time
from pathlib import Path

from bench import Invocation
from bench.core.invocation import format_identifier
from bench.core.process import spawn_streaming


def test_format_identifier():
    s = format_identifier("S", "B", (("opt", "O2"), ("cc", "gcc")), 3)
    assert s == "S/B (opt=O2, cc=gcc) #3"


def test_spawn_streaming_writes_incrementally_then_finishes():
    exe = Invocation(
        command=("sh", "-c", "echo a; sleep 0.05; echo b"), cwd=Path("/tmp")
    )
    live = spawn_streaming(exe)
    # output file exists and fills over time
    deadline = time.monotonic() + 2
    while "a" not in live.stdout_path.read_text() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "a" in live.stdout_path.read_text()
    res = live.finish()
    assert res.returncode == 0
    assert "a" in res.stdout and "b" in res.stdout
    assert res.runtime is not None


def test_spawn_streaming_finish_killed_marks_timeout_like():
    exe = Invocation(command=("sleep", "5"), cwd=Path("/tmp"))
    live = spawn_streaming(exe)
    res = live.finish(killed=True)
    assert res.returncode == 124


def test_spawn_streaming_finish_after_is_alive_polling():
    # A monitor tails by polling is_alive(), that must not reap the child out
    # from under finish()'s rusage-bearing wait4. Poll to completion, then
    # finish() must still return a valid result (with rusage).
    exe = Invocation(command=("sh", "-c", "echo a; echo b"), cwd=Path("/tmp"))
    live = spawn_streaming(exe)
    deadline = time.monotonic() + 5
    while live.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not live.is_alive()
    res = live.finish()
    assert res.returncode == 0
    assert "a" in res.stdout and "b" in res.stdout
    assert res.rusage is not None
    assert res.runtime is not None


def test_spawn_streaming_finish_cancels_timeout_timer():
    # A generous timeout that should never fire for a fast command: finish()
    # must return promptly (the unfired Timer must be cancelled, not left to
    # block interpreter exit for the whole timeout window).
    exe = Invocation(command=("sh", "-c", "sleep 0.05"), cwd=Path("/tmp"), timeout=30)
    live = spawn_streaming(exe)
    t = time.monotonic()
    res = live.finish()
    assert time.monotonic() - t < 2
    assert res.returncode == 0
    assert live.timer is None or not live.timer.is_alive()
