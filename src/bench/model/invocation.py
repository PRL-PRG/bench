"""Invocation: the pure atom of a benchmark run.

An Invocation is a description of how to start one subprocess: command,
working directory, environment, optional timeout, optional stdin payload.
"""

from __future__ import annotations

import dataclasses
import resource
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

type Timeout = float | None
type Env = Mapping[str, str]
type Command = Sequence[str]


@dataclass(frozen=True, slots=True)
class Invocation:
    """Pure description of one subprocess invocation."""

    command: Command
    cwd: Path
    env: Env = dataclasses.field(default_factory=dict[str, str])
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


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------


def format_command(e: Invocation) -> str:
    """A copy-pasteable shell command for one execution: `cd DIR && KEY='v' cmd
    args`. The `cd` prefix appears only when the cwd differs from the current
    directory, env assignments only when present. Every part is shell-quoted."""
    parts: list[str] = []
    if e.cwd != Path.cwd():
        parts.append(f"cd {shlex.quote(str(e.cwd))} &&")
    if e.env:
        parts.append(" ".join(f"{k}={shlex.quote(v)}" for k, v in e.env.items()))
    parts.append(shlex.join(e.command))
    return " ".join(parts)
