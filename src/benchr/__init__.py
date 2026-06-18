"""benchr — a benchmarking framework."""

# Atoms
from benchr.core.execution import (
    Execution,
    ExecutionResult,
    ScheduledExecution,
    Variant,
    Verdict,
    default_success,
)
from benchr.core.sample import (
    Observation,
    Report,
    Run,
    Sample,
    report_from_json,
    report_to_json,
)

# Metrics
from benchr.core.metric import (
    FloatPerLine,
    Metric,
    Rebench,
    Regex,
    RUsage,
    Time,
    max_rss,
)

# Stopping policies
from benchr.core.policy import (
    CoefficientOfVariation,
    FixedRuns,
    PolicyState,
    StoppingPolicy,
)

# Benchmark / Suite
from benchr.grammar.benchmark import (
    Benchmark,
    bench,
    default_label,
    from_files,
)
from benchr.grammar.suite import Suite, suite
from benchr.grammar.context import Context

# Runners
from benchr.runner.base import (
    PlannedBenchmark,
    Runner,
    SuiteMaterializationError,
    plan,
)
from benchr.runner.dry import Dry
from benchr.runner.parallel import Parallel
from benchr.runner.sequential import Sequential
from benchr.runner.source import HarnessHandle, HarnessMonitor, line_monitor

# Reporters
from benchr.report.reporter import (
    CompositeReporter,
    CsvReporter,
    DirReporter,
    JsonReporter,
    ProgressReporter,
    Reporter,
    SummaryReporter,
)

# Formatters
from benchr.report.formatter import Compact, DefaultSummary, Formatter

# CLI
from benchr.cli import main, run

__all__ = [
    # Atoms
    "Execution", "ExecutionResult", "ScheduledExecution", "Variant", "Verdict",
    "default_success",
    "Sample", "Observation", "Run", "Report", "report_from_json", "report_to_json",
    # Metrics
    "Metric",
    "Time", "Regex", "FloatPerLine", "Rebench", "RUsage", "max_rss",
    # Policies
    "StoppingPolicy", "PolicyState",
    "FixedRuns", "CoefficientOfVariation",
    # Benchmark / Suite
    "Benchmark", "bench",
    "default_label", "from_files",
    "Suite", "suite",
    "Context",
    # Runners
    "Runner", "Sequential", "Parallel", "Dry", "plan", "PlannedBenchmark",
    "SuiteMaterializationError",
    "HarnessMonitor", "HarnessHandle", "line_monitor",
    # Reporters
    "Reporter", "CompositeReporter", "CsvReporter", "JsonReporter", "DirReporter", "SummaryReporter", "ProgressReporter",
    # Formatters
    "Formatter", "DefaultSummary", "Compact",
    # CLI
    "run", "main",
]
