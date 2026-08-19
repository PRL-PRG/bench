"""SuiteBuilder: a named collection of Benchmarks plus the defaults they inherit.

It stores defaults (command, env, policies, metrics, ...) next to its member
benchmarks. Calling a `.with_*` method just sets the suite field, and nothing
propagates eagerly. Resolution happens once, in `materialize(ctx)`: every
unset benchmark field is filled from the suite, so builder-call
order never matters.
"""

from __future__ import annotations

import dataclasses
import itertools
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Sequence

from bench.builder.base import BuilderBase, merge_sequence
from bench.builder.benchmark import Benchmark, BenchmarkBuilder


@dataclass(frozen=True, slots=True)
class SuiteContext[T]:
    params: T
    suite: str


type BenchmarkGenerator = Callable[[SuiteContext[Any]], list[BenchmarkBuilder]]


@dataclass(frozen=True, slots=True)
class SuiteBuilder(BuilderBase):
    """A named, frozen collection of benchmarks, generators, and defaults."""

    name: str = ""
    benchmarks: Sequence[BenchmarkBuilder] = ()
    generators: Sequence[BenchmarkGenerator] = ()

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

    def with_shuffle(self, seed: int | None = None) -> SuiteBuilder:
        """Randomize the order benchmarks materialize in (seedable)."""
        # TODO: !!!
        return dataclasses.replace(self, shuffle=True, shuffle_seed=seed)

    # ----- creation ----------------------------------------------------

    def materialize(self, params: Any) -> list[Benchmark]:
        """Return the concrete fully resolved benchmark list."""

        ctx: SuiteContext[Any] = SuiteContext(
            params=params,
            suite=self.name,
        )

        out: list[Benchmark] = [
            bench
            for builder in itertools.chain(
                self.benchmarks, *(gen(ctx) for gen in self.generators)
            )
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
    return SuiteBuilder(name=name, benchmarks=tuple(benchmarks))
