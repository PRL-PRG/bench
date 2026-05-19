"""Shared test fixtures and helpers for the v2 test suite."""

from pathlib import Path
from types import SimpleNamespace

from benchr import (
    Execution, FailedProcessResult, ScheduledExecution, SuccessfulProcessResult,
)


def make_execution(**overrides) -> Execution:
    defaults = dict(
        command=("echo", "hi"),
        cwd=Path("/tmp"),
        env={},
        timeout=None,
        stdin=None,
    )
    defaults.update(overrides)
    return Execution(**defaults)


def make_sched(**overrides) -> ScheduledExecution:
    defaults = dict(
        execution=make_execution(),
        suite="S",
        benchmark="B",
        info=(),
        run=1,
        phase="measure",
    )
    defaults.update(overrides)
    return ScheduledExecution(**defaults)


def make_success(stdout: str = "", stderr: str = "", runtime: float = 1.0,
                 rusage=None, **exe_kw) -> SuccessfulProcessResult:
    return SuccessfulProcessResult(
        execution=make_execution(**exe_kw),
        runtime=runtime, stdout=stdout, stderr=stderr, rusage=rusage,
    )


def make_failure(returncode: int = 1, stdout=None, stderr=None, runtime=None,
                 rusage=None, reason=None, **exe_kw) -> FailedProcessResult:
    return FailedProcessResult(
        execution=make_execution(**exe_kw),
        runtime=runtime, stdout=stdout, stderr=stderr, rusage=rusage,
        returncode=returncode, reason=reason,
    )


def make_rusage(**fields) -> SimpleNamespace:
    defaults = {
        "ru_utime": 0.5, "ru_stime": 0.1, "ru_maxrss": 10240,
        "ru_ixrss": 0, "ru_idrss": 0, "ru_isrss": 0,
        "ru_minflt": 100, "ru_majflt": 0, "ru_nswap": 0,
        "ru_inblock": 0, "ru_oublock": 0, "ru_msgsnd": 0,
        "ru_msgrcv": 0, "ru_nsignals": 0, "ru_nvcsw": 10, "ru_nivcsw": 5,
    }
    defaults.update(fields)
    return SimpleNamespace(**defaults)
