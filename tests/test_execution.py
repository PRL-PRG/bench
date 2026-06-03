"""Execution / ExecutionResult / ScheduledExecution shape."""

from pathlib import Path

import pytest

from benchr import (
    Execution, ExecutionResult, ScheduledExecution,
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


def test_execution_result_failure():
    e = Execution(command=("x",), cwd=Path("/tmp"))
    f = ExecutionResult(execution=e, returncode=-1, failure="boom")
    assert f.is_failure()
    assert f.failure == "boom"
    assert f.returncode == -1
    assert f.stdout == "" and f.stderr == "" and f.runtime is None


def test_execution_result_success_defaults():
    e = Execution(command=("x",), cwd=Path("/tmp"))
    ok = ExecutionResult(execution=e, returncode=0, stdout="1\n", runtime=1.0)
    assert not ok.is_failure()
    assert ok.failure is None


def test_scheduled_execution_identifier():
    sched = ScheduledExecution(
        execution=Execution(command=("x",), cwd=Path("/tmp")),
        suite="S", benchmark="B",
        info=(("opt", "O2"), ("cc", "gcc")),
        run=3, phase="warmup",
    )
    s = sched.identifier()
    assert s == "S/B (opt=O2, cc=gcc) #3 [warmup]"
