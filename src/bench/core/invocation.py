"""Invocation: the pure atom of a benchmark run.

An Invocation is a description of how to start one subprocess: command,
working directory, environment, optional timeout, optional stdin payload.
"""

from __future__ import annotations

import os
import resource
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

EMPTY_MAPPING: Mapping[Any, Any] = MappingProxyType({})


def to_argv(command: Any) -> tuple[Any, ...]:
    """A bare str/bytes/PathLike is a one-element argv, a Sequence is full argv."""
    if isinstance(command, (str, bytes, os.PathLike)):
        return (cast(Any, command),)
    return tuple(command)


@dataclass(frozen=True, slots=True)
class Invocation:
    """Pure description of one subprocess invocation."""

    command: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] = EMPTY_MAPPING
    timeout: float | None = None
    stdin: bytes | None = None


@dataclass(frozen=True, slots=True)
class InvocationResult:
    """Outcome of running one Invocation.

       `failure` is the human-readable reason a run is treated as failed, or
       `None` for a success.

    `returncode` conventions:
      0 ............. clean exit
      124 .......... timed out (coreutils `timeout(1)` convention)
      any other > 0  process crash / non-zero exit
      -1 ........... pre-execution failure (spawn errored before the process ran,
                     no real exit code, `failure` set by `execute`)
    """

    invocation: Invocation
    returncode: int
    stdout: str = ""
    stderr: str = ""
    runtime: float | None = None  # wall-clock seconds
    rusage: resource.struct_rusage | None = None
    failure: str | None = None

    def is_failure(self) -> bool:
        return self.failure is not None


type Verdict = str | None  # None = success, str = failure reason
type SuccessFn = Callable[[InvocationResult], Verdict]


# Conventional returncode sentinels (see InvocationResult docstring above).
TIMEOUT_RC = 124
SPAWN_FAIL_RC = -1


def default_success(result: InvocationResult) -> Verdict:
    """Default success policy: clean exit passes, anything else fails."""
    if result.failure is not None:  # spawn failure already judged by execute()
        return result.failure
    if result.returncode == TIMEOUT_RC:
        return "timeout"
    if result.returncode != 0:
        return f"exit code {result.returncode}"
    return None


# Canonical matrix-variant identifier: sorted ((dimension_name, dimension_value), ...).
type Variant = tuple[tuple[str, str], ...]


def format_variant(variant: Variant) -> str:
    """` (k=v, ...)` suffix identifying a matrix variant. `""` if empty."""
    if not variant:
        return ""
    return " (" + ", ".join(f"{k}={v}" for k, v in variant) + ")"


def format_benchmark(
    suite: str,
    benchmark: str,
    variant: Variant,
    variant_label: str = "",
) -> str:
    """Resolved benchmark-variant name: `suite/benchmark` (collapsing the stutter
    when the two names match) with the variant label or `(k=v, ...)` suffix
    appended."""
    head = benchmark if suite == benchmark else f"{suite}/{benchmark}"
    if variant_label:
        return f"{head}/{variant_label}"
    return f"{head}{format_variant(variant)}"


def format_identifier(
    suite: str,
    benchmark: str,
    variant: Variant,
    run: int,
    variant_label: str = "",
) -> str:
    """Canonical run label: the benchmark name followed by `#run`."""
    return f"{format_benchmark(suite, benchmark, variant, variant_label)} #{run}"
