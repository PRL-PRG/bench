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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from bench.builder.base import BuilderBase, const, merge_sequence
from bench.builder.benchmark import Benchmark, BenchmarkBuilder, default_label
from bench.builder.context import Context, Data
from bench.core.invocation import (
    default_success,
)
from bench.core.outlier import ModifiedZScore
from bench.core.policy import FixedRuns
from bench.runner.controller import Controller

type BenchmarkGenerator = Callable[[Context[Any]], list[BenchmarkBuilder]]


# The inheritance root: the concrete defaults a benchmark falls back to when no
# level (app/suite/benchmark) set a field. `command` has no sensible default and
# is checked at materialize. Folded in via `overlay` as the weakest layer.
DEFAULTS = BuilderBase(
    command=None,
    cwd=lambda _: Path.cwd(),
    env=const({}),
    timeout=const(None),
    metrics=(),
    success=const(default_success),
    warmup=const(FixedRuns(0)),
    runs=const(FixedRuns(1)),
    outlier_detection=ModifiedZScore(),
    cooldown=0.0,
    controller=const(Controller()),
    label_fn=default_label,
)


@dataclass(frozen=True, slots=True)
class SuiteBuilder(BuilderBase):
    """A named, frozen collection of benchmarks, generators, and defaults."""

    name: str = ""
    benchmarks: Sequence[BenchmarkBuilder] = ()
    generators: Sequence[BenchmarkGenerator] = ()

    # ----- suite-only fields (inheritable config lives on BuilderBase) -----
    # Randomize the materialized benchmark order (Mytkowicz et al.), seeded for
    # reproducibility. SuiteBuilder-level: each suite shuffles its own benchmarks.
    shuffle: bool = False
    shuffle_seed: int | None = None

    # ----- producers -------------------------------------------------

    def with_name(self, name: str, override: bool = False) -> SuiteBuilder:
        # Special case - allow override for empty name
        if name == "":
            override = True

        return self.replace(
            "name",
            name,
            override=override,
        )

    def add(self, *bs: BenchmarkBuilder) -> SuiteBuilder:
        return self.replace(
            "benchmarks",
            tuple(bs),
            override=False,
            merge=merge_sequence,
        )

    def generator(self, fn: BenchmarkGenerator) -> SuiteBuilder:
        """Register a deferred `(ctx: Context) -> [BenchmarkBuilder]` producer,
        called when the suite materializes."""
        return self.replace(
            "generators",
            (fn,),
            override=False,
            merge=merge_sequence,
        )

    # ----- defaults (shared setters live on BuilderBase) -----------

    def with_shuffle(self, seed: int | None = None) -> SuiteBuilder:
        """Randomize the order benchmarks materialize in (seedable)."""
        # TODO: !!!
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
        for f in self.generators:
            collected.extend(f(ctx))
        # Fold the inheritance chain: DEFAULTS < this suite < each benchmark.
        # (An enclosing app has already folded itself into this suite via overlay.)
        base = DEFAULTS.overlay(self)
        out: list[Benchmark] = []
        for b in collected:
            resolved = base.overlay(b)
            if resolved.command is None:
                raise ValueError(
                    f"Benchmark {b.name!r} has no command - set one with "
                    f"BenchmarkBuilder.with_command or SuiteBuilder.with_command"
                )
            out.extend(resolved.create(params, suite=self.name))

        if self.shuffle:
            random.Random(self.shuffle_seed).shuffle(out)
        return out


def suite(name: str, *benchmarks: BenchmarkBuilder) -> SuiteBuilder:
    """Concise constructor: `suite("LoxSuite", b1, b2, ...)`."""
    return SuiteBuilder(name=name, benchmarks=tuple(benchmarks))
