"""benchr — a benchmarking framewrok."""

from pathlib import Path

# Atoms
from benchr.grammar.execution import (
    Execution,
    ExecutionResult,
    Phase,
    ScheduledExecution,
    Variant,
    Verdict,
)
from benchr.report.sample import (
    Report,
    RunRecord,
    Sample,
    report_from_json,
    report_to_json,
)

# Metrics
from benchr.grammar.metric import (
    Constant,
    FloatPerLine,
    Metric,
    Rebench,
    Regex,
    RUsage,
    Time,
    max_rss,
)

# Stopping policies
from benchr.grammar.policy import (
    CoefficientOfVariation,
    Custom,
    FixedRuns,
    PolicyState,
    StoppingPolicy,
)

# Benchmark / Suite
from benchr.grammar.benchmark import Benchmark, bench
from benchr.grammar.suite import Suite, suite

# Runners
from benchr.runner.base import Runner
from benchr.runner.dry import Dry
from benchr.runner.parallel import Parallel
from benchr.runner.sequential import Sequential

# Reporters
from benchr.report.reporter import (
    CompositeReporter,
    Csv,
    Dir,
    Json,
    Progress,
    Reporter,
    Summary,
)

# Formatters
from benchr.report.formatter import Compact, DefaultSummary, Formatter

# CLI
from benchr.cli import main, run

# Aliases
B = bench

__all__ = [
    "Path",
    # Atoms
    "Execution", "ExecutionResult", "ScheduledExecution", "Phase", "Variant", "Verdict",
    "Sample", "RunRecord", "Report", "report_from_json", "report_to_json",
    # Metrics
    "Metric", "Time", "Regex", "FloatPerLine", "Rebench", "RUsage", "Constant", "max_rss",
    # Policies
    "StoppingPolicy", "PolicyState",
    "FixedRuns", "CoefficientOfVariation", "Custom",
    # Benchmark / Suite
    "Benchmark", "bench", "B",
    "Suite", "suite",
    # Runners
    "Runner", "Sequential", "Parallel", "Dry",
    # Reporters
    "Reporter", "CompositeReporter", "Csv", "Json", "Dir", "Summary", "Progress",
    # Formatters
    "Formatter", "DefaultSummary", "Compact",
    # CLI
    "run", "main",
]
