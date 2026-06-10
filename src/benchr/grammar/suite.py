"""Suite: a named collection of Benchmarks plus the defaults they inherit.

A ``Suite`` is a frozen value object. It stores *defaults* (command, env,
policies, metrics, …) next to its member benchmarks; calling a ``.with_*``
method just sets the suite field — nothing propagates eagerly. Resolution
happens once, in ``materialize(ctx)``: every benchmark field still holding
its ``UNSET_*`` null object is filled from the suite, so builder-call order
never matters — ``Suite("A").with_command(c).add(b)`` equals
``Suite("A").add(b).with_command(c)``.

Resolution precedence (most specific wins):

    benchmark explicit > benchmark axis default > suite default

(The CLI's ``--runs/--warmup`` override is applied later, by the orchestrator,
on the materialized benchmark list — not by the Suite. See ``benchr.run``.)

Producers:
  ``.add(b)`` / ``.add_all(*bs)``      append benchmarks
  ``.factory(fn)``                     defer ``(ctx) -> [Benchmark]`` production
                                       (wrap the ``from_files`` helper for
                                       ctx-dependent discovery)
  ``.with_command/.with_cwd/.with_env/.with_timeout/.with_metric/``
  ``.with_success/.with_label``        set a suite default
  ``.with_warmup/.with_runs``          set a default warmup/runs policy
  ``.with_matrix(**axes)``             add axes to every benchmark (at materialize)
  ``.add_matrix_skip(...)``            add a skip rule to every benchmark
  ``.filter(pred)``                    keep matching benchmarks (eager — the one
                                       order-dependent builder; add before filtering)
  ``.with_name(new_name)``             rename
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

from benchr.grammar.benchmark import (
    DEFAULT_CWD,
    EMPTY_ENV,
    UNSET_COMMAND,
    UNSET_CWD,
    UNSET_ENV,
    UNSET_LABEL,
    Benchmark,
    Command,
    CommandFn,
    Cwd,
    Env,
    EnvFn,
    LabelFn,
    PathFn,
    SkipFn,
    SkipRule,
    _coerce_policy,
    bench,
    default_label,
)
from benchr.grammar.execution import (
    _EMPTY_MAPPING,
    SuccessFn,
    UNSET_SUCCESS,
    default_success,
)
from benchr.grammar.metric import Metric, Time
from benchr.grammar.policy import UNSET_POLICY, FixedRuns, StoppingPolicy


# A function the Runner can call to materialize benchmarks given the ctx.
# Suite.from_files defers discovery to this hook so e.g. paths can depend on
# ctx.cwd. The Runner / CLI flattens these into concrete benchmarks at run time.
BenchFactory = Callable[[Any], list[Benchmark]]


@dataclass(frozen=True, slots=True)
class Suite:
    """A named, frozen collection of benchmarks, factories, and defaults."""

    name: str = ""
    benchmarks: tuple[Benchmark, ...] = ()
    factories: tuple[BenchFactory, ...] = ()

    # ----- suite defaults (always concrete, except command) ----------
    command: Command = UNSET_COMMAND  # no sensible default; checked at materialize
    cwd: Cwd = DEFAULT_CWD
    env: Env = EMPTY_ENV
    timeout: float | None = None  # None = no timeout
    metrics: tuple[Metric, ...] = (Time(),)
    success: SuccessFn = default_success
    warmup: StoppingPolicy = FixedRuns(0)
    runs: StoppingPolicy = FixedRuns(1)
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
        """Register a deferred ``(ctx) -> [Benchmark]`` producer; ``materialize(ctx)``
        calls it. Wrap ``from_files`` here for ctx-dependent discovery, e.g.
        ``.factory(lambda ctx: from_files(ctx.cwd / "benchmarks", pattern=...))``."""
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
        return dataclasses.replace(self, command=Command(command))

    def with_cwd(self, cwd: str | Path | PathFn) -> Suite:
        return dataclasses.replace(self, cwd=Cwd(cwd))

    def with_env(self, env: Mapping[str, str] | EnvFn) -> Suite:
        """Set the suite env. At materialize it merges *under* each
        benchmark's own env — the benchmark wins per key."""
        return dataclasses.replace(self, env=Env(env))

    def with_timeout(self, timeout: float) -> Suite:
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
        return dataclasses.replace(self, warmup=_coerce_policy(p))

    def with_runs(self, p: StoppingPolicy | int) -> Suite:
        """Set the default runs (measure-phase) policy."""
        return dataclasses.replace(self, runs=_coerce_policy(p))

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
            kwargs=MappingProxyType(dict(kwargs)) if kwargs else _EMPTY_MAPPING,
            predicate=predicate,
        )
        return dataclasses.replace(self, skips=self.skips + (rule,))

    def materialize(self, ctx: Any) -> list[Benchmark]:
        """Return the concrete (post-expansion, fully resolved) benchmark list.

        Calls deferred factories, applies suite-level axes/skips, expands each
        benchmark's matrix into one Benchmark per surviving variant, and fills
        every still-unset field from the suite defaults. After this, benchmarks
        are fully concrete — runners just read fields.
        """
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
        else suite default."""
        resolved = dataclasses.replace(
            b,
            command=self.command if b.command is UNSET_COMMAND else b.command,
            cwd=self.cwd if b.cwd is UNSET_CWD else b.cwd,
            env=self.env if b.env is UNSET_ENV else self.env.merge(b.env),
            timeout=self.timeout if b.timeout is None else b.timeout,
            metrics=b.metrics or self.metrics,
            success=self.success if b.success is UNSET_SUCCESS else b.success,
            warmup=self.warmup if b.warmup is UNSET_POLICY else b.warmup,
            runs=self.runs if b.runs is UNSET_POLICY else b.runs,
            label_fn=self.label_fn if b.label_fn is UNSET_LABEL else b.label_fn,
        )
        if resolved.command is UNSET_COMMAND:
            raise ValueError(
                f"Benchmark {b.name!r} has no command — set one with "
                f"Benchmark.with_command or Suite.with_command"
            )
        return resolved

    @staticmethod
    def _wrap_filter(
        factory: BenchFactory, pred: Callable[[Benchmark], bool]
    ) -> BenchFactory:
        def wrapped(ctx: Any) -> list[Benchmark]:
            return [b for b in factory(ctx) if pred(b)]

        return wrapped


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def suite(name: str, *benchmarks: Benchmark) -> Suite:
    """Concise constructor: ``suite("LoxSuite", b1, b2, ...)``."""
    return Suite(name=name, benchmarks=tuple(benchmarks))
