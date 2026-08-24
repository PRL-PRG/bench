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
from typing import Any, Sequence

from bench.builder.base import BuilderBase, const, merge_sequence
from bench.builder.benchmark import Benchmark, BenchmarkBuilder
from bench.builder.context import Params


@dataclass(frozen=True, slots=True)
class SuiteContext[T: Params]:
    params: T
    suite: str


# HACK: The argument should be "Params or its child" but this is the best
# we have for now
type BenchmarkGenerator = Callable[[SuiteContext[Any]], list[BenchmarkBuilder]]


@dataclass(frozen=True, slots=True)
class SuiteBuilder(BuilderBase):
    """A named, frozen collection of benchmarks, generators, and defaults."""

    name: str = ""
    benchmarks: Sequence[BenchmarkGenerator] = ()

    # Randomize the materialized benchmark order (Mytkowicz et al.), seeded for
    # reproducibility. SuiteBuilder-level: each suite shuffles its own benchmarks.
    shuffle: bool = False
    shuffle_seed: int | None = None

    # ----- with_* setters -----------

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
            tuple(const((b,)) for b in bs),
            override=False,
            merge=merge_sequence,
        )

    def generator(self, fn: BenchmarkGenerator) -> SuiteBuilder:
        """Register a deferred `(ctx: Context) -> [BenchmarkBuilder]` producer,
        called when the suite materializes."""
        return self.replace(
            "benchmarks",
            (fn,),
            override=False,
            merge=merge_sequence,
        )

    def with_shuffle(self, seed: int | None = None) -> SuiteBuilder:
        """Randomize the order benchmarks materialize in (seedable)."""
        # TODO: !!!
        return dataclasses.replace(self, shuffle=True, shuffle_seed=seed)

    # ----- creation ----------------------------------------------------

    def materialize(self, params: Params) -> list[Benchmark]:
        """Return the concrete fully resolved benchmark list."""

        ctx: SuiteContext[Params] = SuiteContext(
            params=params,
            suite=self.name,
        )

        out: list[Benchmark] = [
            bench
            for generator in self.benchmarks
            for builder in generator(ctx)
            for bench in builder.inherit_from(self).create(params, suite=self.name)
        ]

        if self.shuffle:
            random.Random(self.shuffle_seed).shuffle(out)

        return out


# ---------------------------------------------------------------------------
# Shorthand constructors
# ---------------------------------------------------------------------------


def suite(name: str, *benchmarks: BenchmarkBuilder) -> SuiteBuilder:
    """Concise constructor: `suite("LoxSuite", b1, b2, ...)`."""
    return SuiteBuilder(name=name).add(*benchmarks)
