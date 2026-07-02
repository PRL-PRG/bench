"""bench - a benchmarking framework."""

# Atoms
from bench.core.execution import (
    Execution,
    ExecutionResult,
    Variant,
    Verdict,
    default_success,
)
from bench.core.sample import (
    Iteration,
    Report,
    Run,
    Sample,
    report_from_json,
    report_to_json,
)

# Metrics
from bench.core.metric import (
    FloatPerLine,
    IterationMetric,
    ProcessMetric,
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
from bench.grammar.benchmark import (
    Benchmark,
    BenchmarkBuilder,
    bench,
    default_label,
    from_files,
)
from bench.grammar.suite import SuiteBuilder, suite
from bench.grammar.context import Cli, Context

# Runners
from bench.runner.base import (
    Runner,
    SuiteMaterializationError,
)
from bench.runner.dry import Dry
from bench.runner.parallel import Parallel
from bench.runner.sequential import Sequential
from bench.runner.source import HarnessHandle, HarnessMonitor, line_monitor

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
    GroupedSummary,
    Results,
    Summary,
)

# BenchAppBuilder abstraction + run pipeline
from bench.run import BenchAppBuilder, bench_app, run

# CLI
from bench.cli import main

__all__ = [
    # Atoms
    "Execution",
    "ExecutionResult",
    "Variant",
    "Verdict",
    "default_success",
    "Sample",
    "Iteration",
    "Run",
    "Report",
    "report_from_json",
    "report_to_json",
    # Metrics
    "IterationMetric",
    "ProcessMetric",
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
    "Cli",
    # Runners
    "Runner",
    "Sequential",
    "Parallel",
    "Dry",
    "SuiteMaterializationError",
    "HarnessMonitor",
    "HarnessHandle",
    "line_monitor",
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
    "GroupedSummary",
    "DefaultSummary",
    "Compact",
    # BenchAppBuilder + run pipeline
    "BenchAppBuilder",
    "bench_app",
    "run",
    # CLI
    "main",
]
