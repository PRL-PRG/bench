"""Execution: the pure atom of a benchmark run.

An Execution is a description of how to start one subprocess: command,
working directory, environment, optional timeout, optional stdin payload.
It carries no benchmark-level identity (suite/benchmark/run/phase) — that
metadata lives on the ``ScheduledExecution`` produced by ``Benchmark.compile``
and on the ``Sample`` emitted afterward.
"""

from __future__ import annotations

import resource
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping, Optional

_EMPTY_ENV: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Execution:
    """Pure description of one subprocess invocation."""

    command: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] = _EMPTY_ENV
    timeout: float | None = None
    stdin: bytes | None = None


# ---------------------------------------------------------------------------
# ProcessResult: the runner's verdict about what happened to one Execution.
# ---------------------------------------------------------------------------

# `returncode` conventions (set on FailedProcessResult only):
#   124 ........... timed out (coreutils `timeout(1)` convention)
#   any other ≠ 0 . process crash / non-zero exit
#   0 ............. unreachable (success would be SuccessfulProcessResult)


@dataclass(frozen=True, slots=True)
class SuccessfulProcessResult:
    execution: Execution
    runtime: float                            # wall-clock seconds
    stdout: str
    stderr: str
    rusage: resource.struct_rusage | None


@dataclass(frozen=True, slots=True)
class FailedProcessResult:
    execution: Execution
    runtime: float | None
    stdout: str | None
    stderr: str | None
    rusage: resource.struct_rusage | None
    returncode: int
    # Only set for pre-execution failures (command not found, OSError on spawn).
    reason: str | None = None

    @staticmethod
    def empty(execution: Execution, reason: str) -> "FailedProcessResult":
        return FailedProcessResult(
            execution=execution,
            runtime=None,
            stdout=None,
            stderr=None,
            rusage=None,
            returncode=0,
            reason=reason,
        )


ProcessResult = SuccessfulProcessResult | FailedProcessResult


# ---------------------------------------------------------------------------
# ScheduledExecution: an Execution annotated with the benchmark identity it
# belongs to. Produced by Benchmark.compile; consumed by the Runner.
# ---------------------------------------------------------------------------

Phase = Literal["warmup", "measure"]


@dataclass(frozen=True, slots=True)
class ScheduledExecution:
    """An Execution plus the (suite, benchmark, run, phase, info) tag."""

    execution: Execution
    suite: str
    benchmark: str
    info: tuple[tuple[str, str], ...] = ()  # canonical, sorted
    run: int = 1
    phase: Phase = "measure"

    def identifier(self) -> str:
        out = f"{self.suite}/{self.benchmark}"
        if self.info:
            out += " (" + ", ".join(f"{k}={v}" for k, v in self.info) + ")"
        out += f" #{self.run} [{self.phase}]"
        return out
