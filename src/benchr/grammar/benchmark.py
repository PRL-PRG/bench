"""Benchmark: a workload identity that expands into one or more variants.

A ``Benchmark`` names a workload (a program, a file to compress, a regex).
It carries:

  - ``command``/``cwd``/``env`` describing how to invoke the workload (these
    may be variant-aware callables);
  - a list of *matrix axes* (``.with_matrix(vm=[...], size=[...])``) — the
    cartesian product of axes produces the *variants* of this benchmark;
  - optional *skip* rules to drop specific cells;
  - an optional ``label_fn`` that controls how each variant prints.

Within a benchmark, variants are what get compared in the end-of-run Summary —
comparison across different benchmarks is meaningless (apples / oranges) and
is therefore never emitted.

A ``Benchmark`` is a frozen value object. To run one variant you call
``.compile(ctx)`` which returns a generator coroutine; the Runner pumps the
coroutine by ``send()``-ing parsed samples back so stopping policies can
observe and decide whether to continue. ``.expand(ctx)`` produces one
*concrete* Benchmark per surviving variant (axis values stamped into
``data`` so user callables can read ``b.vm``, ``b.size`` etc.).

Two policies live on a Benchmark: ``warmup`` (samples reported, not fed to
the measure policy) and ``measure`` (samples reported and fed to the policy
that controls how many more measure runs to take). Both default to no-op:
``warmup = FixedRuns(0)``, ``measure = FixedRuns(1)``.
"""

from __future__ import annotations

import dataclasses
import itertools
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Generator, Iterator, Mapping, Sequence

from benchr.grammar.execution import (
    _EMPTY_MAPPING,
    Execution,
    Phase,
    ScheduledExecution,
    SuccessFn,
    format_variant,
)
from benchr.grammar.policy import FixedRuns, StoppingPolicy
from benchr.grammar.processor import Processor
from benchr.report.sample import Sample


# A user-supplied command/cwd/env builder. Receives the variant-stamped
# benchmark and the ctx (the typed RunContext dataclass).
type CommandFn = Callable[["Benchmark", Any], Sequence[str]]
type PathFn = Callable[["Benchmark", Any], Path]
type EnvFn = Callable[["Benchmark", Any], Mapping[str, str]]

# A label function turns a variant-stamped benchmark into the human-readable
# variant identifier shown in reports (e.g. ``"sleep 0.05"``).
type LabelFn = Callable[["Benchmark"], str]

# A skip predicate. Returning truthy drops the variant. Predicate receives
# the variant-stamped benchmark so it can read ``b.vm``, ``b.size``, etc.
type SkipFn = Callable[["Benchmark"], bool]


_VARIANT_KEY = "__variant__"


@dataclass(frozen=True, slots=True)
class SkipRule:
    """One skip rule: kwargs (AND-matched) and/or a predicate.

    A variant is dropped if *any* of its rules match. Within a single rule,
    all kwargs must match AND the predicate (if set) must return truthy.
    """

    kwargs: Mapping[str, Any] = _EMPTY_MAPPING
    predicate: SkipFn | None = None

    def matches(self, b: "Benchmark") -> bool:
        for k, v in self.kwargs.items():
            if getattr(b, k, _MISSING) != v:
                return False
        if self.predicate is not None and not self.predicate(b):
            return False
        return True


_MISSING = object()


@dataclass(frozen=True, slots=True)
class Benchmark:
    """A named, frozen benchmark.

    ``data`` holds arbitrary user-supplied keyword args (e.g. ``path=...`` for
    a file-discovered benchmark). Access them as attributes on ``benchmark``:
    ``benchmark.path``, ``benchmark.size``. The ``__getattr__`` hook below
    forwards unknown attribute reads into ``data``.

    Keys starting with ``__`` are reserved (currently ``__variant__``, used to
    stamp the current matrix cell onto each expanded benchmark).
    """

    name: str

    # Either a static list[str] command, or a callable (benchmark, ctx) -> [str].
    command: Sequence[str] | CommandFn | None = None
    cwd: Path | PathFn | None = None
    env: Mapping[str, str] | EnvFn = _EMPTY_MAPPING
    timeout: float | None = None

    processors: tuple[Processor, ...] = ()

    # Optional success policy: returns a failure reason (str) or None for
    # success. Defaults to the Runner's ``default_success`` when unset.
    success: SuccessFn | None = None

    warmup: StoppingPolicy = FixedRuns(0)
    measure: StoppingPolicy = FixedRuns(1)

    # User payload; accessible as benchmark.<key>.
    data: Mapping[str, Any] = _EMPTY_MAPPING

    # Matrix axes (insertion order). Cartesian product across axes produces
    # this benchmark's variants. Empty tuple = one implicit variant.
    axes: tuple[tuple[str, tuple[Any, ...]], ...] = ()

    # Skip rules; a variant is dropped if any rule matches it.
    skips: tuple[SkipRule, ...] = ()

    # Optional variant-label override.
    label_fn: LabelFn | None = None

    # ----- attribute access into data ---------------------------------

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only called when normal lookup fails; safe to use even
        # with slots.
        data = object.__getattribute__(self, "data")
        if name in data:
            return data[name]
        raise AttributeError(name)

    # ----- with_* methods ---------------------------------------------

    def with_command(self, command: Sequence[str] | CommandFn) -> Benchmark:
        return dataclasses.replace(self, command=command)

    def with_cwd(self, cwd: Path | PathFn) -> Benchmark:
        return dataclasses.replace(self, cwd=cwd)

    def with_env(self, env: Mapping[str, str] | EnvFn) -> Benchmark:
        return dataclasses.replace(self, env=env)

    def with_timeout(self, timeout: float) -> Benchmark:
        return dataclasses.replace(self, timeout=timeout)

    def with_process(self, *processors: Processor) -> Benchmark:
        """Attach processors, replacing any already set — pass them all in one
        call (``with_process(p1, p2, …)``); calling again does not append."""
        return dataclasses.replace(self, processors=processors)

    def with_success(self, fn: SuccessFn) -> Benchmark:
        """Override the success policy (returns a failure reason, or None)."""
        return dataclasses.replace(self, success=fn)

    def with_warmup(self, p: StoppingPolicy | int) -> Benchmark:
        return dataclasses.replace(self, warmup=_coerce_policy(p))

    def with_measure(self, p: StoppingPolicy | int) -> Benchmark:
        return dataclasses.replace(self, measure=_coerce_policy(p))

    def runs(self, n: int) -> Benchmark:
        """Sugar for ``.with_measure(FixedRuns(n))``."""
        return self.with_measure(FixedRuns(n))

    # ----- matrix / skip / label --------------------------------------

    def with_matrix(self, **axes: Sequence[Any]) -> Benchmark:
        """Add one matrix axis per kwarg. Repeated calls compose as cartesian product.

        ``b.with_matrix(vm=["v8", "jsc"])`` adds a ``vm`` axis with two
        values. A second call ``.with_matrix(size=[100, 500])`` adds a ``size``
        axis; the benchmark now has 4 variants. Axis values are arbitrary;
        callables (``with_command``, ``with_skip``) read them as attributes on
        the variant-stamped benchmark (``b.vm``, ``b.size``).
        """
        new_axes = list(self.axes)
        existing = {name for name, _ in new_axes}
        for name, values in axes.items():
            if name in existing:
                raise ValueError(f"Benchmark {self.name!r}: axis {name!r} already declared")
            if name.startswith("_"):
                raise ValueError(f"Axis name {name!r} cannot start with '_'")
            new_axes.append((name, tuple(values)))
        return dataclasses.replace(self, axes=tuple(new_axes))

    def with_skip(
        self,
        predicate: SkipFn | None = None,
        /,
        **kwargs: Any,
    ) -> Benchmark:
        """Drop variants matching the given rule.

        Two interchangeable styles, combinable in one call:

          ``.with_skip(vm="v8", size=500)``    — drop variants where every
                                                 named axis equals the given value
          ``.with_skip(lambda b: b.vm != "jsc")`` — drop variants where the
                                                 predicate returns truthy

        Multiple ``.with_skip(...)`` calls compose as OR (any rule may drop a
        variant).
        """
        if predicate is None and not kwargs:
            return self
        rule = SkipRule(kwargs=MappingProxyType(dict(kwargs)) if kwargs else _EMPTY_MAPPING,
                        predicate=predicate)
        return dataclasses.replace(self, skips=self.skips + (rule,))

    def with_label(self, fn: LabelFn) -> Benchmark:
        """Override how each variant's label renders in reports.

        ``fn`` receives the variant-stamped benchmark, e.g.
        ``with_label(lambda b: " ".join(b.command))``.
        """
        return dataclasses.replace(self, label_fn=fn)

    # ----- expansion --------------------------------------------------

    def expand(self) -> Iterator[Benchmark]:
        """Yield one concrete Benchmark per surviving cell of the matrix.

        Each yielded benchmark has its axis values stamped into ``data`` (so
        ``b.vm``, ``b.size`` resolve), has its ``axes`` cleared, and carries
        a canonical ``variant`` tuple via ``self.variant()``. Defaults for
        ``command``/``cwd``/``env`` kick in here if an axis is named
        ``command``/``cwd``/``env`` and no explicit callable was supplied.
        """
        if not self.axes:
            yield self
            return

        axis_names = [n for n, _ in self.axes]
        axis_values = [vs for _, vs in self.axes]
        for combo in itertools.product(*axis_values):
            variant_dict: dict[str, Any] = dict(zip(axis_names, combo))
            stamped_data = dict(self.data) if self.data else {}
            stamped_data.update(variant_dict)
            canonical = tuple(sorted(
                ((k, _stringify(v)) for k, v in variant_dict.items())
            ))
            stamped_data[_VARIANT_KEY] = canonical
            variant = dataclasses.replace(self, data=stamped_data, axes=())

            # Apply built-in axis defaults if the user didn't override.
            if "command" in variant_dict and not self._has_explicit("command"):
                variant = variant.with_command(_axis_command)
            if "cwd" in variant_dict and not self._has_explicit("cwd"):
                variant = variant.with_cwd(_axis_cwd)
            if "env" in variant_dict and self.env is _EMPTY_MAPPING:
                variant = variant.with_env(_axis_env)

            if any(rule.matches(variant) for rule in self.skips):
                continue
            yield variant

    def _has_explicit(self, attr: str) -> bool:
        v = getattr(self, attr)
        if attr == "env":
            return v is not _EMPTY_MAPPING
        return v is not None

    def variant(self) -> tuple[tuple[str, str], ...]:
        """Canonical variant tuple stamped at expansion time (empty if none)."""
        if self.data and _VARIANT_KEY in self.data:
            return tuple(self.data[_VARIANT_KEY])
        return ()

    def variant_label(self) -> str:
        """Variant label for reports. ``label_fn(self)`` if set, else default."""
        if self.label_fn is not None:
            return self.label_fn(self)
        v = self.variant()
        return format_variant(v).strip(" ()")

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

        Failure handling: a failed run produces no samples (the Runner judges
        success via ``default_success`` / ``with_success`` and skips
        ``process()`` on failure), but the policy still observes it with an
        empty list, so every run counts.
        """
        if self.axes:
            raise ValueError(
                f"Benchmark {self.name!r} still has unexpanded matrix axes "
                f"{[n for n, _ in self.axes]}; call .expand() first"
            )
        if self.command is None:
            raise ValueError(f"Benchmark {self.name!r} has no command")
        if self.cwd is None:
            raise ValueError(f"Benchmark {self.name!r} has no cwd")
        if not self.processors:
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
        the current variant onto the result.
        """
        cmd = self.command(self, ctx) if callable(self.command) else self.command
        if cmd is None:
            raise ValueError(f"Benchmark {self.name!r} has no command")
        cwd = self.cwd(self, ctx) if callable(self.cwd) else self.cwd
        if cwd is None:
            raise ValueError(f"Benchmark {self.name!r} has no cwd")
        env = self.env(self, ctx) if callable(self.env) else self.env
        variant = self.variant()
        return ScheduledExecution(
            execution=Execution(
                command=tuple(cmd),
                cwd=Path(cwd),
                env=env or _EMPTY_MAPPING,
                timeout=self.timeout,
            ),
            suite=suite,
            benchmark=self.name,
            variant=variant,
            variant_label=self.variant_label(),
            run=run,
            phase=phase,
        )


def _axis_command(b: Benchmark, _ctx: Any) -> Sequence[str]:
    return list(b.data["command"])


def _axis_cwd(b: Benchmark, _ctx: Any) -> Path:
    return Path(b.data["cwd"])


def _axis_env(b: Benchmark, _ctx: Any) -> Mapping[str, str]:
    return dict(b.data["env"])


def _stringify(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return " ".join(str(x) for x in v)
    return str(v)


def _coerce_policy(p: StoppingPolicy | int) -> StoppingPolicy:
    return p if isinstance(p, StoppingPolicy) else FixedRuns(p)


# ---------------------------------------------------------------------------
# bench(): shorthand constructor
# ---------------------------------------------------------------------------


def bench(name: str, **data: Any) -> Benchmark:
    """Build a Benchmark with arbitrary attached data.

    ``bench("zoo", path=Path("zoo.lox"))`` makes ``b.path`` available. To add
    matrix axes use ``.with_matrix(...)``.
    """
    return Benchmark(name=name, data=dict(data) if data else _EMPTY_MAPPING)
