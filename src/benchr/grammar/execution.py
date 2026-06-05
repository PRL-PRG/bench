"""Execution: the pure atom of a benchmark run.

An Execution is a description of how to start one subprocess: command,
working directory, environment, optional timeout, optional stdin payload.
It carries no benchmark-level identity (suite/benchmark/run/phase) — that
metadata lives on the ``ScheduledExecution`` produced by ``Benchmark.compile``
and on the ``Sample`` emitted afterward.
"""

from __future__ import annotations

import resource
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Literal, Mapping

_EMPTY_MAPPING: Mapping[Any, Any] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Execution:
    """Pure description of one subprocess invocation."""

    command: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str] = _EMPTY_MAPPING
    timeout: float | None = None
    stdin: bytes | None = None


# ---------------------------------------------------------------------------
# ExecutionResult: what happened when one Execution ran.
#
# ``execute`` records facts only — it does not judge success. The Runner asks a
# ``SuccessFn`` (default ``default_success``; per-benchmark overridable via
# ``Benchmark.with_success``) for a ``Verdict`` and stamps the resulting
# ``failure`` reason onto the result. A failed run carries no metrics.
#
# `returncode` conventions:
#   0 ............. clean exit
#   124 .......... timed out (coreutils `timeout(1)` convention)
#   any other > 0  process crash / non-zero exit
#   -1 ........... pre-execution failure (spawn errored before the process ran —
#                  no real exit code; ``failure`` set by ``execute``)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Outcome of running one Execution.

    ``failure`` is the human-readable reason a run is treated as failed, or
    ``None`` for a success.
    """

    execution: Execution
    returncode: int
    stdout: str = ""
    stderr: str = ""
    runtime: float | None = None              # wall-clock seconds
    rusage: resource.struct_rusage | None = None
    failure: str | None = None

    def is_failure(self) -> bool:
        return self.failure is not None


type Verdict = str | None                     # None = success; str = failure reason
type SuccessFn = Callable[[Execution, ExecutionResult], Verdict]


# ---------------------------------------------------------------------------
# ScheduledExecution: an Execution annotated with the benchmark identity it
# belongs to. Produced by Benchmark.compile; consumed by the Runner.
# ---------------------------------------------------------------------------

Phase = Literal["warmup", "measure"]


def format_variant(variant: tuple[tuple[str, str], ...]) -> str:
    """`` (k=v, …)`` suffix identifying a matrix variant; ``""`` if empty."""
    if not variant:
        return ""
    return " (" + ", ".join(f"{k}={v}" for k, v in variant) + ")"


def format_identifier(
    suite: str,
    benchmark: str,
    variant: tuple[tuple[str, str], ...],
    run: int,
    phase: Phase,
    variant_label: str = "",
) -> str:
    """Canonical run label: ``suite/benchmark[/label or (k=v, …)] #run [phase]``.

    Collapses ``suite/benchmark`` to a single token when the two names match
    (common for one-off CLI runs).
    """
    head = benchmark if suite == benchmark else f"{suite}/{benchmark}"
    if variant_label:
        head = f"{head}/{variant_label}"
    else:
        head = f"{head}{format_variant(variant)}"
    return f"{head} #{run} [{phase}]"


@dataclass(frozen=True, slots=True)
class ScheduledExecution:
    """An Execution plus the (suite, benchmark, run, phase, variant) tag.

    ``variant`` is the matrix cell identifier — a canonical (sorted) tuple of
    ``(axis_name, axis_value)`` pairs. ``variant_label`` is the human-readable
    name of the variant (from ``Benchmark.label_fn`` or, by default, the
    formatted ``variant`` tuple).
    """

    execution: Execution
    suite: str
    benchmark: str
    variant: tuple[tuple[str, str], ...] = ()  # canonical, sorted
    variant_label: str = ""
    run: int = 1
    phase: Phase = "measure"

    def identifier(self) -> str:
        return format_identifier(self.suite, self.benchmark, self.variant,
                                 self.run, self.phase,
                                 variant_label=self.variant_label)
