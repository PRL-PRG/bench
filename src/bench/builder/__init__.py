"""Builder DSL for benchmarks: Invocation, Metric, Policy, Benchmark, SuiteBuilder."""

from bench.builder.app import (
    BenchAppBuilder,
    NoBenchmarksMatchedError,
    ParamFactory,
    SuiteGenerator,
    bench_app,
    run,
)
from bench.builder.base import (
    BuilderBase,
    Factory,
    MatrixAxis,
    UnresolvedCommand,
)
from bench.builder.benchmark import (
    BenchmarkBuilder,
    bench,
    from_files,
)
from bench.builder.context import Context, Data
from bench.builder.default import (
    default_filter,
    default_label,
    default_reporter,
    default_runner,
    default_success,
)
from bench.builder.suite import (
    BenchmarkGenerator,
    SubsuiteGenerator,
    SuiteBuilder,
    SuiteContext,
    SuiteMaterializationError,
    plan,
    suite,
)

__all__ = [
    "ParamFactory",
    "SuiteGenerator",
    "NoBenchmarksMatchedError",
    "BenchAppBuilder",
    "run",
    "bench_app",
    "UnresolvedCommand",
    "Factory",
    "MatrixAxis",
    "BuilderBase",
    "BenchmarkBuilder",
    "bench",
    "from_files",
    "Data",
    "Context",
    "default_filter",
    "default_label",
    "default_runner",
    "default_success",
    "default_reporter",
    "SuiteContext",
    "BenchmarkGenerator",
    "SubsuiteGenerator",
    "SuiteBuilder",
    "suite",
    "SuiteMaterializationError",
    "plan",
]
