"""Suite: a named collection of Benchmarks plus the defaults they inherit.

A ``Suite`` is a frozen value object. It stores *defaults* (command, env,
policies, metrics, …) next to its member benchmarks; calling a ``.with_*``
method just sets the suite field — nothing propagates eagerly. Resolution
happens once, in ``materialize(ctx)``: every benchmark field still holding
``UNSET`` is filled from the suite, so builder-call order never matters —
``Suite("A").with_command(c).add(b)`` equals
``Suite("A").add(b).with_command(c)``.

Suite defaults are always concrete (except ``command``, which has no sensible
default); Benchmark fields are all UNSET-able.

Resolution precedence (most specific wins):

    benchmark explicit > benchmark axis default > suite default

(The CLI's ``--runs/--warmup`` override is applied later, by ``benchr.run()``,
on the planned benchmark list — not by the Suite.)

Producers:
  ``.add(b)`` / ``.add_all(*bs)``      append benchmarks
  ``.factory(fn)``                     defer ``(ctx) -> [Benchmark]`` production
                                       (wrap the ``from_files`` helper for
                                       ctx-dependent discovery)
  ``.with_command/.with_cwd/.with_env/.with_timeout/.with_metric/``
  ``.with_success/.with_label``        set a suite default
  ``.with_warmup/.with_runs``          set a default warmup/runs policy
  ``.with_harness()``                  make every benchmark a harness benchmark
  ``.with_matrix(**axes)``             add axes to every benchmark (at materialize)
  ``.add_matrix_skip(...)``            add a skip rule to every benchmark
  ``.filter(pred)``                    keep matching benchmarks (eager — the one
                                       order-dependent builder; add before filtering)
  ``.with_name(new_name)``             rename
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from benchr.grammar.benchmark import (
    UNSET,
    Benchmark,
    CommandFn,
    EnvFn,
    LabelFn,
    PathFn,
    SkipFn,
    SkipRule,
    coerce_command,
    coerce_cwd,
    coerce_env,
    default_label,
)
from benchr.grammar.context import Context, Matrix
from benchr.core.execution import (
    EMPTY_MAPPING,
    SuccessFn,
    default_success,
)
from benchr.core.metric import Metric, Time
from benchr.core.policy import FixedRuns, StoppingPolicy, coerce_policy


# A function the Runner can call to materialize benchmarks given the Context.
# Suite.from_files defers discovery to this hook so e.g. paths can depend on
# ctx.params. The Runner / CLI flattens these into concrete benchmarks at run time.
type BenchFactory = Callable[[Context[Any]], list[Benchmark]]


def _default_cwd(ctx: Context[Any]) -> Path:
    """Suite default cwd: the invoking process's cwd, read at schedule time."""
    return Path.cwd()


def _default_env(ctx: Context[Any]) -> Mapping[str, str]:
    """Suite default env: empty — the child inherits the OS environment."""
    return EMPTY_MAPPING


def _merge_env(base: EnvFn, override: EnvFn) -> EnvFn:
    """Lazy per-key merge: ``base`` first, ``override`` wins (suite ⊕ benchmark)."""
    return lambda ctx: {**base(ctx), **override(ctx)}


# TODO: is it better to use tuple[X, ...] instead of list[X] ?


@dataclass(frozen=True, slots=True)
class Suite:
    """A named, frozen collection of benchmarks, factories, and defaults."""

    name: str = ""
    benchmarks: tuple[Benchmark, ...] = ()
    factories: tuple[BenchFactory, ...] = ()

    # ----- suite defaults (always concrete, except command) ----------
    command: CommandFn = UNSET  # no sensible default; checked at materialize
    cwd: PathFn = _default_cwd
    env: EnvFn = _default_env
    timeout: float | None = None  # None = no timeout
    metrics: tuple[Metric, ...] = (Time(),)
    success: SuccessFn = default_success
    warmup: StoppingPolicy = FixedRuns(0)
    runs: StoppingPolicy = FixedRuns(1)
    harness: bool = False
    label_fn: LabelFn = default_label

    # ----- suite-level matrix / skip (applied at materialize) --------
    axes: tuple[tuple[str, tuple[Any, ...]], ...] = ()
    skips: tuple[SkipRule, ...] = ()

    # ----- producers -------------------------------------------------

    def with_name(self, name: str) -> Suite:
        return dataclasses.replace(self, name=name)

    def add(self, b: Benchmark) -> Suite:
        return dataclasses.replace(self, benchmarks=self.benchmarks + (b,))

    def add_all(self, *bs: Benchmark) -> Suite:
        return dataclasses.replace(self, benchmarks=self.benchmarks + tuple(bs))

    def factory(self, fn: BenchFactory) -> Suite:
        """Register a deferred ``(ctx: Context) -> [Benchmark]`` producer;
        ``materialize`` builds the suite-level Context and calls it. Wrap
        ``from_files`` here for params-dependent discovery, e.g.
        ``.factory(lambda ctx: from_files(ctx.params.cwd / "benchmarks", pattern=...))``."""
        return dataclasses.replace(self, factories=self.factories + (fn,))

    def filter(self, pred: Callable[[Benchmark], bool]) -> Suite:
        """Keep only benchmarks for which ``pred(b)`` is truthy. Wraps any
        deferred factories so the filter also applies post-discovery.

        Note: filtering is eager over the benchmarks present now — benchmarks
        ``add``-ed afterwards are not filtered.
        """
        kept = tuple(b for b in self.benchmarks if pred(b))
        new_factories = tuple(Suite._wrap_filter(fn, pred) for fn in self.factories)
        return dataclasses.replace(self, benchmarks=kept, factories=new_factories)

    # ----- defaults ---------------------------------------------------

    def with_command(self, command: Sequence[str] | CommandFn) -> Suite:
        return dataclasses.replace(self, command=coerce_command(command))

    def with_cwd(self, cwd: str | Path | PathFn) -> Suite:
        return dataclasses.replace(self, cwd=coerce_cwd(cwd))

    def with_env(self, env: Mapping[str, str] | EnvFn) -> Suite:
        """Set the suite env. At materialize it merges *under* each
        benchmark's own env — the benchmark wins per key."""
        return dataclasses.replace(self, env=coerce_env(env))

    def with_timeout(self, timeout: float | None) -> Suite:
        return dataclasses.replace(self, timeout=timeout)

    def with_metric(self, *metrics: Metric) -> Suite:
        """Set (replace) the suite's default metrics — initially ``(Time(),)``."""
        return dataclasses.replace(self, metrics=tuple(metrics))

    def with_success(self, fn: SuccessFn) -> Suite:
        return dataclasses.replace(self, success=fn)

    def with_label(self, fn: LabelFn) -> Suite:
        return dataclasses.replace(self, label_fn=fn)

    def with_warmup(self, p: StoppingPolicy | int) -> Suite:
        """Set the default warmup policy."""
        return dataclasses.replace(self, warmup=coerce_policy(p))

    def with_runs(self, p: StoppingPolicy | int) -> Suite:
        """Set the default policy for the measured runs."""
        return dataclasses.replace(self, runs=coerce_policy(p))

    def with_harness(self) -> Suite:
        """Make every contained benchmark a harness benchmark (executed once,
        iterations parsed from the complete output — see
        ``Benchmark.with_harness``). There is no per-benchmark opt-out; mixed
        suites are two suites."""
        return dataclasses.replace(self, harness=True)

    def with_matrix(self, **axes: Sequence[Any]) -> Suite:
        """Declare matrix axes applied to every contained benchmark (replaces
        any previously set).

        Stored on the suite; ``materialize`` appends these axes to each
        benchmark's own (so per-benchmark axes still compose with suite-level
        ones). See ``Benchmark.with_matrix``.
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
    ) -> Suite:
        """Add a skip rule applied to every contained benchmark.

        Same shape as ``Benchmark.add_matrix_skip``: kwargs are AND-matched against
        axis values, optional ``predicate(bench) -> bool`` for complex cases.
        """
        if predicate is None and not kwargs:
            return self
        rule = SkipRule(
            kwargs=MappingProxyType(dict(kwargs)) if kwargs else EMPTY_MAPPING,
            predicate=predicate,
        )
        return dataclasses.replace(self, skips=self.skips + (rule,))

    def materialize(self, params: Any) -> list[Benchmark]:
        """Return the concrete (post-expansion, fully resolved) benchmark list.

        Calls deferred factories (passing a suite-level ``Context`` built from
        ``params`` and the suite defaults), applies suite-level axes/skips,
        expands each benchmark's matrix into one Benchmark per surviving
        variant, and fills every still-unset field from the suite defaults.
        After this, benchmarks are fully concrete — runners just read fields.
        """
        ctx = Context(
            params=params,
            suite=self.name,
            benchmark=None,
            runs=self.runs,
            warmup=self.warmup,
            timeout=self.timeout,
            metrics=self.metrics,
            harness=self.harness,
            success=self.success,
            matrix=Matrix(),
        )
        collected = list(self.benchmarks)
        for f in self.factories:
            collected.extend(f(ctx))
        return [
            self._resolve(variant)
            for b in collected
            for variant in self._with_suite_matrix(b).expand()
        ]

    # ----- helpers --------------------------------------------------

    def _with_suite_matrix(self, b: Benchmark) -> Benchmark:
        """Append suite axes after the benchmark's own; union skip rules."""
        if self.axes:
            existing = {name for name, _ in b.axes}
            for name, _ in self.axes:
                if name in existing:
                    raise ValueError(
                        f"Benchmark {b.name!r}: axis {name!r} already declared"
                    )
            b = dataclasses.replace(b, axes=b.axes + self.axes)
        if self.skips:
            b = dataclasses.replace(b, skips=b.skips + self.skips)
        return b

    def _resolve(self, b: Benchmark) -> Benchmark:
        """Fill unset fields from the suite: explicit benchmark value wins,
        else suite default. ``env`` is the one merging field: the suite env
        sits under the benchmark's own, benchmark winning per key."""
        resolved = dataclasses.replace(
            b,
            command=self.command if b.command is UNSET else b.command,
            cwd=self.cwd if b.cwd is UNSET else b.cwd,
            env=self.env if b.env is UNSET else _merge_env(self.env, b.env),
            timeout=self.timeout if b.timeout is UNSET else b.timeout,
            metrics=self.metrics if b.metrics is UNSET else b.metrics,
            success=self.success if b.success is UNSET else b.success,
            warmup=self.warmup if b.warmup is UNSET else b.warmup,
            runs=self.runs if b.runs is UNSET else b.runs,
            harness=self.harness if b.harness is UNSET else b.harness,
            label_fn=self.label_fn if b.label_fn is UNSET else b.label_fn,
        )
        if resolved.command is UNSET:
            raise ValueError(
                f"Benchmark {b.name!r} has no command — set one with "
                f"Benchmark.with_command or Suite.with_command"
            )
        if resolved.harness and (
            resolved.warmup.max_runs() is None or resolved.runs.max_runs() is None
        ):
            raise ValueError(
                f"Benchmark {b.name!r} is a harness benchmark: it runs once "
                f"and cannot be stopped mid-flight, so warmup/runs must be "
                f"bounded counts (no CoefficientOfVariation) — pass them to "
                f"the harness via the command fn"
            )
        return resolved

    @staticmethod
    def _wrap_filter(
        factory: BenchFactory, pred: Callable[[Benchmark], bool]
    ) -> BenchFactory:
        def wrapped(ctx: Context[Any]) -> list[Benchmark]:
            return [b for b in factory(ctx) if pred(b)]

        return wrapped


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def suite(name: str, *benchmarks: Benchmark) -> Suite:
    """Concise constructor: ``suite("LoxSuite", b1, b2, ...)``."""
    return Suite(name=name, benchmarks=tuple(benchmarks))
