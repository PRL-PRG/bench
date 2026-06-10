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
the runs policy) and ``runs`` (samples reported and fed to the policy that
controls how many more measured runs to take).

Unset fields hold *null objects* (``UNSET_COMMAND``, ``UNSET_POLICY``, …)
meaning "inherit the suite's default". ``Suite.materialize()`` replaces every
null with the suite's value, so a materialized Benchmark is fully concrete;
using an unresolved field raises instead of guessing.
"""

from __future__ import annotations

import dataclasses
import itertools
import re
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
    UNSET_SUCCESS,
    format_variant,
)
from benchr.grammar.metric import Metric
from benchr.grammar.policy import UNSET_POLICY, FixedRuns, StoppingPolicy
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


# ---------------------------------------------------------------------------
# Command / Cwd / Env: one concrete wrapper type per invocation field.
#
# Each wrapper normalizes the two user spellings — a static value or a
# ``(benchmark, ctx) -> value`` callable — at ``with_*`` time, so the
# dataclass field has a single type and ``schedule()`` never branches on
# ``callable()``. The ``UNSET_*`` singletons are null objects meaning
# "inherit the suite's default"; ``Suite.materialize()`` resolves them away,
# and calling one raises instead of silently misbehaving.
# ---------------------------------------------------------------------------


class Command:
    """Resolves one variant's argv: ``command(benchmark, ctx) -> tuple[str, ...]``."""

    __slots__ = ("_fn",)

    def __init__(self, command: Sequence[str] | CommandFn) -> None:
        if callable(command):
            self._fn = command
        else:
            static = tuple(command)
            self._fn = lambda b, ctx: static

    def __call__(self, b: Benchmark, ctx: Any) -> tuple[str, ...]:
        return tuple(self._fn(b, ctx))


class _UnsetCommand(Command):
    __slots__ = ()

    def __init__(self) -> None:  # no fn — calling it IS the error
        pass

    def __call__(self, b: Benchmark, ctx: Any) -> tuple[str, ...]:
        raise ValueError(f"Benchmark {b.name!r} has no command")

    def __repr__(self) -> str:
        return "UNSET_COMMAND"


UNSET_COMMAND: Command = _UnsetCommand()


class Cwd:
    """Resolves one variant's working directory: ``cwd(benchmark, ctx) -> Path``."""

    __slots__ = ("_fn",)

    def __init__(self, cwd: str | Path | PathFn) -> None:
        if callable(cwd):
            self._fn = cwd
        else:
            static = Path(cwd)
            self._fn = lambda b, ctx: static

    def __call__(self, b: Benchmark, ctx: Any) -> Path:
        return Path(self._fn(b, ctx))


class _UnsetCwd(Cwd):
    __slots__ = ()

    def __init__(self) -> None:
        pass

    def __call__(self, b: Benchmark, ctx: Any) -> Path:
        raise RuntimeError(
            f"Benchmark {b.name!r}: cwd is unset — resolve via Suite.materialize()"
        )

    def __repr__(self) -> str:
        return "UNSET_CWD"


UNSET_CWD: Cwd = _UnsetCwd()

# The suite-level default: the invoking process's cwd, read at schedule time.
DEFAULT_CWD: Cwd = Cwd(lambda b, ctx: Path.cwd())


class Env:
    """Resolves one variant's environment: ``env(benchmark, ctx) -> Mapping``."""

    __slots__ = ("_fn",)

    def __init__(self, env: Mapping[str, str] | EnvFn) -> None:
        if callable(env):
            self._fn = env
        else:
            static = MappingProxyType(dict(env))
            self._fn = lambda b, ctx: static

    def __call__(self, b: Benchmark, ctx: Any) -> Mapping[str, str]:
        return self._fn(b, ctx)

    def merge(self, override: Env) -> Env:
        """Lazy per-key merge: self first, ``override`` wins (suite ⊕ benchmark)."""
        return Env(lambda b, ctx: {**self(b, ctx), **override(b, ctx)})


class _UnsetEnv(Env):
    __slots__ = ()

    def __init__(self) -> None:
        pass

    def __call__(self, b: Benchmark, ctx: Any) -> Mapping[str, str]:
        raise RuntimeError(
            f"Benchmark {b.name!r}: env is unset — resolve via Suite.materialize()"
        )

    def __repr__(self) -> str:
        return "UNSET_ENV"


UNSET_ENV: Env = _UnsetEnv()

# The suite-level default: empty — the child inherits the OS environment.
EMPTY_ENV: Env = Env({})


def default_label(b: Benchmark) -> str:
    """Default variant label: the formatted ``(k=v, …)`` tuple, no parens."""
    return format_variant(b.variant()).strip(" ()")


def _unset_label(b: Benchmark) -> str:
    raise RuntimeError(
        f"Benchmark {b.name!r}: label is unset — resolve via Suite.materialize()"
    )


# Null object for Benchmark.label_fn: "inherit the suite's label function".
UNSET_LABEL: LabelFn = _unset_label


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

    # How to invoke the workload. One concrete wrapper type per field; the
    # UNSET_* null objects mean "inherit the suite's default" and are
    # resolved away by Suite.materialize().
    command: Command = UNSET_COMMAND
    cwd: Cwd = UNSET_CWD
    env: Env = UNSET_ENV
    timeout: float | None = None  # None = no timeout (and inheritable)
    stdin: bytes | None = None  # None = no stdin (never inherited)

    metrics: tuple[Metric, ...] = ()  # () = inherit the suite's metrics

    # Success policy: returns a failure reason (str) or None for success.
    success: SuccessFn = UNSET_SUCCESS

    # Stopping policies (see module docstring).
    warmup: StoppingPolicy = UNSET_POLICY
    runs: StoppingPolicy = UNSET_POLICY

    # User payload; accessible as benchmark.<key>.
    data: Mapping[str, Any] = _EMPTY_MAPPING

    # Matrix axes (insertion order). Cartesian product across axes produces
    # this benchmark's variants. Empty tuple = one implicit variant.
    axes: tuple[tuple[str, tuple[Any, ...]], ...] = ()

    # Skip rules; a variant is dropped if any rule matches it.
    skips: tuple[SkipRule, ...] = ()

    # Variant-label function; UNSET_LABEL = inherit the suite's.
    label_fn: LabelFn = UNSET_LABEL

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
        return dataclasses.replace(self, command=Command(command))

    def with_cwd(self, cwd: str | Path | PathFn) -> Benchmark:
        return dataclasses.replace(self, cwd=Cwd(cwd))

    def with_env(self, env: Mapping[str, str] | EnvFn) -> Benchmark:
        return dataclasses.replace(self, env=Env(env))

    def with_timeout(self, timeout: float) -> Benchmark:
        return dataclasses.replace(self, timeout=timeout)

    def with_stdin(self, data: bytes | str) -> Benchmark:
        """Feed ``data`` to the process's stdin (str is UTF-8 encoded)."""
        return dataclasses.replace(
            self, stdin=data.encode() if isinstance(data, str) else data)

    def with_metric(self, *metrics: Metric) -> Benchmark:
        """Set (replace) the benchmark's metrics. Pass all of them in one call
        (``with_metric(m1, m2, …)``); a later call replaces an earlier one."""
        return dataclasses.replace(self, metrics=metrics)

    def with_success(self, fn: SuccessFn) -> Benchmark:
        """Override the success policy (returns a failure reason, or None)."""
        return dataclasses.replace(self, success=fn)

    def with_warmup(self, p: StoppingPolicy | int) -> Benchmark:
        return dataclasses.replace(self, warmup=_coerce_policy(p))

    def with_runs(self, p: StoppingPolicy | int) -> Benchmark:
        return dataclasses.replace(self, runs=_coerce_policy(p))

    # ----- matrix / skip / label --------------------------------------

    def with_matrix(self, **axes: Sequence[Any]) -> Benchmark:
        """Declare the matrix axes (replaces any previously set).

        Pass every axis in one call: ``b.with_matrix(vm=["v8", "jsc"], size=[100,
        500])`` gives 4 variants (the cartesian product). Axis values are
        arbitrary; callables (``with_command``, ``add_matrix_skip``) read them as
        attributes on the variant-stamped benchmark (``b.vm``, ``b.size``).
        """
        for name in axes:
            if name.startswith("_"):
                raise ValueError(f"Axis name {name!r} cannot start with '_'")
        return dataclasses.replace(
            self, axes=tuple((name, tuple(values)) for name, values in axes.items())
        )

    def add_matrix_skip(
        self,
        predicate: SkipFn | None = None,
        /,
        **kwargs: Any,
    ) -> Benchmark:
        """Add a rule that drops variants.

        Two interchangeable styles, combinable in one call:

          ``.add_matrix_skip(vm="v8", size=500)``    — drop variants where every
                                                 named axis equals the given value
          ``.add_matrix_skip(lambda b: b.vm != "jsc")`` — drop variants where the
                                                 predicate returns truthy

        Multiple ``.add_matrix_skip(...)`` calls compose as OR (any rule may drop a
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
            if "command" in variant_dict and self.command is UNSET_COMMAND:
                variant = variant.with_command(_axis_command)
            if "cwd" in variant_dict and self.cwd is UNSET_CWD:
                variant = variant.with_cwd(_axis_cwd)
            if "env" in variant_dict and self.env is UNSET_ENV:
                variant = variant.with_env(_axis_env)

            if any(rule.matches(variant) for rule in self.skips):
                continue
            yield variant

    def variant(self) -> tuple[tuple[str, str], ...]:
        """Canonical variant tuple stamped at expansion time (empty if none)."""
        if self.data and _VARIANT_KEY in self.data:
            return tuple(self.data[_VARIANT_KEY])
        return ()

    def variant_label(self) -> str:
        """Variant label for reports: ``label_fn(self)`` (suite-filled)."""
        return self.label_fn(self)

    # ----- compile -----------------------------------------------------

    def compile(
        self,
        ctx: Any,
        *,
        suite: str = "",
    ) -> Generator[ScheduledExecution, list[Sample], None]:
        """Yield ScheduledExecutions; the Runner sends back parsed Samples.

        ``runs`` policy receives Samples; ``warmup`` policy does too, in case
        you want CoV-driven warmup (run until things stabilize, then start
        measuring). Samples emitted during warmup are tagged ``phase="warmup"``
        — formatters skip them by default but they appear in JSON/CSV/dir
        outputs.

        Failure handling: a failed run produces no samples (the Runner judges
        success via ``default_success`` / ``with_success`` and skips metric
        extraction on failure), but the policy still observes it with an
        empty list, so every run counts.
        """
        if self.axes:
            raise ValueError(
                f"Benchmark {self.name!r} still has unexpanded matrix axes "
                f"{[n for n, _ in self.axes]}; call .expand() first"
            )
        if self.command is UNSET_COMMAND:
            raise ValueError(f"Benchmark {self.name!r} has no command")

        yield from self._phase(ctx, suite, self.warmup, phase="warmup")
        yield from self._phase(ctx, suite, self.runs, phase="runs")

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
        cmd = self.command(self, ctx)
        cwd = self.cwd(self, ctx)
        env = self.env(self, ctx)
        variant = self.variant()
        return ScheduledExecution(
            execution=Execution(
                command=cmd,
                cwd=cwd,
                env=env if env else _EMPTY_MAPPING,
                timeout=self.timeout,
                stdin=self.stdin,
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


def from_files(
    root: str | Path,
    *,
    pattern: str | None = None,
    recursive: bool = True,
    exclude: set[str] | None = None,
) -> list[Benchmark]:
    """Discover files under ``root``; each becomes a Benchmark with ``b.path`` set.

    Returns the list eagerly — splat into ``suite(name, *from_files(...))``, or
    wrap in ``Suite.factory`` when the root depends on ctx
    (``.factory(lambda ctx: from_files(ctx.cwd / "benchmarks", pattern=...))``).
    Benchmark name is the path relative to ``root`` without extension
    (forward-slash separated). ``pattern`` is a regex matched against the
    filename via ``re.search``.
    """
    compiled = re.compile(pattern) if pattern else None
    exclude_set = exclude or set()
    r = Path(root)
    out: list[Benchmark] = []
    if r.is_dir():
        entries = (
            (Path(d) / fn for d, _, fns in r.walk() for fn in fns)
            if recursive
            else (c for c in r.iterdir() if c.is_file())
        )
        for fp in entries:
            if compiled and not compiled.search(fp.name):
                continue
            name = str(fp.relative_to(r).with_suffix(""))
            if name in exclude_set:
                continue
            out.append(bench(name, path=fp))
    elif r.is_file():
        if compiled is None or compiled.search(r.name):
            name = r.stem
            if name not in exclude_set:
                out.append(bench(name, path=r))
    else:
        raise FileNotFoundError(f"from_files root not found: {r}")
    return out
