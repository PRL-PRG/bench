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

A ``Benchmark`` is a frozen value object. To run one variant the Runner
drives ``benchmarking_loop`` (see ``benchr.core.loop``) and materializes
one ``ScheduledExecution`` per slot via ``.schedule()``; parsed samples are
fed back so stopping policies can observe and decide whether to continue.
``.expand(ctx)`` produces one *concrete* Benchmark per surviving variant
(axis values stamped into ``data`` so user callables can read ``b.vm``,
``b.size`` etc.).

Two policies live on a Benchmark: ``warmup`` (samples reported, not fed to
the runs policy) and ``runs`` (samples reported and fed to the policy that
controls how many more measured runs to take).

Every inheritable field defaults to the ``UNSET`` null object, meaning
"inherit the suite's default". ``Suite.materialize()`` replaces every UNSET
with the suite's value, so a materialized Benchmark is fully concrete; using
an unresolved field raises instead of guessing.
"""

from __future__ import annotations

import dataclasses
import itertools
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from benchr.core.execution import (
    EMPTY_MAPPING,
    Execution,
    ScheduledExecution,
    SuccessFn,
    format_variant,
)
from benchr.core.metric import Metric
from benchr.core.policy import StoppingPolicy, coerce_policy


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
# UNSET: the one null object meaning "inherit the suite's default".
#
# Every inheritable Benchmark field defaults to it; ``Suite.materialize()``
# swaps in the suite's value. Any use of an unresolved field — calling it,
# reading an attribute, truth-testing it — raises instead of guessing.
# ---------------------------------------------------------------------------

_UNSET_MSG = (
    "benchmark field is unset (it inherits the suite's default) — resolve "
    "the benchmark via Suite.materialize() before use"
)


class _Unset:
    __slots__ = ()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(_UNSET_MSG)

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(_UNSET_MSG)

    def __bool__(self) -> bool:
        raise RuntimeError(_UNSET_MSG)

    def __repr__(self) -> str:
        return "UNSET"


# Typed ``Any`` so fields keep their concrete declared types; misuse of an
# unresolved benchmark fails loudly at runtime in one place (above).
UNSET: Any = _Unset()


# ---------------------------------------------------------------------------
# Field normalizers: accept a static value or a ``(benchmark, ctx) -> value``
# callable, store a single callable shape so ``schedule()`` never branches.
# ---------------------------------------------------------------------------


def coerce_command(command: Sequence[str] | CommandFn) -> CommandFn:
    if callable(command):
        return command
    static = tuple(command)
    return lambda b, ctx: static


def coerce_cwd(cwd: str | Path | PathFn) -> PathFn:
    if callable(cwd):
        return cwd
    static = Path(cwd)
    return lambda b, ctx: static


def coerce_env(env: Mapping[str, str] | EnvFn) -> EnvFn:
    if callable(env):
        return env
    static = MappingProxyType(dict(env))
    return lambda b, ctx: static


def default_label(b: Benchmark) -> str:
    """Default variant label: the formatted ``(k=v, …)`` tuple, no parens."""
    return format_variant(b.variant()).strip(" ()")


_VARIANT_KEY = "__variant__"


@dataclass(frozen=True, slots=True)
class SkipRule:
    """One skip rule: kwargs (AND-matched) and/or a predicate.

    A variant is dropped if *any* of its rules match. Within a single rule,
    all kwargs must match AND the predicate (if set) must return truthy.
    """

    kwargs: Mapping[str, Any] = EMPTY_MAPPING
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

    # Every inheritable field defaults to UNSET ("inherit the suite's
    # default") and is resolved away by Suite.materialize(). ``stdin`` is the
    # exception: it is never inherited.
    command: CommandFn = UNSET
    cwd: PathFn = UNSET
    env: EnvFn = UNSET
    timeout: float | None = UNSET
    stdin: bytes | None = None  # None = no stdin (never inherited)

    metrics: tuple[Metric, ...] = UNSET

    # Success policy: returns a failure reason (str) or None for success.
    success: SuccessFn = UNSET

    # Stopping policies (see module docstring).
    warmup: StoppingPolicy = UNSET
    runs: StoppingPolicy = UNSET

    # Harness benchmarks execute the command ONCE; the harness itself runs
    # all iterations and the metrics parse them from the complete output.
    harness: bool = UNSET

    # User payload; accessible as benchmark.<key>.
    data: Mapping[str, Any] = EMPTY_MAPPING

    # Matrix axes (insertion order). Cartesian product across axes produces
    # this benchmark's variants. Empty tuple = one implicit variant.
    axes: tuple[tuple[str, tuple[Any, ...]], ...] = ()

    # Skip rules; a variant is dropped if any rule matches it.
    skips: tuple[SkipRule, ...] = ()

    # Variant-label function turning the variant-stamped benchmark into the
    # label shown in reports.
    label_fn: LabelFn = UNSET

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
        return dataclasses.replace(self, command=coerce_command(command))

    def with_cwd(self, cwd: str | Path | PathFn) -> Benchmark:
        return dataclasses.replace(self, cwd=coerce_cwd(cwd))

    def with_env(self, env: Mapping[str, str] | EnvFn) -> Benchmark:
        return dataclasses.replace(self, env=coerce_env(env))

    def with_timeout(self, timeout: float | None) -> Benchmark:
        """Set the per-run timeout in seconds (``None`` = explicitly no
        timeout, overriding any suite default)."""
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
        return dataclasses.replace(self, warmup=coerce_policy(p))

    def with_runs(self, p: StoppingPolicy | int) -> Benchmark:
        return dataclasses.replace(self, runs=coerce_policy(p))

    def with_harness(self) -> Benchmark:
        """Mark this benchmark as a *harness*: the command is executed once
        and runs all iterations itself — derive the count in the command fn,
        e.g. ``b.warmup.max_runs() + b.runs.max_runs()``. Metrics parse the
        complete output (one sample per iteration); each iteration becomes
        one run record, the first ``warmup`` of them discarded by stats.

        Requires bounded warmup/runs policies (no CoV — the runner cannot
        stop a harness mid-flight). ``timeout`` covers the whole process; the
        output is parsed only after it exits (no live streaming)."""
        return dataclasses.replace(self, harness=True)

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
        rule = SkipRule(kwargs=MappingProxyType(dict(kwargs)) if kwargs else EMPTY_MAPPING,
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
            if "command" in variant_dict and self.command is UNSET:
                variant = variant.with_command(_axis_command)
            if "cwd" in variant_dict and self.cwd is UNSET:
                variant = variant.with_cwd(_axis_cwd)
            if "env" in variant_dict and self.env is UNSET:
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

    # ----- execution materialization ----------------------------------

    def schedule(
        self,
        ctx: Any,
        *,
        suite: str,
        run: int,
    ) -> ScheduledExecution:
        """Materialize one ScheduledExecution for ``(suite, run)``.

        Resolves dynamic command/cwd/env callables against ``ctx`` and stamps
        the current variant onto the result.
        """
        if self.axes:
            raise ValueError(
                f"Benchmark {self.name!r} still has unexpanded matrix axes "
                f"{[n for n, _ in self.axes]}; call .expand() first"
            )
        cmd = tuple(self.command(self, ctx))
        cwd = Path(self.cwd(self, ctx))
        env = self.env(self, ctx)
        variant = self.variant()
        return ScheduledExecution(
            execution=Execution(
                command=cmd,
                cwd=cwd,
                env=env if env else EMPTY_MAPPING,
                timeout=self.timeout,
                stdin=self.stdin,
            ),
            suite=suite,
            benchmark=self.name,
            variant=variant,
            variant_label=self.variant_label(),
            run=run,
        )


def _axis_command(b: Benchmark, _ctx: Any) -> Sequence[str]:
    return list(b.data["command"])


def _axis_cwd(b: Benchmark, _ctx: Any) -> Path:
    return Path(b.data["cwd"])


def _axis_env(b: Benchmark, _ctx: Any) -> Mapping[str, str]:
    return dict(b.data["env"])


def _stringify(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return " ".join(str(x) for x in cast("Sequence[object]", v))
    return str(v)


# ---------------------------------------------------------------------------
# bench(): shorthand constructor
# ---------------------------------------------------------------------------


def bench(name: str, **data: Any) -> Benchmark:
    """Build a Benchmark with arbitrary attached data.

    ``bench("zoo", path=Path("zoo.lox"))`` makes ``b.path`` available. To add
    matrix axes use ``.with_matrix(...)``.
    """
    return Benchmark(name=name, data=dict(data) if data else EMPTY_MAPPING)


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
