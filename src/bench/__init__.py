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
    Observation,
    Report,
    Run,
    Sample,
    report_from_json,
    report_to_json,
)

# Metrics
from bench.core.metric import (
    FloatPerLine,
    Metric,
    Rebench,
    Regex,
    RUsage,
    Time,
    max_rss,
)

# Stopping policies
from bench.core.policy import (
    CoefficientOfVariation,
    FixedRuns,
    MaxDuration,
    PolicyState,
    StoppingPolicy,
)

# Benchmark / Suite
from bench.grammar.benchmark import (
    Benchmark,
    BenchmarkBuilder,
    bench,
    default_label,
    from_files,
)
from bench.grammar.suite import Suite, suite
from bench.grammar.context import Context

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
from bench.report.formatter import Compact, DefaultSummary, Formatter

# CLI
from bench.cli import Bench, main, run

__all__ = [
    # Atoms
    "Execution", "ExecutionResult", "Variant", "Verdict",
    "default_success",
    "Sample", "Observation", "Run", "Report", "report_from_json", "report_to_json",
    # Metrics
    "Metric",
    "Time", "Regex", "FloatPerLine", "Rebench", "RUsage", "max_rss",
    # Policies
    "StoppingPolicy", "PolicyState",
    "FixedRuns", "CoefficientOfVariation", "MaxDuration",
    # Benchmark / Suite
    "Benchmark", "BenchmarkBuilder", "bench",
    "default_label", "from_files",
    "Suite", "suite",
    "Context",
    # Runners
    "Runner", "Sequential", "Parallel", "Dry",
    "SuiteMaterializationError",
    "HarnessMonitor", "HarnessHandle", "line_monitor",
    # Reporters
    "Reporter", "CompositeReporter", "CsvReporter", "JsonReporter", "DirReporter", "SummaryReporter", "ProgressReporter",
    # Formatters
    "Formatter", "DefaultSummary", "Compact",
    # CLI
    "Bench", "run", "main",
]
