"""Execution / ProcessResult / ScheduledExecution shape."""

from pathlib import Path

import pytest

from benchr import (
    Execution, FailedProcessResult, ScheduledExecution, SuccessfulProcessResult,
)


def test_execution_is_frozen():
    e = Execution(command=("x",), cwd=Path("/tmp"))
    with pytest.raises(AttributeError):
        e.command = ("y",)  # type: ignore[misc]


def test_execution_defaults():
    e = Execution(command=("x",), cwd=Path("/tmp"))
    assert e.env == {}
    assert e.timeout is None
    assert e.stdin is None


def test_failed_process_result_empty():
    e = Execution(command=("x",), cwd=Path("/tmp"))
    f = FailedProcessResult.empty(e, "boom")
    assert f.reason == "boom"
    assert f.returncode == 0
    assert f.stdout is None and f.stderr is None and f.runtime is None


def test_scheduled_execution_identifier():
    sched = ScheduledExecution(
        execution=Execution(command=("x",), cwd=Path("/tmp")),
        suite="S", benchmark="B",
        info=(("opt", "O2"), ("cc", "gcc")),
        run=3, phase="warmup",
    )
    s = sched.identifier()
    assert s == "S/B (opt=O2, cc=gcc) #3 [warmup]"
