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
    Report,
    RunRecord,
    RunResult,
    Sample,
    report_from_json,
    report_to_json,
)

# Metrics
from benchr.core.metric import (
    Constant,
    FloatPerLine,
    Metric,
    ProcessMetric,
    Rebench,
    Regex,
    RunMetric,
    RUsage,
    Time,
    max_rss,
)

# Stopping policies
from benchr.core.policy import (
    CoefficientOfVariation,
    Custom,
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
    "Sample", "RunRecord", "RunResult", "Report", "report_from_json", "report_to_json",
    # Metrics
    "Metric", "RunMetric", "ProcessMetric",
    "Time", "Regex", "FloatPerLine", "Rebench", "RUsage", "Constant", "max_rss",
    # Policies
    "StoppingPolicy", "PolicyState",
    "FixedRuns", "CoefficientOfVariation", "Custom",
    # Benchmark / Suite
    "Benchmark", "bench",
    "default_label", "from_files",
    "Suite", "suite",
    "Context",
    # Runners
    "Runner", "Sequential", "Parallel", "Dry", "plan", "PlannedBenchmark",
    "SuiteMaterializationError",
    # Reporters
    "Reporter", "CompositeReporter", "CsvReporter", "JsonReporter", "DirReporter", "SummaryReporter", "ProgressReporter",
    # Formatters
    "Formatter", "DefaultSummary", "Compact",
    # CLI
    "run", "main",
]
