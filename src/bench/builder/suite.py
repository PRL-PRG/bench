"""SuiteBuilder: a named collection of Benchmarks plus the defaults they inherit.

A `.with_*` call only sets the suite's own field; nothing propagates eagerly.
Resolution happens once, in `materialize(params)`, where every unset benchmark
field is filled from the suite - so builder-call order never matters.
"""

from __future__ import annotations

import itertools
import random
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from bench.builder.base import BuilderBase, const, merge_sequence
from bench.builder.benchmark import BenchmarkBuilder
from bench.error import BenchError
from bench.model.benchmark import Benchmark
from bench.params import Params


# ---------------------------------------------------------------------------
# Base types
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SuiteContext[T: Params]:
    params: T
    suite: str


# HACK: The argument should be "Params or its child" but this is the best
# we have for now
type BenchmarkGenerator = Callable[[SuiteContext[Any]], list[BenchmarkBuilder]]
type SubsuiteGenerator = Callable[[SuiteContext[Any]], list[SuiteBuilder]]

# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SuiteBuilder(BuilderBase):
    """A named, frozen collection of benchmarks, generators, and defaults."""

    name: str = ""
    benchmarks: Sequence[BenchmarkGenerator] = ()
    suites: Sequence[SubsuiteGenerator] = ()

    # Randomize the materialized benchmark order (Mytkowicz et al.), seeded for
    # reproducibility. SuiteBuilder-level: each suite shuffles its own benchmarks.
    shuffle: bool = False
    shuffle_seed: int | None = None

    # ----- modify multiple parameters at once -------------------------

    def implace_modify(
        self, fun: Callable[[SuiteBuilder, SuiteContext[Any]], SuiteBuilder]
    ) -> SuiteBuilder:
        return suite("").add_suite_generator(lambda ctx: [fun(self, ctx)])

    # ----- name -----------

    def with_name(self, name: str, override: bool = False) -> SuiteBuilder:
        # Special case - allow override for empty name
        if name == "":
            override = True

        return self.replace(
            "name",
            name,
            override=override,
        )

    # ----- add benchmarks -----------

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

    # ----- add subsuites -----------

    def add_suites(self, *ss: SuiteBuilder) -> SuiteBuilder:
        """Register suite(s)."""
        return self.replace(
            "suites",
            tuple(const((s,)) for s in ss),
            override=False,
            merge=merge_sequence,
        )

    def add_suite_generator(self, fn: SubsuiteGenerator) -> SuiteBuilder:
        """Register a deferred suite producer."""
        return self.replace(
            "suites",
            (fn,),
            override=False,
            merge=merge_sequence,
        )

    # ----- shuffle -----------

    def with_shuffle(
        self, seed: int | None = None, override: bool = False
    ) -> SuiteBuilder:
        """Randomize the order benchmarks materialize in (seedable)."""
        return self.replace("shuffle", True, override=True).replace(
            "shuffle_seed", seed, override=override
        )

    # ----- creation ----------------------------------------------------

    def materialize(self, params: Params, parent_suite: str = "") -> list[Benchmark]:
        """Return the concrete fully resolved benchmark list."""

        if parent_suite == "":
            actual_name = self.name
        elif self.name == "":
            actual_name = parent_suite
        else:
            actual_name = f"{parent_suite}/{self.name}"

        ctx: SuiteContext[Params] = SuiteContext(
            params=params,
            suite=actual_name,
        )

        out: list[Benchmark] = list(
            itertools.chain(
                (
                    bench
                    for generator in self.benchmarks
                    for builder in generator(ctx)
                    for bench in builder.inherit_from(self).create(
                        params, suite=actual_name
                    )
                ),
                (
                    bench
                    for generator in self.suites
                    for builder in generator(ctx)
                    for bench in builder.inherit_from(self).materialize(
                        params, parent_suite=actual_name
                    )
                ),
            )
        )

        if self.shuffle:
            random.Random(self.shuffle_seed).shuffle(out)

        return out


# ---------------------------------------------------------------------------
# Shorthand constructors
# ---------------------------------------------------------------------------


def suite(name: str, *children: BenchmarkBuilder | SuiteBuilder) -> SuiteBuilder:
    """Concise constructor: `suite("LoxSuite", b1, b2, ...)`."""
    return (
        SuiteBuilder(name=name)
        .add(*(ch for ch in children if isinstance(ch, BenchmarkBuilder)))
        .add_suites(*(ch for ch in children if isinstance(ch, SuiteBuilder)))
    )


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------


class SuiteMaterializationError(BenchError):
    """A suite's factory failed while building its benchmarks."""

    def __init__(self, suite: str, cause: BaseException) -> None:
        self.suite = suite
        self.cause = cause
        super().__init__(self._format())

    def _format(self) -> str:
        lines = [f"Failed to materialize suite {self.suite!r}: {self.cause}"]

        # TODO: the idea is that only subprocess failures carry capturable output worth surfacing to user
        if isinstance(self.cause, subprocess.CalledProcessError):
            out = self.cause.output or self.cause.stderr
            if out:
                text = (
                    out.decode(errors="replace") if isinstance(out, bytes) else str(out)
                )
                lines += ["", text.rstrip()]
        return "\n".join(lines)


def plan(
    suites: list[SuiteBuilder],
    params: Params,
) -> list[Benchmark]:
    """Flatten suites + their deferred factories into resolved benchmarks."""
    out: list[Benchmark] = []
    for s in suites:
        try:
            out.extend(s.materialize(params))
        except Exception as cause:
            raise SuiteMaterializationError(s.name, cause) from cause
    return out
