"""Benchmark: a generator of Executions, driven by warmup/measure policies.

A ``Benchmark`` is a frozen value object. To run it you call ``.compile(ctx)``
which returns a generator coroutine; the Runner pumps the coroutine by
``send()``ing the parsed samples back so stopping policies can observe and
decide whether to continue.

Two policies live on a Benchmark: ``warmup`` (samples reported, not fed to the
measure policy) and ``measure`` (samples reported and fed to the policy that
controls how many more measure runs to take). Both default to no-op:
``warmup = FixedRuns(0)``, ``measure = FixedRuns(1)``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Generator, Mapping, Sequence

from benchr.grammar.execution import (
    Execution,
    Phase,
    ScheduledExecution,
)
from benchr.grammar.policy import FixedRuns, StoppingPolicy
from benchr.grammar.processor import Processor
from benchr.report.sample import Sample


# A user-supplied command builder. Receives the benchmark and the ctx (the
# typed RunContext dataclass). Returns a list[str].
CommandFn = Callable[["Benchmark", Any], Sequence[str]]
PathFn = Callable[["Benchmark", Any], Path]
EnvFn = Callable[["Benchmark", Any], Mapping[str, str]]


_EMPTY_ENV: Mapping[str, str] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class Benchmark:
    """A named, frozen benchmark.

    ``data`` holds arbitrary user-supplied keyword args (e.g. ``path=...`` for
    a file-discovered benchmark). Access them as attributes on ``benchmark``:
    ``benchmark.path``, ``benchmark.size``. The ``__getattr__`` hook below
    forwards unknown attribute reads into ``data``.

    Keys starting with ``__`` are reserved (currently only ``__info__``,
    used by ``Suite.matrix`` to stamp variant labels onto Samples).
    """

    name: str

    # Either a static list[str] command, or a callable (benchmark, ctx) -> [str].
    command: Sequence[str] | CommandFn | None = None
    cwd: Path | PathFn | None = None
    env: Mapping[str, str] | EnvFn = _EMPTY_ENV
    timeout: float | None = None

    processor: Processor | None = None

    warmup: StoppingPolicy = FixedRuns(0)
    measure: StoppingPolicy = FixedRuns(1)

    # User payload; accessible as benchmark.<key>
    data: Mapping[str, Any] = _EMPTY_ENV

    # ----- attribute access into data ---------------------------------

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only called when normal lookup fails; safe to use even
        # with slots.
        data = object.__getattribute__(self, "data")
        if name in data:
            return data[name]
        raise AttributeError(name)

    # ----- with_* methods ---------------------------------------------

    def with_command(self, command: Sequence[str] | CommandFn) -> "Benchmark":
        return dataclasses.replace(self, command=command)

    def with_cwd(self, cwd: Path | PathFn) -> "Benchmark":
        return dataclasses.replace(self, cwd=cwd)

    def with_env(self, env: Mapping[str, str] | EnvFn) -> "Benchmark":
        return dataclasses.replace(self, env=env)

    def with_timeout(self, timeout: float) -> "Benchmark":
        return dataclasses.replace(self, timeout=timeout)

    def with_process(self, processor: Processor) -> "Benchmark":
        return dataclasses.replace(self, processor=processor)

    def with_warmup(self, p: StoppingPolicy | int) -> "Benchmark":
        return dataclasses.replace(self, warmup=_coerce_policy(p))

    def with_measure(self, p: StoppingPolicy | int) -> "Benchmark":
        return dataclasses.replace(self, measure=_coerce_policy(p))

    def runs(self, n: int) -> "Benchmark":
        """Sugar for ``.with_measure(FixedRuns(n))``."""
        return self.with_measure(FixedRuns(n))

    # ----- compile -----------------------------------------------------

    def compile(
        self,
        ctx: Any,
        *,
        suite: str = "",
    ) -> Generator[ScheduledExecution, list[Sample], None]:
        """Yield ScheduledExecutions; the Runner sends back parsed Samples.

        ``measure`` policy receives Samples; ``warmup`` policy does too, in case
        you want CoV-driven warmup (run until things stabilize, then start
        measuring). Samples emitted during warmup are tagged ``phase="warmup"``
        — formatters skip them by default but they appear in JSON/CSV/dir
        outputs.

        Failure handling: if a run produces *no* samples (the Runner only sends
        samples from successful runs — see Runner.is_success), the policy is
        not advanced.
        """
        if self.command is None:
            raise ValueError(f"Benchmark {self.name!r} has no command")
        if self.cwd is None:
            raise ValueError(f"Benchmark {self.name!r} has no cwd")
        if self.processor is None:
            raise ValueError(f"Benchmark {self.name!r} has no processor")

        yield from self._phase(ctx, suite, self.warmup, phase="warmup")
        yield from self._phase(ctx, suite, self.measure, phase="measure")

    def _phase(
        self,
        ctx: Any,
        suite: str,
        policy: StoppingPolicy,
        phase: Phase,
    ) -> Generator[ScheduledExecution, list[Sample], None]:
        state = policy.start()
        run = 0
        while not state.converged():
            run += 1
            samples = yield self.schedule(ctx, suite=suite, run=run, phase=phase)
            # samples is None if the Runner can't send (StopIteration on first
            # next()); treat as empty.
            state.observe(run, samples or ())

    # ----- execution materialization ----------------------------------

    def schedule(
        self,
        ctx: Any,
        *,
        suite: str,
        run: int,
        phase: Phase,
    ) -> ScheduledExecution:
        """Materialize one ScheduledExecution for ``(suite, run, phase)``.

        Resolves dynamic command/cwd/env callables against ``ctx`` and stamps
        any matrix info attached to the benchmark.
        """
        cmd = self.command(self, ctx) if callable(self.command) else self.command
        cwd = self.cwd(self, ctx) if callable(self.cwd) else self.cwd
        env = self.env(self, ctx) if callable(self.env) else self.env
        # Variant labels are attached by Suite.matrix under the reserved
        # ``__info__`` key in ``data``.
        info: tuple[tuple[str, str], ...] = ()
        if self.data and "__info__" in self.data:
            info = tuple(self.data["__info__"])
        return ScheduledExecution(
            execution=Execution(
                command=tuple(cmd),
                cwd=Path(cwd),
                env=env or _EMPTY_ENV,
                timeout=self.timeout,
            ),
            suite=suite,
            benchmark=self.name,
            info=info,
            run=run,
            phase=phase,
        )


def _coerce_policy(p: StoppingPolicy | int) -> StoppingPolicy:
    return p if isinstance(p, StoppingPolicy) else FixedRuns(p)


# ---------------------------------------------------------------------------
# bench(): shorthand constructor
# ---------------------------------------------------------------------------


def bench(name: str, **data: Any) -> Benchmark:
    """Build a Benchmark with arbitrary attached data.

    ``bench("zoo", path=Path("zoo.lox"))`` makes ``b.path`` available.
    """
    return Benchmark(name=name, data=dict(data) if data else _EMPTY_ENV)
