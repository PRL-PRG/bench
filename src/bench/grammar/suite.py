"""Suite: a named collection of Benchmarks plus the defaults they inherit.

It stores defaults (command, env, policies, metrics, ...) next to its member
benchmarks. Calling a `.with_*` method just sets the suite field, and nothing
propagates eagerly. Resolution happens once, in `materialize(ctx)`: every
unset benchmark field is filled from the suite, so builder-call
order never matters.
"""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from bench.grammar.benchmark import (
    UNSET,
    Benchmark,
    BenchmarkBuilder,
    Build,
    CommandFn,
    MetricSetters,
    EnvFn,
    LabelFn,
    PathFn,
    SkipFn,
    as_build,
    const,
    default_label,
    make_skip_rule,
    normalize_matrix,
    to_argv,
)
from bench.grammar.context import Context, Matrix
from bench.core.execution import (
    EMPTY_MAPPING,
    SuccessFn,
    default_success,
)
from bench.core.metric import (
    IterationMetric,
    MetricSource,
    ProcessMetric,
)
from bench.core.outlier import ModifiedZScore, OutlierDetection
from bench.core.policy import FixedRuns, StoppingPolicy, coerce_policy

if TYPE_CHECKING:
    from bench.runner.source import HarnessMonitor


type BenchmarkFactory = Callable[[Context[Any]], list[BenchmarkBuilder]]


def _default_cwd(ctx: Context[Any]) -> Path:
    """Suite default cwd: the invoking process's cwd, read at schedule time."""
    return Path.cwd()


def _default_env(ctx: Context[Any]) -> Mapping[str, str]:
    """Suite default env: empty, the child inherits the OS environment."""
    return EMPTY_MAPPING


def _merge_env(base: EnvFn, override: EnvFn) -> EnvFn:
    """Lazy per-key merge: `base` first, `override` wins (suite (+) benchmark)."""
    return lambda ctx: {**base(ctx), **override(ctx)}


@dataclass(frozen=True, slots=True)
class Suite(MetricSetters):
    """A named, frozen collection of benchmarks, factories, and defaults."""

    name: str = ""
    benchmarks: tuple[BenchmarkBuilder, ...] = ()
    factories: tuple[BenchmarkFactory, ...] = ()

    # ----- suite defaults --------------------------------------------
    # Each is the inheritance root for benchmarks that leave the field UNSET.
    # command/cwd/env and the policy/config fields are stored as `Build`
    # builders (a static default is wrapped via `const`). success/monitor/
    # label_fn/harness are value-only.
    command: CommandFn = UNSET  # no sensible default, checked at materialize
    cwd: PathFn = _default_cwd
    env: EnvFn = _default_env
    timeout: Build[float | None] = const(None)  # None = no timeout
    # Default metrics are empty; a benchmark with none declared falls back to
    # Time() at resolution (see BenchmarkBuilder._resolve_cell).
    iteration_metrics: Build[tuple[tuple[IterationMetric, MetricSource], ...]] = const(
        ()
    )
    process_metrics: Build[tuple[ProcessMetric, ...]] = const(())
    success: SuccessFn = default_success
    warmup: Build[StoppingPolicy] = const(FixedRuns(0))
    runs: Build[StoppingPolicy] = const(FixedRuns(1))
    # Outlier detection is on by default; with_outlier_detection(NoDetection())
    # turns it off.
    outlier_detection: OutlierDetection = ModifiedZScore()
    # Seconds to pause between successive process executions (thermal settling).
    cooldown: float = 0.0
    # Randomize the materialized benchmark order (Mytkowicz et al.), seeded for
    # reproducibility. Suite-level: each suite shuffles its own benchmarks.
    shuffle: bool = False
    shuffle_seed: int | None = None
    harness: bool = False
    # Suite-level default for the harness monitor, benchmark value wins.
    monitor: HarnessMonitor | None = None
    label_fn: LabelFn = default_label
    matrix: Mapping[str, tuple[Any, ...]] = EMPTY_MAPPING
    skips: tuple[SkipFn, ...] = ()
    filters: tuple[Callable[[Benchmark], bool], ...] = ()

    # ----- producers -------------------------------------------------

    def with_name(self, name: str) -> Suite:
        return dataclasses.replace(self, name=name)

    def add(self, b: BenchmarkBuilder) -> Suite:
        return dataclasses.replace(self, benchmarks=self.benchmarks + (b,))

    def add_all(self, *bs: BenchmarkBuilder) -> Suite:
        return dataclasses.replace(self, benchmarks=self.benchmarks + tuple(bs))

    def factory(self, fn: BenchmarkFactory) -> Suite:
        """Register a deferred `(ctx: Context) -> [Benchmark]` producer
        that wwill be called when suite materializes."""
        return dataclasses.replace(self, factories=self.factories + (fn,))

    def filter(self, pred: Callable[[Benchmark], bool]) -> Suite:
        """Keep only the resolved benchmarks for which `pred(b)` is truthy.

        Applied once, at the end of `materialize`, to every fully-resolved
        variant, so it is order-independent (it sees benchmarks added before
        or after this call) and can filter individual matrix variants.
        """
        return dataclasses.replace(self, filters=self.filters + (pred,))

    # ----- defaults ---------------------------------------------------

    def with_command(self, command: Sequence[str] | CommandFn) -> Suite:
        return dataclasses.replace(self, command=as_build(command, to_argv))

    def with_cwd(self, cwd: str | Path | PathFn) -> Suite:
        return dataclasses.replace(self, cwd=as_build(cwd, Path))

    def with_env(self, env: Mapping[str, str] | EnvFn) -> Suite:
        return dataclasses.replace(self, env=as_build(env, dict))

    def with_timeout(self, timeout: float | None | Build[float | None]) -> Suite:
        return dataclasses.replace(self, timeout=as_build(timeout))

    def with_success(self, fn: SuccessFn) -> Suite:
        return dataclasses.replace(self, success=fn)

    def with_label(self, fn: LabelFn) -> Suite:
        return dataclasses.replace(self, label_fn=fn)

    def with_warmup(self, p: int | StoppingPolicy | Build[StoppingPolicy]) -> Suite:
        """Set the default warmup policy."""
        return dataclasses.replace(self, warmup=as_build(p, coerce_policy))

    def with_runs(self, p: int | StoppingPolicy | Build[StoppingPolicy]) -> Suite:
        """Set the default policy for the measured runs."""
        return dataclasses.replace(self, runs=as_build(p, coerce_policy))

    def with_outlier_detection(self, d: OutlierDetection) -> Suite:
        """Set the default outlier-detection strategy (`NoDetection()` = off)."""
        return dataclasses.replace(self, outlier_detection=d)

    def with_cooldown(self, seconds: float) -> Suite:
        """Set the default pause between successive process executions."""
        return dataclasses.replace(self, cooldown=seconds)

    def with_shuffle(self, seed: int | None = None) -> Suite:
        """Randomize the order benchmarks materialize in (seedable)."""
        return dataclasses.replace(self, shuffle=True, shuffle_seed=seed)

    def with_harness(self, monitor: HarnessMonitor | None = None) -> Suite:
        """Make every contained benchmark a harness benchmark."""
        return dataclasses.replace(self, harness=True, monitor=monitor)

    def with_matrix(self, **dims: Sequence[Any]) -> Suite:
        """Declare matrix dimensions applied to every contained benchmark

        When materialized,these dimensions are appended to each
        benchmark's own (a per-benchmark dimensions compose with
        suite-level ones).
        """
        return dataclasses.replace(self, matrix=normalize_matrix(dims))

    def add_matrix_skip(
        self,
        predicate: SkipFn | None = None,
        /,
        **kwargs: Any,
    ) -> Suite:
        """Add a skip rule applied to every contained benchmark.

        kwargs are AND-matched against dimension values, optional predicate for complex cases.
        """
        rule = make_skip_rule(predicate, kwargs)
        if rule is None:
            return self
        return dataclasses.replace(self, skips=self.skips + (rule,))

    def materialize(self, params: Any) -> list[Benchmark]:
        """Return the concrete fully resolved benchmark list."""

        ctx: Context[Any] = Context(
            params=params,
            suite=self.name,
            benchmark=None,
            matrix=Matrix(),
        )
        collected = list(self.benchmarks)
        for f in self.factories:
            collected.extend(f(ctx))
        out: list[Benchmark] = []
        for b in collected:
            resolved = self._resolve(self._with_suite_matrix(b))
            out.extend(resolved.create(params, suite=self.name))
        for pred in self.filters:
            out = [b for b in out if pred(b)]
        if self.shuffle:
            random.Random(self.shuffle_seed).shuffle(out)
        return out

    def _with_suite_matrix(self, b: BenchmarkBuilder) -> BenchmarkBuilder:
        """Append suite matrix dimensions after the benchmark's own and union skip rules."""
        if self.matrix:
            for name in self.matrix:
                if name in b.matrix:
                    raise ValueError(
                        f"Benchmark {b.name!r}: matrix dimension {name!r} already declared"
                    )
            b = dataclasses.replace(
                b, matrix=MappingProxyType({**b.matrix, **self.matrix})
            )
        if self.skips:
            b = dataclasses.replace(b, skips=b.skips + self.skips)
        return b

    def _resolve(self, b: BenchmarkBuilder) -> BenchmarkBuilder:
        """Fill unset fields from the suite: explicit benchmark value wins,
        otherwise the suite default. `env` is the one merging field: the suite
        env sits under the benchmark's own, benchmark winning per key."""

        command = b.command if b.command is not UNSET else self.command
        cwd = b.cwd if b.cwd is not UNSET else self.cwd
        env = _merge_env(self.env, b.env) if b.env is not UNSET else self.env

        resolved = dataclasses.replace(
            b,
            command=command,
            cwd=cwd,
            env=env,
            timeout=self.timeout if b.timeout is UNSET else b.timeout,
            iteration_metrics=(
                self.iteration_metrics
                if b.iteration_metrics is UNSET
                else b.iteration_metrics
            ),
            process_metrics=(
                self.process_metrics
                if b.process_metrics is UNSET
                else b.process_metrics
            ),
            success=self.success if b.success is UNSET else b.success,
            warmup=self.warmup if b.warmup is UNSET else b.warmup,
            runs=self.runs if b.runs is UNSET else b.runs,
            outlier_detection=(
                self.outlier_detection
                if b.outlier_detection is UNSET
                else b.outlier_detection
            ),
            cooldown=self.cooldown if b.cooldown is UNSET else b.cooldown,
            harness=self.harness if b.harness is UNSET else b.harness,
            monitor=self.monitor if b.monitor is UNSET else b.monitor,
            label_fn=self.label_fn if b.label_fn is UNSET else b.label_fn,
        )
        if resolved.command is UNSET:
            raise ValueError(
                f"Benchmark {b.name!r} has no command — set one with "
                f"BenchmarkBuilder.with_command or Suite.with_command"
            )
        return resolved


def suite(name: str, *benchmarks: BenchmarkBuilder) -> Suite:
    """Concise constructor: `suite("LoxSuite", b1, b2, ...)`."""
    return Suite(name=name, benchmarks=tuple(benchmarks))
