"""Invocation: the pure atom of a benchmark run.

An Invocation is a description of how to start one subprocess: command,
working directory, environment, optional timeout, optional stdin payload.
"""

from __future__ import annotations

import dataclasses
import os
import resource
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence, cast

if TYPE_CHECKING:
    from bench.core.process import Command


def to_argv(command: Any) -> tuple[Any, ...]:
    """A bare str/bytes/PathLike is a one-element argv, a Sequence is full argv."""
    if isinstance(command, (str, bytes, os.PathLike)):
        return (cast(Any, command),)
    return tuple(command)


type Timeout = float | None


@dataclass(frozen=True, slots=True)
class Invocation:
    """Pure description of one subprocess invocation."""

    command: Command
    cwd: Path
    env: Mapping[str, str] = dataclasses.field(default_factory=dict[str, str])
    inherit_env: bool = False
    timeout: Timeout = None
    stdin: bytes | None = None
    capture_output: bool = True


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
    runtime: float
    stdout: str = ""
    stderr: str = ""
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


# TODO: Move to model
@dataclass(frozen=True, slots=True)
class Variant:
    """Representation of a variant"""

    pairs: tuple[tuple[str, str], ...] = ()
    "Canonical representation: `((dimension, value), ...)`, sorted by dimension"

    def __post_init__(self) -> None:
        # Canonicalize once, at construction: equality and hashing are the pair
        # tuple's, so two variants with the same dimensions must order alike.
        object.__setattr__(self, "pairs", tuple(sorted(self.pairs)))

    @staticmethod
    def _stringify_value(v: Any) -> str:
        if isinstance(v, (list, tuple)):
            return " ".join(str(x) for x in cast(Sequence[object], v))
        return str(v)

    @staticmethod
    def of(mapping: Mapping[str, Any]) -> Variant:
        return Variant(
            tuple((k, Variant._stringify_value(v)) for k, v in mapping.items())
        )

    def __getitem__(self, dim: str, /) -> str:
        for k, v in self.pairs:
            if k == dim:
                return v

        raise KeyError(dim)

    def get(self, dim: str, default: str | None = None) -> str | None:
        for k, v in self.pairs:
            if k == dim:
                return v
        return default

    def keys(self) -> tuple[str, ...]:
        return tuple(k for k, _ in self.pairs)

    def as_dict(self) -> dict[str, str]:
        return dict(self.pairs)

    def __iter__(self):
        return self.pairs.__iter__()

    def __len__(self) -> int:
        return self.pairs.__len__()

    def __contains__(self, dim: str) -> bool:
        for k, _ in self.pairs:
            if k == dim:
                return True

        return False


def format_variant_pairs(pairs: Iterable[tuple[str, str]]) -> str:
    """`k=v, ...` naming a variant on its own. `""` if empty. Unlike
    `format_variant` this carries no surrounding ` (...)`, so it also serves where
    the variant is the whole string: a summary label, a directory component."""
    return ", ".join(f"{k}={v}" for k, v in pairs)


def format_variant(variant: Variant) -> str:
    """` (k=v, ...)` suffix identifying a matrix variant. `""` if empty."""
    if not variant:
        return ""

    return f" ({format_variant_pairs(variant)})"


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
