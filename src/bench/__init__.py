"""bench - a benchmarking framework."""

# Atoms
from bench.core.invocation import (
    Invocation,
    InvocationResult,
    Variant,
    Verdict,
    default_success,
)
from bench.core.results import (
    Iteration,
    Report,
    Execution,
    Sample,
    report_from_json,
    report_to_json,
)

# Metrics
from bench.core.metric import (
    FloatPerLine,
    IterationMetric,
    Metric,
    Rebench,
    Regex,
    RUsage,
    Time,
    max_rss,
)
from bench.perf import PerfStat

# Stopping policies
from bench.core.policy import (
    CoefficientOfVariation,
    FixedRuns,
    MaxDuration,
    PolicyState,
    StoppingPolicy,
)

# Outlier detection
from bench.core.outlier import (
    ModifiedZScore,
    NoDetection,
    OutlierDetection,
)

# Environment + diagnostics
from bench.core.environment import (
    Diagnostic,
    Environment,
    EnvironmentCollector,
    NoEnvironment,
    SystemEnvironment,
)
from bench.core.checks import run_checks

# Benchmark / SuiteBuilder
from bench.builder.benchmark import (
    Benchmark,
    BenchmarkBuilder,
    bench,
    default_label,
    from_files,
)
from bench.builder.suite import SuiteBuilder, suite
from bench.builder.context import Context, SharedBenchParams, SharedSelectionParams

# Runners
from bench.runner.base import (
    Runner,
    SuiteMaterializationError,
)
from bench.runner.dry import Dry
from bench.runner.parallel import Parallel
from bench.runner.sequential import Sequential

# Reporters
from bench.report.reporter import (
    CompositeReporter,
    CsvReporter,
    DirReporter,
    JsonReporter,
    ProgressReporter,
    Reporter,
    SummaryReporter,
)

# Formatters
from bench.report.formatter import (
    Compact,
    DefaultSummary,
    Formatter,
    GeomeanSummary,
    Results,
    Summary,
)

# BenchAppBuilder abstraction + run pipeline
from bench.run import BenchAppBuilder, NoBenchmarksMatchedError, bench_app, run

# CLI
from bench.cli import main

__all__ = [
    # Atoms
    "Invocation",
    "InvocationResult",
    "Variant",
    "Verdict",
    "default_success",
    "Sample",
    "Iteration",
    "Execution",
    "Report",
    "report_from_json",
    "report_to_json",
    # Metrics
    "IterationMetric",
    "Metric",
    "Time",
    "Regex",
    "FloatPerLine",
    "Rebench",
    "RUsage",
    "max_rss",
    "PerfStat",
    # Policies
    "StoppingPolicy",
    "PolicyState",
    "FixedRuns",
    "CoefficientOfVariation",
    "MaxDuration",
    # Outlier detection
    "OutlierDetection",
    "NoDetection",
    "ModifiedZScore",
    # Environment + diagnostics
    "Environment",
    "EnvironmentCollector",
    "SystemEnvironment",
    "NoEnvironment",
    "Diagnostic",
    "run_checks",
    # Benchmark / SuiteBuilder
    "Benchmark",
    "BenchmarkBuilder",
    "bench",
    "default_label",
    "from_files",
    "SuiteBuilder",
    "suite",
    "Context",
    "SharedBenchParams",
    "SharedSelectionParams",
    # Runners
    "Runner",
    "Sequential",
    "Parallel",
    "Dry",
    "SuiteMaterializationError",
    # Reporters
    "Reporter",
    "CompositeReporter",
    "CsvReporter",
    "JsonReporter",
    "DirReporter",
    "SummaryReporter",
    "ProgressReporter",
    # Formatters
    "Formatter",
    "Results",
    "Summary",
    "GeomeanSummary",
    "DefaultSummary",
    "Compact",
    # BenchAppBuilder + run pipeline
    "BenchAppBuilder",
    "NoBenchmarksMatchedError",
    "bench_app",
    "run",
    # CLI
    "main",
]
