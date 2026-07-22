"""SuiteBuilder: a named collection of Benchmarks plus the defaults they inherit.

It stores defaults (command, env, policies, metrics, ...) next to its member
benchmarks. Calling a `.with_*` method just sets the suite field, and nothing
propagates eagerly. Resolution happens once, in `materialize(ctx)`: every
unset benchmark field is filled from the suite, so builder-call
order never matters.
"""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.builder.base import UNSET, BuilderBase, const
from bench.builder.benchmark import Benchmark, BenchmarkBuilder, default_label
from bench.builder.context import Context, Data
from bench.core.invocation import (
    EMPTY_MAPPING,
    default_success,
)
from bench.core.outlier import ModifiedZScore
from bench.core.policy import FixedRuns


type BenchmarkFactory = Callable[[Context[Any]], list[BenchmarkBuilder]]


def _default_cwd(ctx: Context[Any]) -> Path:
    """Default cwd: the invoking process's cwd, read at schedule time."""
    return Path.cwd()


def _default_env(ctx: Context[Any]) -> Mapping[str, str]:
    """Default env: empty, the child inherits the OS environment."""
    return EMPTY_MAPPING


# The inheritance root: the concrete defaults a benchmark falls back to when no
# level (app/suite/benchmark) set a field. `command` has no sensible default and
# is checked at materialize. Folded in via `overlay` as the weakest layer.
DEFAULTS = BuilderBase(
    command=UNSET,
    cwd=_default_cwd,
    env=_default_env,
    timeout=const(None),
    iteration_metrics=const(()),
    process_metrics=const(()),
    success=const(default_success),
    warmup=const(FixedRuns(0)),
    runs=const(FixedRuns(1)),
    outlier_detection=ModifiedZScore(),
    cooldown=0.0,
    label_fn=default_label,
)


@dataclass(frozen=True, slots=True)
class SuiteBuilder(BuilderBase):
    """A named, frozen collection of benchmarks, factories, and defaults."""

    name: str = ""
    benchmarks: tuple[BenchmarkBuilder, ...] = ()
    factories: tuple[BenchmarkFactory, ...] = ()

    # ----- suite-only fields (inheritable config lives on BuilderBase) -----
    # Randomize the materialized benchmark order (Mytkowicz et al.), seeded for
    # reproducibility. SuiteBuilder-level: each suite shuffles its own benchmarks.
    shuffle: bool = False
    shuffle_seed: int | None = None
    filters: tuple[Callable[[Benchmark], bool], ...] = ()

    # ----- producers -------------------------------------------------

    def with_name(self, name: str) -> SuiteBuilder:
        return dataclasses.replace(self, name=name)

    def add(self, b: BenchmarkBuilder) -> SuiteBuilder:
        return dataclasses.replace(self, benchmarks=self.benchmarks + (b,))

    def add_all(self, *bs: BenchmarkBuilder) -> SuiteBuilder:
        return dataclasses.replace(self, benchmarks=self.benchmarks + tuple(bs))

    def factory(self, fn: BenchmarkFactory) -> SuiteBuilder:
        """Register a deferred `(ctx: Context) -> [BenchmarkBuilder]` producer,
        called when the suite materializes."""
        return dataclasses.replace(self, factories=self.factories + (fn,))

    def filter(self, pred: Callable[[Benchmark], bool]) -> SuiteBuilder:
        """Keep only the resolved benchmarks for which `pred(b)` is truthy.

        Applied once, at the end of `materialize`, to every fully-resolved
        variant, so it is order-independent (it sees benchmarks added before
        or after this call) and can filter individual matrix variants.
        """
        return dataclasses.replace(self, filters=self.filters + (pred,))

    # ----- defaults (shared setters live on BuilderBase) -----------

    def with_shuffle(self, seed: int | None = None) -> SuiteBuilder:
        """Randomize the order benchmarks materialize in (seedable)."""
        return dataclasses.replace(self, shuffle=True, shuffle_seed=seed)

    def materialize(self, params: Any) -> list[Benchmark]:
        """Return the concrete fully resolved benchmark list."""

        ctx: Context[Any] = Context(
            params=params,
            suite=self.name,
            benchmark=None,
            data=Data(),
        )
        collected = list(self.benchmarks)
        for f in self.factories:
            collected.extend(f(ctx))
        # Fold the inheritance chain: DEFAULTS < this suite < each benchmark.
        # (An enclosing app has already folded itself into this suite via overlay.)
        base = DEFAULTS.overlay(self)
        out: list[Benchmark] = []
        for b in collected:
            resolved = base.overlay(b)
            if resolved.command is UNSET:
                raise ValueError(
                    f"Benchmark {b.name!r} has no command - set one with "
                    f"BenchmarkBuilder.with_command or SuiteBuilder.with_command"
                )
            out.extend(resolved.create(params, suite=self.name))
        for pred in self.filters:
            out = [b for b in out if pred(b)]
        if self.shuffle:
            random.Random(self.shuffle_seed).shuffle(out)
        return out


def suite(name: str, *benchmarks: BenchmarkBuilder) -> SuiteBuilder:
    """Concise constructor: `suite("LoxSuite", b1, b2, ...)`."""
    return SuiteBuilder(name=name, benchmarks=tuple(benchmarks))
